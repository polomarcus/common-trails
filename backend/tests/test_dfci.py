"""Tests for DFCI storage and display."""
import pytest

from app.services.ingest import (
    get_dfci_edge_count,
    get_dfci_geojson,
    store_dfci_edges,
)
from app.services.routing_profiles import TRAIL_SCORES, compute_trail_score

pytestmark = pytest.mark.slow


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
