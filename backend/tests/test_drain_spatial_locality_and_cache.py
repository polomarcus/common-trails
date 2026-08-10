"""perf(drain): warm OSM caches + spatial-order the archive (2026-07-20).

The drain spent ~1–2 min PER activity because the OSM matcher reloaded
17k–65k segments from the DB per activity: the segment/grid caches were sized
for the 512 Mi web service, and the archive processed members in upload order
(region-hopping Montpellier ↔ Spain) so the small caches thrashed.

Pinned contracts:

  (a) the whole-archive drain processes members in SPATIAL-LOCALITY order —
      two interleaved regions come out grouped, not interleaved;
  (b) with a warm segment cache, matching two activities over the SAME tiles
      loads segments from the DB ONCE (not once per activity);
  (c) the three OSM-cache caps are read from the environment at use time, so
      the 8 Gi drain job can size them up without touching the code defaults
      (which must stay safe for the 512 Mi web service).

DB-free: everything is driven through monkeypatched seams, so these run in the
scratch venv without Postgres AND under the normal suite.
"""
import contextlib
import gzip
import io
import zipfile
from unittest.mock import MagicMock

from app.jobs import ingest_pending_archives as drain_mod
from app.services import ingest as ingest_service
from app.services.tile_keys import tile_key_from_latlon

# Two well-separated regions (different z14 tiles → different BIGINT keys).
_MONTPELLIER = (43.61, 3.87)
_BARCELONA = (41.39, 2.17)


