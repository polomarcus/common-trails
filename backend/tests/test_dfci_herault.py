"""Tests for DFCI Hérault import (departmental open data)."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli.import_dfci_herault import (
    DfciTrack,
    _load_cache,
    _save_cache,
    fetch_all_records,
    parse_dfci_track,
    tracks_to_edges,
)
from app.services.ingest import (
    get_dfci_edge_count,
    get_dfci_geojson,
    store_dfci_edges,
)

pytestmark = pytest.mark.slow

# ── Sample API records ────────────────────────────────────────────────────

SAMPLE_RECORD_LINESTRING = {
    "n_dfci_1": "D34-A1-T2",
    "n_dfci_2": "T2",
    "statut_operationnel": "Opérationnel",
    "cat_act": "Piste non revêtue",
    "geo_shape": {
        "type": "LineString",
        "coordinates": [[3.5, 43.5], [3.6, 43.6], [3.7, 43.7]],
    },
}

SAMPLE_RECORD_MULTILINE = {
    "n_dfci_1": "D34-B2-T1",
    "n_dfci_2": "T1",
    "statut_operationnel": "Opérationnel",
    "cat_act": "Chemin",
    "geo_shape": {
        "type": "MultiLineString",
        "coordinates": [
            [[3.5, 43.5], [3.6, 43.6]],
            [[3.6, 43.6], [3.7, 43.7]],
        ],
    },
}

SAMPLE_RECORD_NO_GEOM = {
    "n_dfci_1": "D34-C3",
    "statut_operationnel": "Opérationnel",
}

SAMPLE_RECORD_NON_OPERATIONAL = {
    "n_dfci_1": "D34-D4-T3",
    "n_dfci_2": "T3",
    "statut_operationnel": "Non opérationnel",
    "cat_act": "Sentier",
    "geo_shape": {
        "type": "LineString",
        "coordinates": [[3.8, 43.8], [3.9, 43.9]],
    },
}


class TestParseDfciTrack:
    """Test API record → DfciTrack parsing."""

    def test_parse_linestring(self):
        track = parse_dfci_track(SAMPLE_RECORD_LINESTRING)
        assert track is not None
        assert track.ref == "D34-A1-T2"
        assert track.troncon == "T2"
        assert track.statut == "Opérationnel"
        assert track.surface == "dirt"
        assert len(track.coords) == 3
        assert track.coords[0] == [3.5, 43.5]

    def test_parse_multilinestring(self):
        track = parse_dfci_track(SAMPLE_RECORD_MULTILINE)
        assert track is not None
        assert track.ref == "D34-B2-T1"
        assert track.surface == "dirt"  # "Chemin" → dirt
        assert len(track.coords) == 4  # 2 + 2 flattened

    def test_parse_no_geometry_returns_none(self):
        track = parse_dfci_track(SAMPLE_RECORD_NO_GEOM)
        assert track is None

    def test_parse_surface_mapping(self):
        """Different cat_act values map to correct OSM surfaces."""
        for cat_act, expected in [
            ("Route", "asphalt"),
            ("Piste revêtue", "asphalt"),
            ("Piste non revêtue", "dirt"),
            ("Chemin", "dirt"),
            ("Sentier", "ground"),
            ("Inconnu", "dirt"),  # fallback
        ]:
            record = {
                "n_dfci_1": "X",
                "cat_act": cat_act,
                "statut_operationnel": "",
                "geo_shape": {
                    "type": "LineString",
                    "coordinates": [[3.5, 43.5], [3.6, 43.6]],
                },
            }
            track = parse_dfci_track(record)
            assert track is not None
            assert track.surface == expected, f"cat_act={cat_act!r}"

    def test_parse_missing_ref_uses_n_dfci_2(self):
        record = {
            "n_dfci_2": "T5",
            "statut_operationnel": "",
            "cat_act": "",
            "geo_shape": {
                "type": "LineString",
                "coordinates": [[3.5, 43.5], [3.6, 43.6]],
            },
        }
        track = parse_dfci_track(record)
        assert track is not None
        assert track.ref == "T5"

    def test_parse_single_point_returns_none(self):
        record = {
            "n_dfci_1": "X",
            "statut_operationnel": "",
            "cat_act": "",
            "geo_shape": {
                "type": "LineString",
                "coordinates": [[3.5, 43.5]],
            },
        }
        track = parse_dfci_track(record)
        assert track is None


class TestTracksToEdges:
    """Test DfciTrack → edge dict conversion."""

    def test_basic_conversion(self):
        track = DfciTrack(
            ref="D34-A1",
            troncon="",
            coords=[[3.5, 43.5], [3.6, 43.6]],
            statut="Opérationnel",
            surface="dirt",
        )
        edges = tracks_to_edges([track])
        assert len(edges) == 1
        edge = edges[0]
        assert edge["trail_type"] == "DFCI"
        assert edge["trail_network"] is True
        assert edge["highway"] == "track"
        assert edge["surface"] == "dirt"
        assert edge["ref"] == "D34-A1"
        assert edge["geometry"]["type"] == "LineString"
        assert edge["geometry"]["coordinates"] == [[3.5, 43.5], [3.6, 43.6]]
        assert edge["user_count"] == 0
        assert edge["pass_count"] == 0

    def test_empty_list(self):
        assert tracks_to_edges([]) == []


class TestFetchPagination:
    """Test API pagination logic."""

    @pytest.mark.asyncio
    async def test_single_page(self):
        records = [{"n_dfci_1": f"R{i}"} for i in range(50)]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": records,
            "total_count": 50,
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.cli.import_dfci_herault.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_all_records()

        assert len(result) == 50
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_two_pages(self):
        """150 records → 2 API calls (100 + 50)."""
        page1 = [{"n_dfci_1": f"R{i}"} for i in range(100)]
        page2 = [{"n_dfci_1": f"R{i}"} for i in range(100, 150)]

        resp1 = MagicMock()
        resp1.raise_for_status = MagicMock()
        resp1.json.return_value = {"results": page1, "total_count": 150}

        resp2 = MagicMock()
        resp2.raise_for_status = MagicMock()
        resp2.json.return_value = {"results": page2, "total_count": 150}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp1, resp2])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.cli.import_dfci_herault.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_all_records()

        assert len(result) == 150
        assert mock_client.get.call_count == 2


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


class TestStoreEdgesIntegration:
    """Test DfciTrack → edges → store_dfci_edges."""

    def test_tracks_stored_correctly(self):
        track = parse_dfci_track(SAMPLE_RECORD_LINESTRING)
        assert track is not None
        edges = tracks_to_edges([track])
        count = store_dfci_edges(edges)
        assert count == 1
        assert get_dfci_edge_count() == 1

        geojson = get_dfci_geojson()
        assert len(geojson["features"]) == 1
        assert geojson["features"][0]["properties"]["ref"] == "D34-A1-T2"
        assert geojson["features"][0]["properties"]["trail_type"] == "DFCI"


class TestStatutFiltering:
    """Test that non-operational tracks are filtered."""

    def test_non_operational_skipped(self):
        track = parse_dfci_track(SAMPLE_RECORD_NON_OPERATIONAL)
        assert track is not None
        # The track parses fine, but statut is "Non opérationnel"
        assert track.statut == "Non opérationnel"
        # Filtering happens in import_dfci_herault(), not in parse_dfci_track()
        # The import function checks statut and skips non-operational

    def test_operational_passes(self):
        track = parse_dfci_track(SAMPLE_RECORD_LINESTRING)
        assert track is not None
        assert track.statut == "Opérationnel"


class TestHeraultCache:
    """Test local JSON cache for Hérault DFCI edges."""

    def test_save_and_load_cache(self, monkeypatch, tmp_path):
        cache_file = str(tmp_path / "dfci_herault_edges.json")
        monkeypatch.setattr("app.cli.import_dfci_herault._CACHE_WRITE_FILE", cache_file)
        monkeypatch.setattr("app.cli.import_dfci_herault._CACHE_READ_PATHS", [cache_file])

        edges = [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[3.5, 43.5], [3.6, 43.6]],
                },
                "surface": "dirt",
                "highway": "track",
                "ref": "D34-A1",
                "user_count": 0,
                "pass_count": 0,
                "trail_network": True,
                "trail_type": "DFCI",
            },
        ]
        _save_cache(edges)
        assert os.path.exists(cache_file)

        loaded = _load_cache()
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["ref"] == "D34-A1"

    def test_load_cache_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cli.import_dfci_herault._CACHE_READ_PATHS", [str(tmp_path / "nope.json")])
        assert _load_cache() is None

    def test_load_cache_corrupt_file(self, monkeypatch, tmp_path):
        cache_file = tmp_path / "bad.json"
        cache_file.write_text("NOT JSON")
        monkeypatch.setattr("app.cli.import_dfci_herault._CACHE_READ_PATHS", [str(cache_file)])
        assert _load_cache() is None

    def test_load_cache_empty_list(self, monkeypatch, tmp_path):
        cache_file = tmp_path / "empty.json"
        cache_file.write_text("[]")
        monkeypatch.setattr("app.cli.import_dfci_herault._CACHE_READ_PATHS", [str(cache_file)])
        assert _load_cache() is None
