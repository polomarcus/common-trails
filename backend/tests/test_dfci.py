"""Tests for DFCI import and storage."""
import os

import pytest

from app.cli.import_dfci import _load_cache, _parse_ways, _save_cache
from app.services.ingest import (
    get_dfci_edge_count,
    get_dfci_geojson,
    store_dfci_edges,
)
from app.services.routing_profiles import TRAIL_SCORES, compute_trail_score

pytestmark = pytest.mark.slow


class TestDFCIParsing:
    """Test Overpass response parsing."""

    def test_parse_ways_basic(self):
        data = {
            "elements": [
                {"type": "node", "id": 1, "lon": 3.5, "lat": 43.5},
                {"type": "node", "id": 2, "lon": 3.6, "lat": 43.6},
                {"type": "node", "id": 3, "lon": 3.7, "lat": 43.7},
                {
                    "type": "way",
                    "id": 100,
                    "nodes": [1, 2, 3],
                    "tags": {
                        "ref:FR:DFCI": "A1",
                        "highway": "track",
                        "surface": "gravel",
                    },
                },
            ]
        }
        edges = _parse_ways(data)
        assert len(edges) == 1
        edge = edges[0]
        assert edge["trail_type"] == "DFCI"
        assert edge["trail_network"] is True
        assert edge["surface"] == "gravel"
        assert edge["highway"] == "track"
        assert edge["ref"] == "A1"
        assert edge["geometry"]["type"] == "LineString"
        assert len(edge["geometry"]["coordinates"]) == 3
        assert edge["geometry"]["coordinates"][0] == [3.5, 43.5]

    def test_parse_ways_missing_nodes(self):
        data = {
            "elements": [
                {"type": "node", "id": 1, "lon": 3.5, "lat": 43.5},
                {
                    "type": "way",
                    "id": 100,
                    "nodes": [1, 999],  # node 999 missing
                    "tags": {"ref:FR:DFCI": "B2"},
                },
            ]
        }
        edges = _parse_ways(data)
        # Way has only 1 resolved node → too few for LineString → skipped
        assert len(edges) == 0

    def test_parse_ways_default_tags(self):
        data = {
            "elements": [
                {"type": "node", "id": 1, "lon": 3.5, "lat": 43.5},
                {"type": "node", "id": 2, "lon": 3.6, "lat": 43.6},
                {
                    "type": "way",
                    "id": 100,
                    "nodes": [1, 2],
                    "tags": {"ref:FR:DFCI": "C3"},
                },
            ]
        }
        edges = _parse_ways(data)
        assert len(edges) == 1
        assert edges[0]["surface"] == "unknown"
        assert edges[0]["highway"] == "track"

    def test_parse_ways_ref_fallback(self):
        """Ways with ref~DFCI but no ref:FR:DFCI should use ref as fallback."""
        data = {
            "elements": [
                {"type": "node", "id": 1, "lon": 3.5, "lat": 43.5},
                {"type": "node", "id": 2, "lon": 3.6, "lat": 43.6},
                {
                    "type": "way",
                    "id": 100,
                    "nodes": [1, 2],
                    "tags": {"ref": "DFCI E1", "highway": "track"},
                },
            ]
        }
        edges = _parse_ways(data)
        assert len(edges) == 1
        assert edges[0]["ref"] == "DFCI E1"

    def test_parse_ways_ref_fr_dfci_preferred(self):
        """ref:FR:DFCI takes priority over ref when both present."""
        data = {
            "elements": [
                {"type": "node", "id": 1, "lon": 3.5, "lat": 43.5},
                {"type": "node", "id": 2, "lon": 3.6, "lat": 43.6},
                {
                    "type": "way",
                    "id": 100,
                    "nodes": [1, 2],
                    "tags": {"ref:FR:DFCI": "E1", "ref": "DFCI E1"},
                },
            ]
        }
        edges = _parse_ways(data)
        assert len(edges) == 1
        assert edges[0]["ref"] == "E1"

    def test_parse_empty_response(self):
        assert _parse_ways({}) == []
        assert _parse_ways({"elements": []}) == []


@pytest.fixture(autouse=True)
def _preserve_dfci():
    """Save fixture DFCI edges; restore after test wipes them."""
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        backup = db.execute(sa_text(
            "SELECT ref, surface, highway, trail_type, ST_AsEWKT(geometry) FROM dfci_edges"
        )).fetchall()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM dfci_edges"))
        for ref, surface, highway, trail_type, geom in backup:
            db.execute(sa_text("""
                INSERT INTO dfci_edges (ref, surface, highway, trail_type, geometry)
                VALUES (:ref, :surface, :highway, :trail_type, ST_GeomFromEWKT(:geom))
            """), {"ref": ref, "surface": surface, "highway": highway,
                   "trail_type": trail_type, "geom": geom})
        db.commit()
    finally:
        db.close()


