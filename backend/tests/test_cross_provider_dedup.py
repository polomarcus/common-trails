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
        })
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "123",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000, "activity_date": _DATE,
        })
        assert r2["status"] == "already_exists"

    def test_same_file_hash_dedup(self):
        """Same file_hash → skip."""
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "abc123",
            "distance_m": 5000, "activity_date": _DATE,
        })
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "abc123",
            "distance_m": 5000, "activity_date": _DATE,
        })
        assert r2["status"] == "already_exists"

    def test_cross_provider_date_distance_dedup(self):
        """GPX import then Strava resync → skip (same date, same distance)."""
        # First: GPX upload
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "gpx_hash_1",
            "distance_m": 5000, "activity_date": _DATE,
        })
        assert r1["status"] == "created"

        # Second: Strava resync (same date, similar distance, different provider)
        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "456",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5050,  # 1% difference — within 10% tolerance
            "activity_date": _DATE + timedelta(seconds=30),  # 30s offset — within 5min
        })
        assert r2["status"] == "already_exists"

    def test_cross_provider_different_date_no_dedup(self):
        """Different date → not a duplicate."""
        r1 = ingest_activity(_USER, {
            "provider": "file", "sport": "mtb",
            "geometry_geojson": _GEOJSON, "file_hash": "gpx_hash_2",
            "distance_m": 5000, "activity_date": _DATE,
        })
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "789",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5000,
            "activity_date": _DATE + timedelta(days=1),  # next day
        })
        assert r2["status"] == "created"

    def test_strava_iso8601_string_activity_date_does_not_crash(self):
        """The former Strava API resync job passed Strava's
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
        })
        assert r1["status"] == "created"

        # Strava resync — same activity arrives as an ISO 8601 string.
        # Two acceptable forms: with "Z" (UTC) or with "+00:00" offset.
        iso_with_z = _DATE.strftime("%Y-%m-%dT%H:%M:%SZ")
        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "iso-resync-1",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 5050,
            "activity_date": iso_with_z,
        })
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
        })
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
        })
        assert r1["status"] == "created"

        r2 = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "999",
            "sport": "mtb", "geometry_geojson": _GEOJSON,
            "distance_m": 50000,  # 10x different
            "activity_date": _DATE,
        })
        assert r2["status"] == "created"
