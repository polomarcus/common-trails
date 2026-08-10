"""GOLDEN — the archive contribution loop, upload → drain → heat_edges.

The prod contribution path is:

    user uploads a Strava-export .zip
      → POST /imports/strava-archive/init          (records consent, awaiting_upload)
      → PUT  <signed/local upload url>              (the archive bytes land in storage)
      → POST /imports/strava-archive/complete       (flips the row to 'uploaded')
      → the DRAIN job app.jobs.ingest_pending_archives.drain_pending_archives
          (streams the .zip, parses + ingests each member) → heat_edges / heat_edges_agg

The sibling ``test_strava_archive_consent_import.py`` drives every step of this
EXCEPT the final one that matters most for the MVP promise: it always drains
with ``skip_heat_computation=True``, so the drain → HEATMAP link
(heat_edges + heat_edges_agg actually gaining rows for the contributed
geometry) has ZERO coverage. This test closes that gap: it runs the REAL drain
with heat computation ON and asserts the loop reaches the community heat layer.

What is asserted (all REAL code, no inline mirrors):
  * the mtb + road members are ingested, tagged source="manual_upload" (#453
    provenance), with the activities.csv sport (mtb / road);
  * the out-of-scope Yoga member is SKIPPED (skip-beats-pollute) — never ingested;
  * heat_edge_contributors gains rows for THIS user's hash — the drain → heat_edges
    link (holds via grid-fallback even where OSM is sparse);
  * heat_edges_agg is INCREMENTALLY maintained: every OSM way the contribution
    touched has a fresh agg row (updated_at bumped during this drain) — the
    drain → heat_edges_agg link (needs the OSM substrate, hence @golden).

Runs for real under ``make test-golden`` (needs the OSM PBF in osm_road_edges);
SKIPs in CI where there is no substrate.
"""
import io
import json
import os
import zipfile

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.services.gpx import parse_gpx
from tests.conftest import osm_present_in_bbox

_GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "herault_gravel_golden.gpx")

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not (osm_present_in_bbox() and os.path.exists(_GOLDEN)),
        reason="needs the occitanie OSM PBF in osm_road_edges + the golden fixture; CI has neither.",
    ),
]


def _golden_coords() -> list:
    with open(_GOLDEN, "rb") as fh:
        return json.loads(parse_gpx(fh.read())["geometry_geojson"])["coordinates"]


def _gpx_from_coords(name: str, coords: list) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for lon, lat, *rest in coords:
        ele = rest[0] if rest else None
        seg.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon, elevation=ele))
    return gpx.to_xml()


def _tiny_gpx(name: str, lat0: float, lon0: float, n: int = 6) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.0005, longitude=lon0 + i * 0.0005, elevation=100 + i))
    return gpx.to_xml()


