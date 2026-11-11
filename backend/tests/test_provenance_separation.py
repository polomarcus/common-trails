"""PUBLIC ⟷ PERSONAL provenance separation (②).

Compliance (Strava 2026 API Policy §5.4/§5.10): Strava-API-sourced data may
NOT feed the public/community heatmap; only the user's own uploaded archive
(``source='manual_upload'``) may. The community build reads ``activities``
through ONE gate — ``provenance.community_eligible_conditions`` — so these
tests ingest through the REAL ``ingest_activity`` and check which stored rows
that gate lets through.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import ingest_activity
from app.services.provenance import (
    COMMUNITY_SOURCE,
    STRAVA_API_SOURCE,
    is_community_source,
    should_promote_to_community,
)

_LON0, _LAT0 = 7.0005, 4.0005
_USER = "prov-sep-user"


def _geo(n: int = 5, step: float = 0.0002) -> str:
    coords = [[round(_LON0 + i * step, 6), round(_LAT0 + i * step, 6)] for i in range(n)]
    return json.dumps({"type": "LineString", "coordinates": coords})


def _community_rows() -> int:
    """Rows of the test user the community build would read — the SSOT gate
    (``provenance.community_eligible_conditions``) the raw export filters on."""
    from app.services.provenance import community_eligible_conditions

    conds, params = community_eligible_conditions()
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :u AND " + " AND ".join(conds)),
            {**params, "u": _USER},
        ).scalar() or 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id LIKE 'prov-sep%'"))
        db.commit()
    finally:
        db.close()


class TestProvenanceUnit:
    def test_is_community_source(self):
        assert is_community_source(COMMUNITY_SOURCE) is True
        assert is_community_source(STRAVA_API_SOURCE) is False
        assert is_community_source(None) is False  # legacy → excluded
        assert is_community_source("garmin") is False

    def test_should_promote_decision_table(self):
        # manual_upload over a non-community twin → promote.
        assert should_promote_to_community(COMMUNITY_SOURCE, STRAVA_API_SOURCE) is True
        assert should_promote_to_community(COMMUNITY_SOURCE, None) is True
        # two community copies, or a strava sync over a community row → skip.
        assert should_promote_to_community(COMMUNITY_SOURCE, COMMUNITY_SOURCE) is False
        assert should_promote_to_community(STRAVA_API_SOURCE, COMMUNITY_SOURCE) is False
        assert should_promote_to_community(None, STRAVA_API_SOURCE) is False


class TestIngestGate:
    """What ``ingest_activity`` stores is community-eligible only when its
    provenance allows it."""

    def test_strava_api_does_not_feed_community(self):
        r = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "sep-strava-1",
            "source": STRAVA_API_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 900,
            "activity_date": datetime(2025, 3, 1, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert _community_rows() == 0, "strava_api activity must NOT be community-eligible"

    def test_manual_upload_feeds_community(self):
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-manual-1",
            "activity_date": datetime(2025, 3, 2, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert _community_rows() == 1, "manual_upload activity must be community-eligible"

    def test_legacy_null_source_does_not_feed_community(self):
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            # No "source" key → NULL → legacy → excluded from community.
            "sport": "mtb", "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-legacy-1",
            "activity_date": datetime(2025, 3, 3, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert _community_rows() == 0, "legacy NULL-source rows must not feed community"

    def test_community_reads_only_the_manual_upload(self):
        # A strava_api ride and a manual_upload ride of the same user: only the
        # manual one passes the gate.
        ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "sep-pub-strava",
            "source": STRAVA_API_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 900,
            "activity_date": datetime(2025, 3, 4, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-pub-manual",
            "activity_date": datetime(2025, 3, 5, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert _community_rows() == 1
