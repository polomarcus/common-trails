"""Tests for /heatmap/trails API endpoint — direction fields exposure.

These tests insert edges in a remote Iceland bbox where production data
is sparse, then query that bbox specifically. Sport names that start
with `test_` are normalized to `gravel` by `_normalize_heat_edge_sport`,
so we use the real partition (`gravel`) and rely on the bbox + the
isolated geographic location for test isolation.
"""
import json

import pytest
from sqlalchemy import text as sa_text

from app.api.heatmap import _bbox_trails_cache, _trails_gz_cache
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

_SPORT = "gravel"
# Far-north Iceland bbox — production data here is effectively zero.
_TEST_BBOX = (-18.2, 65.6, -17.9, 65.8)
_TEST_BBOX_SQL_MAKE = (
    f"ST_MakeEnvelope({_TEST_BBOX[0]}, {_TEST_BBOX[1]}, "
    f"{_TEST_BBOX[2]}, {_TEST_BBOX[3]}, 4326)"
)


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _wipe_test_bbox():
    db = SessionLocal()
    try:
        db.execute(sa_text(f"""
            DELETE FROM heat_edge_contributors
            WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(geometry, {_TEST_BBOX_SQL_MAKE})
            )
        """))
        db.execute(sa_text(f"""
            DELETE FROM heat_edges
            WHERE ST_Intersects(geometry, {_TEST_BBOX_SQL_MAKE})
        """))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_test_edges():
    """Wipe edges in the Iceland test bbox + clear response caches."""
    _trails_gz_cache.clear()
    _bbox_trails_cache.clear()
    _wipe_test_bbox()
    yield
    _trails_gz_cache.clear()
    _bbox_trails_cache.clear()
    _wipe_test_bbox()


class TestHeatmapTrailsDirectionFields:
    """GET /heatmap/trails includes forward_count and backward_count in features."""

    # Iceland coords, < 5m gap → 1 edge per trace, no densification
    _FWD = [[-18.10000, 65.70000], [-18.09990, 65.70000]]
    _BWD = [[-18.09990, 65.70000], [-18.10000, 65.70000]]

    def _seed_edges(self):
        """Seed 2 forward + 1 backward on same edge with 3 unique users."""
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u2", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u3", _SPORT, _geojson(self._BWD))

    def _query(self, client):
        return client.get(
            f"/heatmap/trails?sport={_SPORT}"
            f"&min_lon={_TEST_BBOX[0]}&min_lat={_TEST_BBOX[1]}"
            f"&max_lon={_TEST_BBOX[2]}&max_lat={_TEST_BBOX[3]}"
        )

    def test_direction_fields_present(self, client):
        self._seed_edges()
        resp = self._query(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        features = data["features"]
        assert len(features) >= 1
        props = features[0]["properties"]
        assert "forward_count" in props
        assert "backward_count" in props

    def test_direction_values_correct(self, client):
        self._seed_edges()
        resp = self._query(client)
        data = resp.json()
        # The seeded edge is the only one in the test bbox
        props = data["features"][0]["properties"]
        assert props["forward_count"] == 2
        assert props["backward_count"] == 1
        assert props["forward_count"] + props["backward_count"] == props["pass_count"]

    def test_direction_fields_default_zero(self, client):
        """Features without backward traversals should have backward_count=0."""
        # Different segment, still inside the test bbox — slight lat shift
        coords = [[-18.10000, 65.71000], [-18.09990, 65.71000]]
        _update_heat_edges("u1", _SPORT, _geojson(coords))
        _update_heat_edges("u2", _SPORT, _geojson(coords))
        resp = self._query(client)
        data = resp.json()
        assert len(data["features"]) >= 1
        props = data["features"][0]["properties"]
        assert props["forward_count"] == 2
        assert props["backward_count"] == 0
