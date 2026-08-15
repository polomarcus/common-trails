"""GOLDEN (raw-mode) — the archive drain in the config PROD ACTUALLY RUNS.

Prod is ``HEATMAP_DISPLAY_SOURCE=raw``: the community map renders precise GPS
traces straight from ``activities``, so the drain does NO OSM map-matching and
writes NO ``heat_edges`` (``ingest.py`` gates ``_update_heat_edges`` on
``not raw_display_enabled()``). The whole contribution loop
— upload → drain → ``activities`` — is therefore testable with **zero OSM
substrate**, unlike the legacy matched golden (``test_archive_drain_golden.py``),
which needs the occitanie PBF in ``osm_road_edges`` and so SKIPs everywhere the
substrate is absent (CI, and any dev box that hasn't run the ~30-min import).

This runs the REAL ``init → PUT → complete → drain_pending_archives`` flow in raw
mode and pins the drain contract that **PR B (batch ingest) must preserve**:

  * every in-scope member → exactly one ``activities`` row, ``source=manual_upload``,
    the per-member CSV sport;
  * a byte-identical duplicate member is DEDUPED (one row, not two);
  * an out-of-scope (Yoga) member is SKIPPED, never ingested;
  * NO ``heat_edges`` are written (raw mode) — the matched path stays dark;
  * throughput (``slow``): a 500-member archive drains at a sane per-member cost.

No ``@pytest.mark.golden`` gate: raw mode needs no substrate, so this runs in the
normal suite AND in CI — real coverage for the drain PR B changes, which the
matched golden could never give (always skipped without the PBF).
"""
import io
import time
import zipfile

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text


@pytest.fixture(autouse=True)
def _raw_mode(monkeypatch):
    """Drive the drain through the raw path prod runs (no OSM, no heat_edges).
    ``raw_display_enabled()`` reads the env live, so this flips the real gate."""
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    """The drain claims pending rows GLOBALLY — a sibling test's leftovers must
    not leak into this one's counts."""
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


def _gpx(name: str, lat0: float, lon0: float, day: int, n: int = 6) -> str:
    """A small, valid GPX. Distinct ``day`` + coords per member → distinct
    file_hash AND distinct (date, distance) so neither dedup vector false-fires."""
    g = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    g.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    from datetime import UTC, datetime, timedelta
    t0 = datetime(2024, 1, 1, 6, 0, 0, tzinfo=UTC) + timedelta(days=day)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.0006, longitude=lon0 + i * 0.0006,
            elevation=100 + i, time=t0 + timedelta(minutes=i)))
    return g.to_xml()


def _archive(members: list[tuple[str, str]], csv_rows: list[str]) -> bytes:
    """members: (path, gpx_xml). csv_rows: activities.csv data lines."""
    csv = ("Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
           + "\n".join(csv_rows) + "\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        for path, xml in members:
            zf.writestr(path, xml)
    return buf.getvalue()


def _user_id(client, auth_headers) -> str:
    return client.get("/auth/me", headers=auth_headers).json()["user_id"]


def _user_id_hash(user_id: str) -> int:
    import hashlib
    return int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16)