def _archive_zip() -> bytes:
    """A Strava-export-shaped ZIP built from the golden (map-matching) trace.

    Two DISTINCT in-scope members (mtb = the golden coords, road = the same
    trace shifted ~40 m east so its file_hash differs and it is not deduped),
    plus one out-of-scope Yoga member that must be skipped.
    """
    coords = _golden_coords()
    # ~40 m east shift (France latitude): distinct geometry → distinct file_hash,
    # still inside the dense Hérault OSM coverage so it map-matches too.
    dlon = 40.0 / (111_320.0 * 0.7071)
    road_coords = [[lon + dlon, lat, *rest] for lon, lat, *rest in coords]

    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,2024-01-01,VTT session,MountainBikeRide,activities/mtb1.gpx\n"
        "2,2024-01-02,Morning spin,Ride,activities/road1.gpx\n"
        "3,2024-01-03,Zen studio,Yoga,activities/yoga1.gpx\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/mtb1.gpx", _gpx_from_coords("VTT session", coords))
        zf.writestr("activities/road1.gpx", _gpx_from_coords("Morning spin", road_coords))
        zf.writestr("activities/yoga1.gpx", _tiny_gpx("Zen studio", 43.60, 3.88))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    """Truncate the archive-intake queue tables before each test (the drain
    claims pending rows GLOBALLY, so a sibling test's leftovers must not leak)."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(sa_text(
            "TRUNCATE pending_archive_files, pending_archives, contribution_consents"))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _local_intake_dir(tmp_path, monkeypatch):
    """Force the local (filesystem) storage backend under a temp dir."""
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _user_id(client, auth_headers) -> str:
    return client.get("/auth/me", headers=auth_headers).json()["user_id"]


def _user_id_hash(user_id: str) -> int:
    import hashlib
    return int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16)


def _init(client, auth_headers) -> dict:
    resp = client.post(
        "/imports/strava-archive/init",
        headers=auth_headers,
        json={
            "sport": "road",  # form fallback — the CSV per-member sport should win
            "consent": True,
            "consent_version": "strava-archive-2026-07-v1",
            "consent_text": "Je consens à contribuer mes traces (ODbL).",
            "locale": "fr",
            "filename": "export.zip",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_archive_upload_drains_to_heat_edges_and_agg(client, auth_headers):
    from app.db.models import Activity, PendingArchive
    from app.db.session import SessionLocal
    from app.jobs.ingest_pending_archives import drain_pending_archives

    uid = _user_id(client, auth_headers)
    uid_hash = _user_id_hash(uid)

    # 1) REAL init → PUT → complete (no hand-rolled insert — catches drift).
    body = _init(client, auth_headers)
    assert body["storage_backend"] == "local"
    put = client.put(body["upload_url"], headers=auth_headers, content=_archive_zip())
    assert put.status_code == 200, put.text
    comp = client.post(
        "/imports/strava-archive/complete",
        headers=auth_headers,
        json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
    )
    assert comp.status_code == 202, comp.text
    assert comp.json()["status"] == "uploaded"

    # Baseline for the freshness proof: the drain's recompute stamps
    # heat_edges_agg.updated_at = now(); capture the DB clock BEFORE draining.
    db = SessionLocal()
    try:
        t0 = db.execute(sa_text("SELECT now()")).scalar()
        contribs_before = db.execute(sa_text(
            "SELECT count(*) FROM heat_edge_contributors WHERE user_id_hash = :h"
        ), {"h": uid_hash}).scalar()
    finally:
        db.close()
    assert contribs_before == 0, "fresh user must start with no heat contributions"

    # 2) Run the REAL drain the Cloud Run job runs — heat computation ON.
    summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=False)
    assert summary["archives"] == 1, summary
    assert summary["imported"] == 2, summary        # mtb + road ingested
    assert summary["skipped"] >= 1, summary          # yoga skipped, never ingested
    assert summary["failed"] == 0, summary

    db = SessionLocal()
    try:
        # 3a) PROVENANCE — both members ingested, source="manual_upload", CSV sport.
        acts = db.query(Activity).filter(Activity.user_id == uid).all()
        assert len(acts) == 2, [(a.sport, a.source) for a in acts]
        assert {a.source for a in acts} == {"manual_upload"}
        assert sorted(a.sport for a in acts) == ["mtb", "road"]

        arch = db.get(PendingArchive, body["archive_id"])
        assert arch.status == "done"
        assert arch.imported == 2

        # 3b) DRAIN → heat_edges link: the contribution produced heat_edge
        # contributor rows for THIS user (holds via grid-fallback even where
        # OSM is sparse).
        contribs_after = db.execute(sa_text(
            "SELECT count(*) FROM heat_edge_contributors WHERE user_id_hash = :h"
        ), {"h": uid_hash}).scalar()
        assert contribs_after > 0, "drain must write heat_edge_contributors for the contribution"

        # 3c) DRAIN → heat_edges_agg link: every OSM way the contribution
        # touched (spatial match on the golden trace) must have a FRESH agg row
        # (updated_at bumped during this drain by the incremental recompute).
        touched = db.execute(sa_text("""
            SELECT DISTINCT he.osm_way_id, he.sport
            FROM heat_edges he
            JOIN heat_edge_contributors hec ON hec.edge_key = he.edge_key
            WHERE hec.user_id_hash = :h AND he.osm_way_id IS NOT NULL
        """), {"h": uid_hash}).all()
        assert touched, (
            "expected the golden trace to map-match at least one OSM way; got only "
            "grid-fallback — the heat_edges_agg link could not be exercised."
        )
        for way_id, sport in touched:
            row = db.execute(sa_text("""
                SELECT updated_at FROM heat_edges_agg
                WHERE osm_way_id = :w AND sport = :s
            """), {"w": way_id, "s": sport}).first()
            assert row is not None, f"touched way {way_id}/{sport} missing from heat_edges_agg"
            assert row[0] >= t0, (
                f"heat_edges_agg row {way_id}/{sport} was not refreshed by the drain "
                f"(updated_at {row[0]} < drain start {t0})"
            )
    finally:
        db.close()