def _gpx_bytes(lat: float, lon: float, n: int = 5) -> bytes:
    pts = "".join(
        f'<trkpt lat="{lat + i * 0.001}" lon="{lon + i * 0.001}"><ele>100</ele></trkpt>'
        for i in range(n)
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="test">'
        f"<trk><name>t</name><trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


class TestMemberSpatialKey:
    def test_gpx_first_point_tile(self):
        raw = _gpx_bytes(*_MONTPELLIER)
        assert drain_mod._member_spatial_key(raw, "ride.gpx") == tile_key_from_latlon(*_MONTPELLIER)

    def test_gz_member_is_decompressed(self):
        raw = _gpx_bytes(*_BARCELONA)
        gz = io.BytesIO()
        with gzip.GzipFile(fileobj=gz, mode="wb") as f:
            f.write(raw)
        assert drain_mod._member_spatial_key(gz.getvalue(), "ride.gpx.gz") == \
            tile_key_from_latlon(*_BARCELONA)

    def test_fit_member_has_no_cheap_probe(self):
        assert drain_mod._member_spatial_key(b"\x0e\x10PARSEFIT", "ride.fit") is None

    def test_unparseable_gpx_is_unknown(self):
        assert drain_mod._member_spatial_key(b"<gpx></gpx>", "empty.gpx") is None

    def test_sort_key_groups_known_before_unknown(self):
        mont = tile_key_from_latlon(*_MONTPELLIER)
        bcn = tile_key_from_latlon(*_BARCELONA)
        keys = [mont, None, bcn, None, mont]
        # sorted() must cluster: all known (ascending) then all unknown.
        ordered = sorted(keys, key=drain_mod._spatial_sort_key)
        assert ordered[-2:] == [None, None]
        known = [k for k in ordered if k is not None]
        assert known == sorted(known)


def _interleaved_archive() -> bytes:
    """Members deliberately interleaved by region in the zip's own order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mont_1.gpx", _gpx_bytes(*_MONTPELLIER))
        zf.writestr("spain_1.gpx", _gpx_bytes(*_BARCELONA))
        zf.writestr("mont_2.gpx", _gpx_bytes(_MONTPELLIER[0] + 0.02, _MONTPELLIER[1] + 0.02))
        zf.writestr("spain_2.gpx", _gpx_bytes(_BARCELONA[0] + 0.02, _BARCELONA[1] + 0.02))
        zf.writestr("mont_3.gpx", _gpx_bytes(_MONTPELLIER[0] + 0.04, _MONTPELLIER[1] + 0.04))
    return buf.getvalue()


class TestArchiveSpatialOrder:
    def test_members_are_grouped_by_region(self, monkeypatch):
        """(a) an interleaved 2-region archive is INGESTED grouped by region —
        never region-hopping — so the segment cache stays warm."""
        from app.services import archive_intake

        zip_bytes = _interleaved_archive()

        @contextlib.contextmanager
        def fake_open(backend, key):
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                yield zf

        processed_order: list[str] = []

        def fake_ingest(db, *, user_id, filename, raw, resolved_sport,
                        contribute_heatmap, source, skip_heat_computation,
                        collect_touched_ways=None):
            processed_order.append(filename)
            return "imported", "act-id", None

        monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", lambda: None)
        monkeypatch.setattr(drain_mod, "_recompute_heat_agg_batched", lambda w: None)
        monkeypatch.setattr(drain_mod, "_ping_pmtiles_rebuild", lambda i, s: None)
        monkeypatch.setattr(drain_mod, "_finish_archive", lambda *a, **k: None)
        monkeypatch.setattr(drain_mod, "_notify_archive_terminal", lambda *a, **k: None)
        monkeypatch.setattr(drain_mod, "_ingest_member_bytes", fake_ingest)
        monkeypatch.setattr(drain_mod, "_claim_archives", lambda db, limit: [{
            "id": "arch-1", "user_id": "u1", "storage_backend": "local",
            "bucket_key": "k", "fallback_sport": "road",
            "contribute_heatmap": True, "source": "manual_upload_archive",
        }])
        monkeypatch.setattr(archive_intake, "archive_size", lambda b, k: None)
        monkeypatch.setattr(archive_intake, "open_archive_zip", fake_open)
        monkeypatch.setattr("app.db.session.SessionLocal", MagicMock())

        summary = drain_mod.drain_pending_archives(limit=1, pace_seconds=0.0)

        assert summary["imported"] == 5, summary
        assert len(processed_order) == 5, processed_order
        regions = [name.split("_", 1)[0] for name in processed_order]
        # Contiguity: once the region changes it must never change back.
        switches = sum(1 for i in range(1, len(regions)) if regions[i] != regions[i - 1])
        assert switches == 1, f"region-hopping order (expected 1 switch): {processed_order}"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CountingDB:
    """Minimal db double: counts segment-load queries, returns given rows."""
    def __init__(self, rows=None):
        self.rows = rows or []
        self.segment_loads = 0

    def execute(self, statement, params=None):
        text = str(statement)
        if "FROM osm_road_edges" in text and "ST_StartPoint" in text:
            self.segment_loads += 1
        return _FakeResult(self.rows)


def _clear_osm_caches():
    ingest_service._osm_segment_cache.clear()
    ingest_service._osm_segment_cache_order.clear()
    ingest_service._osm_grid_cache.clear()
    ingest_service._osm_adjacency_cache.clear()


class TestSegmentCacheWarm:
    def test_same_tiles_load_from_db_once(self, monkeypatch):
        """(b) prewarming the SAME tiles twice hits the DB exactly once — the
        second activity reads segments from the warm cache."""
        monkeypatch.setenv("OSM_TILE_CACHE_MAX", "500")
        _clear_osm_caches()
        try:
            db = _CountingDB(rows=[])  # empty tiles still get a negative-cache entry
            coords = [[_MONTPELLIER[1], _MONTPELLIER[0]],
                      [_MONTPELLIER[1] + 0.001, _MONTPELLIER[0] + 0.001]]

            ingest_service._prewarm_osm_segments(coords, db)
            first = db.segment_loads
            ingest_service._prewarm_osm_segments(coords, db)
            second = db.segment_loads

            assert first == 1, "first prewarm should issue exactly one bulk load"
            assert second == 1, "second prewarm over the same tiles must NOT reload"
        finally:
            _clear_osm_caches()


class TestEnvKnobs:
    def test_caps_read_from_env(self, monkeypatch):
        """(c) the three caps come from the environment at use time."""
        monkeypatch.setenv("OSM_TILE_CACHE_MAX", "777")
        monkeypatch.setenv("OSM_GRID_CACHE_MAX", "42")
        monkeypatch.setenv("OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY", "123456")
        assert ingest_service._osm_segment_cache_max() == 777
        assert ingest_service._osm_grid_cache_max() == 42
        assert ingest_service._osm_grid_cache_max_segs_per_entry() == 123456

    def test_defaults_stay_conservative(self, monkeypatch):
        for k in ("OSM_TILE_CACHE_MAX", "OSM_GRID_CACHE_MAX",
                  "OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY"):
            monkeypatch.delenv(k, raising=False)
        assert ingest_service._osm_segment_cache_max() == 50
        assert ingest_service._osm_grid_cache_max() == 200
        assert ingest_service._osm_grid_cache_max_segs_per_entry() == 10000

    def test_segment_cache_eviction_respects_env_cap(self, monkeypatch):
        """A tightened OSM_TILE_CACHE_MAX evicts down to the new cap live."""
        monkeypatch.setenv("OSM_TILE_CACHE_MAX", "2")
        _clear_osm_caches()
        try:
            db = _CountingDB(rows=[])
            for tk in (100_001, 200_002, 300_003):
                ingest_service._get_cached_tile_segments(tk, db)
            assert len(ingest_service._osm_segment_cache) == 2
            assert len(ingest_service._osm_segment_cache_order) == 2
            # LRU: the oldest tile was evicted.
            assert 100_001 not in ingest_service._osm_segment_cache
        finally:
            _clear_osm_caches()
