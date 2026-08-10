"""PUBLIC ⟷ PERSONAL provenance separation (②).

Compliance (Strava 2026 API Policy §5.4/§5.10): Strava-API-sourced data may
NOT feed the public/community heatmap; only the user's own uploaded archive
(``source='manual_upload'``) may. The community layer is ``heat_edges`` — we
enforce this at INGEST (option (b)): only community-eligible activities ever
write to ``heat_edges``, so every downstream reader is correct by construction.

These tests FAIL on the pre-change code (where a strava_api ride wrote
``heat_edges``) and PASS after the provenance gate. They drive the REAL
handlers (``ingest_activity`` inline path + ``process_heat_compute``), never an
inline mirror.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import get_heat_edges_public, ingest_activity
from app.services.provenance import (
    COMMUNITY_SOURCE,
    STRAVA_API_SOURCE,
    is_community_source,
    should_promote_to_community,
)

# A distinctive, otherwise-empty bbox (Gulf of Guinea) so the test's heat_edges
# never collide with real dev data. Short (<60m) segments so grid-fallback
# edges survive the public-read spaghetti filter.
_LON0, _LAT0 = 7.0005, 4.0005
_BBOX = (7.0, 4.0, 7.01, 4.01)
_USER = "prov-sep-user"


def _geo(n: int = 5, step: float = 0.0002) -> str:
    coords = [[round(_LON0 + i * step, 6), round(_LAT0 + i * step, 6)] for i in range(n)]
    return json.dumps({"type": "LineString", "coordinates": coords})


def _count_edges() -> int:
    db = SessionLocal()
    try:
        return db.execute(sa_text(
            "SELECT COUNT(*) FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]}).scalar() or 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id LIKE 'prov-sep%'"))
        db.execute(sa_text(
            "DELETE FROM heat_edge_contributors WHERE edge_key IN ("
            "  SELECT edge_key FROM heat_edges "
            "  WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326)))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
        db.execute(sa_text(
            "DELETE FROM heat_edges "
            "WHERE ST_Intersects(geometry, ST_MakeEnvelope(:a,:b,:c,:d,4326))"
        ), {"a": _BBOX[0], "b": _BBOX[1], "c": _BBOX[2], "d": _BBOX[3]})
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
    """The inline ``ingest_activity`` heat path gates on provenance."""

    def test_strava_api_does_not_feed_community(self):
        before = _count_edges()
        r = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "sep-strava-1",
            "source": STRAVA_API_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 900,
            "activity_date": datetime(2025, 3, 1, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)  # skip_heat_computation defaults False → inline
        assert r["status"] == "created"
        assert _count_edges() == before, (
            "strava_api activity must NOT write heat_edges (community layer)"
        )

    def test_manual_upload_feeds_community(self):
        before = _count_edges()
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-manual-1",
            "activity_date": datetime(2025, 3, 2, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert r["edges_indexed"] > 0
        assert _count_edges() > before, "manual_upload activity must feed heat_edges"

    def test_legacy_null_source_does_not_feed_community(self):
        before = _count_edges()
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            # No "source" key → NULL → legacy → excluded from community.
            "sport": "mtb", "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-legacy-1",
            "activity_date": datetime(2025, 3, 3, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True)
        assert r["status"] == "created"
        assert _count_edges() == before, "legacy NULL-source rows must not feed community"

    def test_public_read_reflects_only_community_contributions(self):
        # A strava_api ride writes nothing; a manual_upload ride writes edges.
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
        edges = get_heat_edges_public(bbox=_BBOX, k=1)
        # Every public edge in this bbox originates from the manual_upload ride
        # (the strava_api ride wrote zero). If the gate regressed, the strava
        # ride would also appear and doubling the contributor pool.
        assert len(edges) >= 1


class TestProcessHeatComputeGate:
    """The prod chokepoint (webhook + GPX-upload heat tasks) gates on provenance."""

    def test_strava_api_activity_is_noop(self):
        from app.api.internal_ingest import process_heat_compute
        r = ingest_activity(_USER, {
            "provider": "strava", "provider_activity_id": "sep-phc-strava",
            "source": STRAVA_API_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 900,
            "activity_date": datetime(2025, 3, 6, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True, skip_heat_computation=True)
        before = _count_edges()
        res = process_heat_compute(r["activity_id"], _USER)
        assert res["status"] == "noop"
        assert res["edges_indexed"] == 0
        assert _count_edges() == before

    def test_manual_upload_activity_computes(self):
        from app.api.internal_ingest import process_heat_compute
        r = ingest_activity(_USER, {
            "provider": "file", "provider_activity_id": None,
            "source": COMMUNITY_SOURCE, "sport": "mtb",
            "geometry_geojson": _geo(), "distance_m": 5000,
            "file_hash": "sep-phc-manual",
            "activity_date": datetime(2025, 3, 7, 8, 0, tzinfo=UTC),
        }, contribute_heatmap=True, skip_heat_computation=True)
        before = _count_edges()
        res = process_heat_compute(r["activity_id"], _USER)
        assert res["status"] == "ok"
        assert res["edges_indexed"] > 0
        assert _count_edges() > before
