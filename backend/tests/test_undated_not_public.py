"""Undated activities never feed anything PUBLIC (product rule 2026-11).

An activity with ``activity_date IS NULL`` cannot be shown to have been
ridden — in practice it is a planned itinerary (Strava / Komoot / … route GPX
uploaded as a file). It stays in the owner's private "Ma heatmap"
(/me/activities), untouched, but is excluded from the public heatmap build,
the public stats and the "already contributed" count — via the ONE shared
predicate ``provenance.community_eligible_conditions``.

Non-regression tests driving the REAL code: ingest, the raw-trace stream the
PMTiles / raster / export builds read, ``compute_community_stats``, the
/me endpoints, the archive drain's per-member ingest (incl. the
activities.csv date fallback) and the end-of-import email.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs import ingest_pending_archives as drain_mod
from app.jobs.build_pmtiles import compute_community_stats
from app.services import archive_intake
from app.services import email as email_service
from app.services import gpx as gpx_service
from app.services import ingest as ingest_service
from app.services.provenance import community_eligible_conditions, is_community_eligible
from app.services.raw_trace_display import iter_masked_runs

# Isolated empty longitude (mid-Atlantic) so a dev corpus can't interfere.
_LON = -36.0
_LAT = 43.61
_STEP = 10.0 / 110_574  # ~10 m
_BBOX = (_LON - 0.5, _LAT - 0.5, _LON + 0.5, _LAT + 0.5)
_DATE = datetime(2024, 3, 2, 7, 30, tzinfo=UTC)


def _line(lon: float, n: int = 200) -> str:
    return json.dumps({"type": "LineString", "coordinates": [[lon, _LAT + i * _STEP] for i in range(n)]})


def _register(client) -> tuple[str, dict]:
    resp = client.post(
        "/auth/register",
        json={"email": f"undated_{uuid.uuid4().hex[:8]}@example.com", "password": "testpass123",
              "username": f"und_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["user_id"], {"Authorization": f"Bearer {body['access_token']}"}


def _ingest(user_id: str, lon: float, activity_date) -> str:
    res = ingest_service.ingest_activity(
        user_id=user_id,
        activity_data={
            "provider": "file", "provider_activity_id": None, "source": "manual_upload",
            "sport": "gravel", "name": "t", "geometry_geojson": _line(lon),
            "distance_m": 2000.0, "file_hash": uuid.uuid4().hex, "activity_date": activity_date,
        },
        contribute_heatmap=True,
        skip_heat_computation=True,
    )
    assert res["status"] == "created", res
    return res["activity_id"]


def _delete_user_rows(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activity_cells WHERE user_id = :u"), {"u": user_id})
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": user_id})
        db.commit()
    finally:
        db.close()


def test_predicate_requires_a_date():
    conds, _params = community_eligible_conditions()
    assert "activity_date IS NOT NULL" in conds
    common = {"source": "manual_upload", "contribute_heatmap": True, "has_geometry": True}
    assert is_community_eligible(**common, activity_date=_DATE) is True
    assert is_community_eligible(**common, activity_date=None) is False


def test_undated_activity_is_private_only(client, monkeypatch):
    """Dated → public build + stats + contribution count; undated → none of
    them, but still served by "Ma heatmap" (/me/activities)."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    user_id, headers = _register(client)
    db = SessionLocal()
    try:
        base = compute_community_stats(db, min_uc=1)
        dated_id = _ingest(user_id, _LON, _DATE)
        undated_id = _ingest(user_id, _LON + 0.05, None)

        # Public raw-trace stream (PMTiles heat_points/trails, raster pyramid,
        # community export all read it).
        ids = {str(aid) for aid, _u, _s, _run in iter_masked_runs(db, bbox=_BBOX)}
        assert dated_id in ids
        assert undated_id not in ids

        # Public stats banner: exactly ONE new trace.
        after = compute_community_stats(db, min_uc=1)
        assert after["traces"] == base["traces"] + 1

        # "Already contributed" count only counts what is public.
        status = client.get("/me/contribution-status", headers=headers).json()
        assert status["count"] == 1

        # Private "Ma heatmap": BOTH rows, the undated one untouched.
        me = client.get("/me/activities?limit=100", headers=headers).json()
        by_id = {f["properties"]["id"]: f for f in me["features"]}
        assert set(by_id) == {dated_id, undated_id}
        # The feed's activity_date falls back to created_at (display/sort);
        # ``undated`` carries the truth for the /map card.
        assert by_id[undated_id]["properties"]["undated"] is True
        assert by_id[dated_id]["properties"]["undated"] is False
        db.expire_all()
        assert db.execute(sa_text("SELECT activity_date FROM activities WHERE id = :i"),
                          {"i": undated_id}).scalar() is None
    finally:
        db.close()
        _delete_user_rows(user_id)