def _upload_and_complete(client, auth_headers, zip_bytes: bytes) -> str:
    resp = client.post(
        "/imports/strava-archive/init",
        headers=auth_headers,
        json={
            "sport": "road",  # form fallback — CSV per-member sport should win
            "consent": True,
            "consent_version": "strava-archive-2026-07-v1",
            "consent_text": "Je consens à contribuer mes traces (ODbL).",
            "locale": "fr",
            "filename": "export.zip",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["storage_backend"] == "local"
    put = client.put(body["upload_url"], headers=auth_headers, content=zip_bytes)
    assert put.status_code == 200, put.text
    comp = client.post(
        "/imports/strava-archive/complete",
        headers=auth_headers,
        json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
    )
    assert comp.status_code == 202, comp.text
    return body["archive_id"]


def test_raw_drain_writes_activities_dedupes_and_skips_no_osm(client, auth_headers):
    """Correctness: in-scope members land once (dedup, provenance, sport), the
    Yoga member is skipped, and NO heat_edges are written (raw mode)."""
    from app.db.models import Activity, PendingArchive
    from app.db.session import SessionLocal
    from app.jobs.ingest_pending_archives import drain_pending_archives

    uid = _user_id(client, auth_headers)
    uid_hash = _user_id_hash(uid)

    # 10 distinct in-scope rides (5 mtb + 5 road), + 1 byte-identical duplicate
    # of mtb_0 (must dedupe), + 1 out-of-scope Yoga (must skip).
    members: list[tuple[str, str]] = []
    csv_rows: list[str] = []
    idx = 1
    for i in range(5):
        xml = _gpx(f"VTT {i}", 43.60 + i * 0.02, 3.80 + i * 0.02, day=i)
        members.append((f"activities/mtb_{i}.gpx", xml))
        csv_rows.append(f"{idx},2024-01-0{(i % 9) + 1},VTT {i},MountainBikeRide,activities/mtb_{i}.gpx")
        idx += 1
    for i in range(5):
        xml = _gpx(f"Route {i}", 43.90 + i * 0.02, 4.00 + i * 0.02, day=20 + i)
        members.append((f"activities/road_{i}.gpx", xml))
        csv_rows.append(f"{idx},2024-02-0{(i % 9) + 1},Route {i},Ride,activities/road_{i}.gpx")
        idx += 1
    # Duplicate: same bytes as mtb_0 under a different path (Strava sometimes
    # ships the same file twice). Distinct name → still same file_hash.
    dup_xml = members[0][1]
    members.append(("activities/mtb_0_copy.gpx", dup_xml))
    csv_rows.append(f"{idx},2024-01-01,VTT 0 again,MountainBikeRide,activities/mtb_0_copy.gpx")
    idx += 1
    # Out-of-scope Yoga.
    yoga = _gpx("Zen", 43.61, 3.88, day=99)
    members.append(("activities/yoga.gpx", yoga))
    csv_rows.append(f"{idx},2024-03-01,Zen,Yoga,activities/yoga.gpx")

    zip_bytes = _archive(members, csv_rows)
    archive_id = _upload_and_complete(client, auth_headers, zip_bytes)

    summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=False)

    assert summary["archives"] == 1, summary
    assert summary["imported"] == 10, summary   # 5 mtb + 5 road, dup NOT counted
    assert summary["skipped"] >= 2, summary       # yoga (out-of-scope) + the duplicate
    assert summary["failed"] == 0, summary

    db = SessionLocal()
    try:
        acts = db.query(Activity).filter(Activity.user_id == uid).all()
        assert len(acts) == 10, [(a.sport, a.source) for a in acts]  # dedup held
        assert {a.source for a in acts} == {"manual_upload"}         # provenance
        assert sorted(a.sport for a in acts) == ["mtb"] * 5 + ["road"] * 5
        arch = db.get(PendingArchive, archive_id)
        assert arch.status == "done" and arch.imported == 10

        # Raw mode: the matched path stayed dark — no heat_edges for this user.
        contribs = db.execute(sa_text(
            "SELECT count(*) FROM heat_edge_contributors WHERE user_id_hash = :h"
        ), {"h": uid_hash}).scalar()
        assert contribs == 0, "raw mode must write NO heat_edges (matched path is dead)"
    finally:
        db.close()


@pytest.mark.slow
def test_raw_drain_500_members_throughput(client, auth_headers):
    """Throughput: a 500-member archive drains in raw mode at a sane per-member
    cost (the shape PR B optimizes). Pins that a big archive completes and every
    member lands — the runtime bound is generous (CI is shared/slow) and exists
    only to catch an accidental per-member blow-up (e.g. an N² dedup scan)."""
    from app.db.models import Activity
    from app.db.session import SessionLocal
    from app.jobs.ingest_pending_archives import drain_pending_archives

    uid = _user_id(client, auth_headers)
    N = 500
    members: list[tuple[str, str]] = []
    csv_rows: list[str] = []
    for i in range(N):
        # Spread coords over a wide grid + distinct day → all distinct.
        lat = 43.0 + (i % 100) * 0.01
        lon = 3.0 + (i // 100) * 0.01
        xml = _gpx(f"Ride {i}", lat, lon, day=i)
        members.append((f"activities/ride_{i}.gpx", xml))
        csv_rows.append(f"{i},2024-01-01,Ride {i},Ride,activities/ride_{i}.gpx")

    zip_bytes = _archive(members, csv_rows)
    _upload_and_complete(client, auth_headers, zip_bytes)

    t0 = time.monotonic()
    summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=False)
    elapsed = time.monotonic() - t0

    assert summary["imported"] == N, summary
    assert summary["failed"] == 0, summary
    per_member_ms = (elapsed / N) * 1000
    # Generous ceiling: raw ingest is a parse + dedup SELECT + INSERT (~10-20 ms
    # locally). 200 ms/member would mean something regressed badly.
    assert per_member_ms < 200, f"{per_member_ms:.1f} ms/member over {N} members ({elapsed:.1f}s)"

    db = SessionLocal()
    try:
        n_acts = db.query(Activity).filter(Activity.user_id == uid).count()
        assert n_acts == N, n_acts
    finally:
        db.close()
    print(f"\nraw drain: {N} members in {elapsed:.1f}s = {per_member_ms:.1f} ms/member")


@pytest.mark.slow
def test_raw_drain_adaptive_pacing_stays_fast(client, auth_headers, monkeypatch):
    """The DEFAULT (adaptive) pacing must NOT re-inflate the drain. With no
    DRAIN_PACE_SECONDS pinned, a 300-member raw drain self-tunes: sleep ≈ each
    member's ~ms ingest, so total time stays a small multiple of the pure-work
    time — NOT the minutes a fixed 0.25 s pace would add (300 × 0.25 = 75 s)."""
    from app.db.models import Activity
    from app.db.session import SessionLocal
    from app.jobs.ingest_pending_archives import drain_pending_archives

    monkeypatch.delenv("DRAIN_PACE_SECONDS", raising=False)  # force adaptive
    uid = _user_id(client, auth_headers)
    N = 300
    members, csv_rows = [], []
    for i in range(N):
        lat, lon = 43.0 + (i % 100) * 0.01, 3.0 + (i // 100) * 0.01
        members.append((f"activities/ride_{i}.gpx", _gpx(f"Ride {i}", lat, lon, day=i)))
        csv_rows.append(f"{i},2024-01-01,Ride {i},Ride,activities/ride_{i}.gpx")
    _upload_and_complete(client, auth_headers, _archive(members, csv_rows))

    t0 = time.monotonic()
    # pace_seconds=None → the resolver picks adaptive (the prod default path).
    summary = drain_pending_archives(limit=5, pace_seconds=None, skip_heat_computation=False)
    elapsed = time.monotonic() - t0

    assert summary["imported"] == N, summary
    # Fixed 0.25 s would add 300×0.25 = 75 s of pure sleep on top of the work.
    # Adaptive (ratio 1.0 ≈ 50 % duty) must stay well under that — a generous
    # 30 s ceiling still proves the pacing didn't re-inflate.
    assert elapsed < 30, f"adaptive drain took {elapsed:.1f}s for {N} members — pacing re-inflated?"

    db = SessionLocal()
    try:
        assert db.query(Activity).filter(Activity.user_id == uid).count() == N
    finally:
        db.close()
    print(f"\nadaptive raw drain: {N} members in {elapsed:.1f}s")
