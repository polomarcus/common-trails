"""Test cross-provider activity deduplication."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.services.ingest import ingest_activity

_USER = "dedup-test-user"
_GEOJSON = json.dumps({
    "type": "LineString",
    "coordinates": [[3.87, 43.61], [3.871, 43.611], [3.872, 43.612]],
})
_DATE = datetime(2025, 8, 4, 7, 10, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _cleanup():
    """Clean up test activities."""
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :uid"), {"uid": _USER})
        db.commit()
    finally:
        db.close()


class TestCrossProviderDedup:

    def test_same_provider_id_dedup(self):
        """Same provider + provider_activity_id → skip."""
        r1 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "123",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "123",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r2["status"] == "already_exists"

    def test_same_file_hash_dedup(self):
        """Same file_hash → skip."""
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "abc123",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "abc123",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r2["status"] == "already_exists"

    def test_cross_provider_date_distance_dedup(self):
        """GPX import then Strava resync → skip (same date, same distance)."""
        # First: GPX upload
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "gpx_hash_1",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        # Second: Strava resync (same date, similar distance, different provider)
        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "456",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5050,  # 1% difference — within 10% tolerance
            "activity_date": _DATE + timedelta(seconds=30),  # 30s offset — within 5min
        }, skip_heat_computation=True)
        assert r2["status"] == "already_exists"

    def test_cross_provider_different_date_no_dedup(self):
        """Different date → not a duplicate."""
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "gpx_hash_2",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "789",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000,
            "activity_date": _DATE + timedelta(days=1),  # next day
        }, skip_heat_computation=True)
        assert r2["status"] == "created"

    def test_strava_iso8601_string_activity_date_does_not_crash(self):
        """The resync path (`app/jobs/resync_strava.py`) passes Strava's
        `start_date_local` / `start_date` verbatim — these are ISO 8601
        STRINGS, not datetime objects. The cross-provider dedup added
        2026-05-27 (S2.6) does `activity_date - timedelta(minutes=5)`,
        which raises `TypeError: unsupported operand type(s) for -:
        'str' and 'datetime.timedelta'` for string inputs.

        First exercised against prod 2026-05-31 when the
        `common-trails-resync-strava-prod` Cloud Run Job ran for the
        first time — all 435 attempted ingests failed.

        Regression pin: ISO 8601 string MUST coerce to datetime, and a
        previously-imported activity with the same date/distance MUST
        still dedupe.
        """
        # Insert first via the GPX path (datetime activity_date).
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "iso_dedup_hash",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        # Strava resync — same activity arrives as an ISO 8601 string.
        # Two acceptable forms: with "Z" (UTC) or with "+00:00" offset.
        iso_with_z = _DATE.strftime("%Y-%m-%dT%H:%M:%SZ")
        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "iso-resync-1",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5050,
            "activity_date": iso_with_z,
        }, skip_heat_computation=True)
        # The bug: pre-fix this raised TypeError. Post-fix: dedup hits.
        assert r2["status"] == "already_exists", (
            f"ISO 8601 string activity_date must coerce + dedup; got {r2}"
        )

    def test_unparseable_activity_date_string_coerces_to_none(self):
        """A malformed activity_date string coerces to NULL: the dedup
        window is best-effort and skips when the parse fails, and the
        Activity row inserts with `activity_date=NULL` rather than
        crashing on Postgres' timestamp cast.
        """
        r1 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "iso-bad-1",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000,
            "activity_date": "not-a-date-at-all",
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        # Confirm the row landed with NULL activity_date.
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(
                sa_text("SELECT activity_date FROM activities WHERE id = :i"),
                {"i": r1["activity_id"]},
            ).fetchone()
            assert row is not None
            assert row[0] is None, f"expected NULL activity_date, got {row[0]}"
        finally:
            db.close()

    def test_cross_provider_different_distance_no_dedup(self):
        """Very different distance → not a duplicate."""
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "gpx_hash_3",
            "distance_m": 5000, "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "999",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 50000,  # 10x different
            "activity_date": _DATE,
        }, skip_heat_computation=True)
        assert r2["status"] == "created"


class TestCrossProviderDedupInBulkPath:
    """The bulk path (`ingest_activities_bulk`) was missing cross-provider
    dedup until the PR #341 follow-up bundle. These tests pin that fix
    so a future refactor that drops the in-memory `(date,distance)`
    pre-load fails visibly. Same semantics as the single-row path
    above, but exercised through the bulk entry point.
    """

    def test_gpx_then_strava_bulk_does_not_double_insert(self):
        """The reported trap: user uploads GPX, then connects Strava.
        Strava bulk-import picks up the same activity. The unique
        constraint `(user_id, provider, provider_activity_id)` doesn't
        catch this (different providers) → pre-PR this created a
        duplicate row. After the fix, the bulk path skips via the
        in-memory `(date ±5min, distance ±10%)` check."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal
        from app.services.ingest import ingest_activities_bulk

        # T0: GPX upload
        ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "file",
                "provider_activity_id": None,
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 5000,
                "activity_date": _DATE,
                "file_hash": "bulk_gpx_hash",
            }],
            contribute_heatmap=False,
        )

        # T1: Strava bulk import — same physical activity
        result = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "strava",
                "provider_activity_id": "bulk-456",
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 5030,  # 0.6% drift — within tolerance
                "activity_date": _DATE + timedelta(seconds=42),  # 42s — within 5min
            }],
            contribute_heatmap=False,
        )
        assert result["created"] == 0, "bulk path must dedupe GPX→Strava"
        assert result["failed"] == 0

        # DB sanity: exactly one row, not two.
        db = SessionLocal()
        try:
            count = db.execute(
                sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :uid"),
                {"uid": _USER},
            ).scalar()
            assert count == 1, f"expected 1 row after dedup, got {count}"
        finally:
            db.close()

    def test_strava_then_gpx_bulk_does_not_double_insert(self):
        """Reverse direction: Strava bulk first, then user drags the
        GPX from their Strava export. Same cross-provider check
        catches it."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal
        from app.services.ingest import ingest_activities_bulk

        ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "strava",
                "provider_activity_id": "rev-1",
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 8000,
                "activity_date": _DATE,
            }],
            contribute_heatmap=False,
        )

        result = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "file",
                "provider_activity_id": None,
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 8000,
                "activity_date": _DATE,
                "file_hash": "rev_gpx_hash",
            }],
            contribute_heatmap=False,
        )
        assert result["created"] == 0

        db = SessionLocal()
        try:
            count = db.execute(
                sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :uid"),
                {"uid": _USER},
            ).scalar()
            assert count == 1
        finally:
            db.close()

    def test_within_same_bulk_batch_dedupes(self):
        """Two copies of the same activity in a single bulk call (e.g.
        a malformed Strava response) must not both insert — the
        in-memory list grows as we add."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal
        from app.services.ingest import ingest_activities_bulk

        result = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[
                {
                    "provider": "strava",
                    "provider_activity_id": "batch-1",
                    "sport": "mtb",
                    "geometry_geojson": _GEOJSON,
                    "distance_m": 6000,
                    "activity_date": _DATE,
                },
                {
                    # Same date+distance, different provider_activity_id
                    # so the same-provider constraint doesn't catch it.
                    "provider": "strava",
                    "provider_activity_id": "batch-2",
                    "sport": "mtb",
                    "geometry_geojson": _GEOJSON,
                    "distance_m": 6000,
                    "activity_date": _DATE,
                },
            ],
            contribute_heatmap=False,
        )
        assert result["created"] == 1, "second copy must dedupe via in-memory tuple list"

        db = SessionLocal()
        try:
            count = db.execute(
                sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :uid"),
                {"uid": _USER},
            ).scalar()
            assert count == 1
        finally:
            db.close()

    def test_bulk_does_not_falsely_dedupe_different_days(self):
        """Two genuine rides on different days must NOT dedupe even
        if their distance happens to match exactly."""
        from app.services.ingest import ingest_activities_bulk

        result = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[
                {
                    "provider": "strava",
                    "provider_activity_id": "day1",
                    "sport": "mtb",
                    "geometry_geojson": _GEOJSON,
                    "distance_m": 10000,
                    "activity_date": _DATE,
                },
                {
                    "provider": "strava",
                    "provider_activity_id": "day2",
                    "sport": "mtb",
                    "geometry_geojson": _GEOJSON,
                    "distance_m": 10000,
                    "activity_date": _DATE + timedelta(days=1),
                },
            ],
            contribute_heatmap=False,
        )
        assert result["created"] == 2

    def test_naive_datetime_does_not_break_dedup(self):
        """F1 from the 2nd-pass review: a naive `activity_date` must
        be interpreted as UTC, not local TZ. Otherwise a Paris dev
        environment computes a different epoch from a UTC Cloud Run
        container and legit cross-provider duplicates leak through.
        """
        from datetime import datetime as _dt

        from app.services.ingest import ingest_activities_bulk

        naive_when = _dt(2026, 7, 1, 9, 0)  # NO tzinfo — was the bug class

        ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "file",
                "provider_activity_id": None,
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 7500,
                "activity_date": naive_when,
                "file_hash": "naive_test_hash",
            }],
            contribute_heatmap=False,
        )

        # Same wall-clock as UTC datetime — should dedupe.
        aware_when = _dt(2026, 7, 1, 9, 0, tzinfo=UTC)
        result = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "strava",
                "provider_activity_id": "naive-test",
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 7500,
                "activity_date": aware_when,
            }],
            contribute_heatmap=False,
        )
        assert result["created"] == 0, (
            "naive datetime must normalize to UTC so naive↔aware dedupes"
        )

    def test_advisory_lock_released_on_completion(self):
        """The wrapper takes `pg_try_advisory_lock`, must release on
        normal return. Otherwise the connection going back to the pool
        carries the lock and the next call for the same user blocks."""
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal
        from app.services.ingest import ingest_activities_bulk

        ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "file",
                "provider_activity_id": None,
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 1000,
                "activity_date": _DATE,
                "file_hash": "lock_test_1",
            }],
            contribute_heatmap=False,
        )

        # Same user, second call — must be able to acquire the lock
        # immediately (try-acquire returns true). If the prior call
        # leaked the lock, this would block ~15s before timing out.
        check_db = SessionLocal()
        try:
            got = check_db.execute(
                sa_text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": f"strava_ingest:{_USER}"},
            ).scalar()
            assert got is True, "advisory lock leaked across calls"
            # Clean up — release what we just acquired.
            check_db.execute(
                sa_text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": f"strava_ingest:{_USER}"},
            )
            check_db.commit()
        finally:
            check_db.close()

    def test_bucketed_dedup_still_catches_window_edges(self):
        """The bucketed lookup uses 5-min buckets and scans 3 adjacent
        slots. Verify the ±5-min window still catches edges that
        straddle bucket boundaries."""

        from app.services.ingest import ingest_activities_bulk

        # Choose a time that's just inside a bucket boundary.
        # Bucket size = 300s. Offset by 4min30s — same bucket as
        # initial; offset by 5min01s — adjacent bucket.
        ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "file",
                "provider_activity_id": None,
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 9000,
                "activity_date": _DATE,
                "file_hash": "bucket_test",
            }],
            contribute_heatmap=False,
        )

        # 4min30s later → still within 5-min window
        within = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "strava",
                "provider_activity_id": "bkt-1",
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 9000,
                "activity_date": _DATE + timedelta(seconds=270),
            }],
            contribute_heatmap=False,
        )
        assert within["created"] == 0

        # 6 min later → outside 5-min window
        outside = ingest_activities_bulk(
            user_id=_USER,
            activities_data=[{
                "provider": "strava",
                "provider_activity_id": "bkt-2",
                "sport": "mtb",
                "geometry_geojson": _GEOJSON,
                "distance_m": 9000,
                "activity_date": _DATE + timedelta(seconds=360),
            }],
            contribute_heatmap=False,
        )
        assert outside["created"] == 1