# ── Date at ingest: activities.csv fallback ────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mar 2, 2024, 7:30:00 AM", _DATE),
        ("Mar 2, 2024, 7:30:00 PM", _DATE.replace(hour=19)),
        ("2 mars 2024, 07:30:00", _DATE),
        ("2 févr. 2024, 07:30:00", _DATE.replace(month=2)),
        ("2024-03-02T07:30:00Z", _DATE),
        ("", None),
        ("pas une date", None),
        ("Mar 2, 1970, 7:30:00 AM", None),  # implausible year
    ],
)
def test_parse_strava_csv_date(raw, expected):
    assert gpx_service.parse_strava_csv_date(raw) == expected


def _untimed_gpx(lon: float) -> bytes:
    pts = "".join(
        f'<trkpt lat="{_LAT + i * _STEP:.6f}" lon="{lon:.6f}"><ele>100</ele></trkpt>'
        for i in range(50)
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="t"><trk><name>x</name>'
        f"<trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


def test_csv_activity_dates_maps_member_names():
    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,\"Mar 2, 2024, 7:30:00 AM\",Ride,Ride,activities/1.gpx\n"
        "2,,Ride,Ride,activities/2.gpx\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("export/activities.csv", csv)
        zf.writestr("export/activities/1.gpx", _untimed_gpx(_LON))
        zf.writestr("export/activities/2.gpx", _untimed_gpx(_LON + 0.01))
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        dates = archive_intake.csv_activity_dates(zf)
        names = [n for n, _raw, _cs in archive_intake.iter_zip_members(zf)]
    assert dates == {"export/activities/1.gpx": _DATE}
    assert set(dates) <= set(names)  # keyed like the drain iterates


def test_drain_member_uses_csv_date_and_flags_undated():
    """A CSV-listed file without timestamps is dated from the CSV; with no
    date anywhere the member is still imported (private) and flagged."""
    uid = f"undated-drain-{uuid.uuid4().hex[:8]}"
    try:
        common = {"user_id": uid, "resolved_sport": "road", "contribute_heatmap": True,
                  "source": "manual_upload", "skip_heat_computation": True}
        o1, aid1, note1 = drain_mod._ingest_member_bytes(
            filename="activities/1.gpx", raw=_untimed_gpx(_LON), fallback_date=_DATE, **common)
        o2, aid2, note2 = drain_mod._ingest_member_bytes(
            filename="routes/2.gpx", raw=_untimed_gpx(_LON + 0.02), **common)
        assert (o1, note1) == ("imported", None)
        assert (o2, note2) == ("imported", drain_mod.UNDATED)
        db = SessionLocal()
        try:
            got = {str(i): d for i, d in db.execute(sa_text(
                "SELECT id, activity_date FROM activities WHERE user_id = :u"), {"u": uid}).all()}
        finally:
            db.close()
        assert got[aid1] == _DATE
        assert got[aid2] is None
    finally:
        _delete_user_rows(uid)


# ── End-of-import email ────────────────────────────────────────────────────


def test_done_email_reports_undated_traces_fr():
    _s, html = email_service.render_archive_done_email(
        imported=5, skipped=0, failed=0, imported_undated=2, locale="fr")
    assert ("<strong>2</strong> traces sans date (itinéraires planifiés ?) : visibles "
            "dans Ma heatmap uniquement, pas sur la carte publique") in html
    assert "la heatmap communautaire les intégrera" in html  # 3 dated ones do


def test_done_email_reports_undated_traces_en():
    _s, html = email_service.render_archive_done_email(
        imported=1, skipped=0, failed=0, imported_undated=1, locale="en")
    assert "<strong>1</strong> with no date (planned routes?)" in html
    # Nothing dated → no promise about the community heatmap.
    assert "community heatmap will include them" not in html


def test_done_email_no_undated_line_by_default():
    _s, html = email_service.render_archive_done_email(imported=3, skipped=0, failed=0)
    assert "sans date" not in html