class TestDFCIStore:
    """Test PostGIS DFCI store."""

    def test_store_and_retrieve(self):
        edges = [
            {
                "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
                "surface": "gravel",
                "highway": "track",
                "ref": "A1",
                "user_count": 0,
                "pass_count": 0,
                "trail_network": True,
                "trail_type": "DFCI",
            },
            {
                "geometry": {"type": "LineString", "coordinates": [[3.7, 43.7], [3.8, 43.8]]},
                "surface": "dirt",
                "highway": "track",
                "ref": "B2",
                "user_count": 0,
                "pass_count": 0,
                "trail_network": True,
                "trail_type": "DFCI",
            },
        ]
        count = store_dfci_edges(edges)
        assert count == 2
        assert get_dfci_edge_count() == 2

        geojson = get_dfci_geojson()
        refs = {f["properties"]["ref"] for f in geojson["features"]}
        assert "A1" in refs

    def test_store_replaces_previous(self):
        store_dfci_edges([{
            "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
            "surface": "gravel", "highway": "track", "ref": "old",
            "user_count": 0, "pass_count": 0, "trail_network": True, "trail_type": "DFCI",
        }])
        assert get_dfci_edge_count() == 1

        store_dfci_edges([])
        assert get_dfci_edge_count() == 0

    def test_geojson_output(self):
        store_dfci_edges([{
            "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
            "surface": "gravel", "highway": "track", "ref": "D4",
            "user_count": 0, "pass_count": 0, "trail_network": True, "trail_type": "DFCI",
        }])
        geojson = get_dfci_geojson()
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 1
        feat = geojson["features"][0]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "LineString"
        assert feat["properties"]["trail_type"] == "DFCI"
        assert feat["properties"]["ref"] == "D4"


class TestDFCICache:
    """Test local JSON cache for DFCI edges."""

    def test_save_and_load_cache(self, monkeypatch, tmp_path):
        cache_file = str(tmp_path / "dfci_edges.json")
        monkeypatch.setattr("app.cli.import_dfci._CACHE_WRITE_FILE", cache_file)
        monkeypatch.setattr("app.cli.import_dfci._CACHE_READ_PATHS", [cache_file])

        edges = [
            {
                "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
                "surface": "gravel", "highway": "track", "ref": "A1",
                "user_count": 0, "pass_count": 0, "trail_network": True, "trail_type": "DFCI",
            },
        ]
        _save_cache(edges)
        assert os.path.exists(cache_file)

        loaded = _load_cache()
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["ref"] == "A1"
        assert loaded[0]["geometry"]["coordinates"] == [[3.5, 43.5], [3.6, 43.6]]

    def test_load_cache_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cli.import_dfci._CACHE_READ_PATHS", [str(tmp_path / "nope.json")])
        assert _load_cache() is None

    def test_load_cache_corrupt_file(self, monkeypatch, tmp_path):
        cache_file = tmp_path / "bad.json"
        cache_file.write_text("NOT JSON")
        monkeypatch.setattr("app.cli.import_dfci._CACHE_READ_PATHS", [str(cache_file)])
        assert _load_cache() is None

    def test_load_cache_empty_list(self, monkeypatch, tmp_path):
        cache_file = tmp_path / "empty.json"
        cache_file.write_text("[]")
        monkeypatch.setattr("app.cli.import_dfci._CACHE_READ_PATHS", [str(cache_file)])
        assert _load_cache() is None


class TestDFCIApi:
    """Test DFCI API endpoints."""

    @pytest.fixture(autouse=True)
    def _clear_dfci(self, client):
        """Clear DFCI edges and GeoJSON cache before each test."""
        from sqlalchemy import text as sa_text

        from app.api import heatmap
        from app.db.session import SessionLocal

        heatmap._dfci_gz_cache = None
        db = SessionLocal()
        try:
            db.execute(sa_text("DELETE FROM dfci_edges"))
            db.commit()
        finally:
            db.close()
        yield
        heatmap._dfci_gz_cache = None

    def test_get_dfci_empty(self, client):
        resp = client.get("/heatmap/dfci")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 0

    def test_get_dfci_with_data(self, client):
        store_dfci_edges([{
            "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
            "surface": "gravel", "highway": "track", "ref": "X1",
            "user_count": 0, "pass_count": 0, "trail_network": True, "trail_type": "DFCI",
        }])
        resp = client.get("/heatmap/dfci")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["features"]) == 1
        assert data["features"][0]["properties"]["ref"] == "X1"
        assert data["features"][0]["properties"]["trail_type"] == "DFCI"
        assert data["metadata"]["license"] == "ODbL-1.0"

    def test_seed_dfci_in_test_mode(self, client):
        edges = [{
            "geometry": {"type": "LineString", "coordinates": [[3.5, 43.5], [3.6, 43.6]]},
            "surface": "gravel", "highway": "track", "ref": "S1",
            "user_count": 0, "pass_count": 0, "trail_network": True, "trail_type": "DFCI",
        }]
        resp = client.post("/heatmap/dfci", json=edges)
        assert resp.status_code == 200
        assert resp.json()["seeded"] == 1
        assert get_dfci_edge_count() == 1


class TestDFCITrailScore:
    """Test DFCI trail scoring integration."""

    def test_dfci_in_trail_scores(self):
        assert "DFCI" in TRAIL_SCORES
        assert TRAIL_SCORES["DFCI"] == 1.0

    def test_compute_trail_score_dfci(self):
        score = compute_trail_score("DFCI")
        assert score == 1.0

    def test_dfci_higher_than_gr(self):
        assert TRAIL_SCORES["DFCI"] >= TRAIL_SCORES["GR"]
