"""Impossible activity dates are stored undated, never published as-is.

Seen in prod (2026-11): a ride dated 2030 — a device clock gone wrong. The
FIT parser does not check dates and the GPX parser lets next year through.
The guard lives in ``ingest.ingest_activity`` (every import path) and in the
archive drain, where the activities.csv date takes over from a broken file
date. Dates in the DB tests are relative to now, so they never expire.
"""

import uuid
from datetime import UTC, datetime, timedelta

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs import ingest_pending_archives as drain
from app.services import ingest as ingest_service
from app.services.ingest import plausible_activity_date

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (datetime(2030, 5, 1, 8, 0, tzinfo=UTC), None),  # the prod case
        (NOW + timedelta(days=2), None),
        (NOW + timedelta(hours=20), NOW + timedelta(hours=20)),  # time-zone slop is fine
        (datetime(1989, 12, 31), None),  # FIT epoch, naive
        (datetime(1999, 8, 22, tzinfo=UTC), None),  # GPS week rollover
        (datetime(2000, 1, 1, tzinfo=UTC), datetime(2000, 1, 1, tzinfo=UTC)),
        (datetime(2024, 8, 15, 10, 30), datetime(2024, 8, 15, 10, 30)),  # naive kept as given
        ("2024-08-15T10:30:45Z", datetime(2024, 8, 15, 10, 30, 45, tzinfo=UTC)),  # Strava ISO
        ("2031-01-01T00:00:00Z", None),
        ("not a date", None),
        (12345, None),
    ],
)
def test_plausible_activity_date(value, expected):
    assert plausible_activity_date(value, now=NOW) == expected


def _gpx(when: datetime, lon=3.71, lat=43.47, n=8) -> bytes:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name="ride")
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat + i * 0.001, longitude=lon + i * 0.001, time=when + timedelta(seconds=10 * i)
            )
        )
    return gpx.to_xml().encode()


@pytest.fixture
def uid():
    u = str(uuid.uuid4())
    yield u
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": u})
        db.commit()
    finally:
        db.close()


def _stored_date(activity_id):
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT activity_date FROM activities WHERE id = :id"), {"id": activity_id}
        ).scalar()
    finally:
        db.close()


def _ingest(uid, when, **extra):
    return ingest_service.ingest_activity(
        user_id=uid,
        activity_data={
            "provider": "file",
            "source": "manual_upload",
            "sport": "road",
            "name": "ride",
            "geometry_geojson": '{"type":"LineString","coordinates":[[3.71,43.47],[3.72,43.48]]}',
            "distance_m": 1400.0 + uuid.uuid4().int % 1000,
            "file_hash": uuid.uuid4().hex,
            "activity_date": when,
            **extra,
        },
        contribute_heatmap=True,
    )


def test_ingest_stores_a_future_date_undated(uid):
    res = _ingest(uid, datetime.now(UTC) + timedelta(days=400))
    assert res["status"] == "created"
    assert _stored_date(res["activity_id"]) is None


def test_ingest_stores_the_fit_epoch_undated(uid):
    res = _ingest(uid, datetime(1989, 12, 31, 0, 0))
    assert _stored_date(res["activity_id"]) is None


def test_ingest_keeps_a_real_date(uid):
    when = datetime(2025, 6, 1, 8, 0, tzinfo=UTC)
    res = _ingest(uid, when)
    assert _stored_date(res["activity_id"]) == when


def test_ingest_parses_strava_iso_strings(uid):
    res = _ingest(uid, "2025-06-02T08:00:00Z")
    assert _stored_date(res["activity_id"]) == datetime(2025, 6, 2, 8, 0, tzinfo=UTC)


def _soon() -> datetime:
    """A date the GPX parser accepts (≤ next calendar year) but the guard
    rejects (> now + 1 day)."""
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)


def test_archive_gpx_with_a_broken_clock_takes_the_csv_date(uid):
    """A GPX dated a few days ahead inside a Strava export: the activities.csv
    date (the rider's real date) takes over instead of the impossible one."""
    csv_date = datetime(2024, 9, 1, 7, 30, tzinfo=UTC)
    out, aid, note = drain._ingest_member_bytes(
        user_id=uid,
        filename="activities/broken-clock.gpx",
        raw=_gpx(_soon()),
        resolved_sport="road",
        contribute_heatmap=False,
        source="manual_upload",
        fallback_date=csv_date,
    )
    assert out == "imported" and note is None
    assert _stored_date(aid) == csv_date


def test_archive_fit_with_a_broken_clock_takes_the_csv_date(uid, monkeypatch):
    """The prod case: a FIT (naive datetime, no date check in the FIT parser)
    dated years ahead of the rider's real CSV date."""
    csv_date = datetime(2024, 9, 2, 7, 30, tzinfo=UTC)
    real_parse = drain._parse_member
    broken = datetime.now() + timedelta(days=4 * 365)  # naive, like fitparse

    def parse_like_a_broken_fit(raw, filename):
        parsed = real_parse(raw, "member.gpx")
        parsed["activity_date"] = broken
        parsed["start_time"] = broken
        return parsed

    monkeypatch.setattr(drain, "_parse_member", parse_like_a_broken_fit)
    out, aid, note = drain._ingest_member_bytes(
        user_id=uid,
        filename="activities/broken-clock.fit",
        raw=_gpx(datetime(2024, 1, 1, tzinfo=UTC), lon=3.9),
        resolved_sport="road",
        contribute_heatmap=False,
        source="manual_upload",
        fallback_date=csv_date,
    )
    assert out == "imported" and note is None
    assert _stored_date(aid) == csv_date


def test_archive_member_with_a_broken_clock_and_no_csv_is_undated(uid):
    out, aid, note = drain._ingest_member_bytes(
        user_id=uid,
        filename="activities/broken-clock-2.gpx",
        raw=_gpx(_soon(), lon=3.75),
        resolved_sport="road",
        contribute_heatmap=False,
        source="manual_upload",
    )
    assert out == "imported" and note == drain.UNDATED
    assert _stored_date(aid) is None
