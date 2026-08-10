"""Non-regression tests for GET /heatmap/export response-size hardening.

The export endpoint defaulted to a WHOLE-WORLD bbox with NO LIMIT and ALWAYS
returned an uncompressed ``JSONResponse`` — a cold full-bbox request could
build a body past Cloud Run's 32 MiB non-streaming cap → a silent 500. The fix
(mirroring /heatmap/trails + /heatmap/dfci):

* gzip the body when the client sends ``accept-encoding: gzip``;
* cap the feature count (``_EXPORT_MAX_FEATURES``) so an unbounded whole-world
  request can never build an over-cap body, flagging ``metadata.truncated``.

These drive the REAL handler (``app.api.heatmap.heatmap_export``) so handler
drift is caught, and seed edges in a remote Iceland bbox where prod data is
effectively zero (same isolation trick as test_heatmap.py).
"""
import gzip
import json

import pytest
from sqlalchemy import text as sa_text
from starlette.requests import Request

from app.api import heatmap as heatmap_api
from app.api.heatmap import heatmap_export
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

_SPORT = "gravel"
# Far-north Iceland bbox — production data here is effectively zero.
_TEST_BBOX = (-18.2, 65.6, -17.9, 65.9)
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
    _wipe_test_bbox()
    yield
    _wipe_test_bbox()


def _seed_cells(n: int) -> None:
    """Seed ``n`` distinct z14 cells (spaced in latitude), each with 2 users so
    they clear the default K=2 anonymity threshold."""
    for i in range(n):
        lat = 65.60 + i * 0.05  # >> one z14 tile apart → distinct cell_key
        seg = [[-18.10000, lat], [-18.09990, lat]]
        _update_heat_edges("u1", _SPORT, _geojson(seg))
        _update_heat_edges("u2", _SPORT, _geojson(seg))


def _make_request(accept_encoding: str | None) -> Request:
    headers = []
    if accept_encoding is not None:
        headers.append((b"accept-encoding", accept_encoding.encode()))
    return Request({"type": "http", "method": "GET", "headers": headers})


def _call_export(request: Request):
    return heatmap_export(
        request=request,
        sport=_SPORT,
        zoom=14,
        min_lon=_TEST_BBOX[0],
        min_lat=_TEST_BBOX[1],
        max_lon=_TEST_BBOX[2],
        max_lat=_TEST_BBOX[3],
    )


class TestHeatmapExportGzip:
    def test_gzip_path_sets_encoding_and_is_smaller(self):
        _seed_cells(3)

        gz_resp = _call_export(_make_request("gzip"))
        plain_resp = _call_export(_make_request(None))

        # gzip branch: Content-Encoding header + a genuinely compressed body
        assert gz_resp.headers.get("content-encoding") == "gzip"
        assert "content-encoding" not in {k.lower() for k in plain_resp.headers}
        assert len(gz_resp.body) < len(plain_resp.body)

        # The compressed body decodes back to the same GeoJSON the plain path
        # returns (feature_count etc. identical) — gzip must be transparent.
        gz_json = json.loads(gzip.decompress(gz_resp.body))
        plain_json = json.loads(plain_resp.body)
        assert gz_json["type"] == "FeatureCollection"
        assert gz_json["metadata"]["feature_count"] == plain_json["metadata"]["feature_count"]
        assert gz_json["metadata"]["feature_count"] >= 3
        assert gz_json["metadata"]["truncated"] is False


class TestHeatmapExportCap:
    def test_unbounded_request_is_capped(self, monkeypatch):
        _seed_cells(3)
        # Force the cap below the seeded feature count — an unbounded/dense
        # request must never build the full body.
        monkeypatch.setattr(heatmap_api, "_EXPORT_MAX_FEATURES", 1)

        resp = _call_export(_make_request(None))
        data = json.loads(resp.body)
        assert data["metadata"]["truncated"] is True
        assert data["metadata"]["feature_count"] == 1
        assert len(data["features"]) == 1
        assert "hint" in data["metadata"]

    def test_within_cap_not_truncated(self, monkeypatch):
        _seed_cells(3)
        monkeypatch.setattr(heatmap_api, "_EXPORT_MAX_FEATURES", 1000)
        resp = _call_export(_make_request(None))
        data = json.loads(resp.body)
        assert data["metadata"]["truncated"] is False
        assert data["metadata"]["feature_count"] >= 3
