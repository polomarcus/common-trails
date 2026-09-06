"""Perf refactor pins: parse-once raster pyramids + threaded tile uploads.

The rebuild used to re-read + re-json-parse + re-RDP-simplify the WHOLE
combined geojsonl once PER per-sport calque (up to 6 full parses), and upload
every tile PNG serially. These tests pin:

* ``parse_heat_edges`` + ``render_pyramid_from_parsed`` produce byte-identical
  tiles and identical stats (incl. the FILTERED per-sport bounds) to the legacy
  one-shot ``build_raster_pyramid`` path, for a 2-sport corpus.
* ``_render_and_upload_pyramid`` (build_pmtiles) delivers every tile to the
  uploader EXACTLY once through the thread pool, returns a ``written_keys``
  set that matches, and re-raises an injected upload exception (so a failed
  calque can never silently purge its siblings' tiles).

DB-free — drives the real builders on tiny synthetic GeoJSONL fixtures.
"""
import json
import threading

import pytest

import app.jobs.build_pmtiles as bp
from app.services import heatmap_raster_pyramid as pyr


def _line(coords, pass_count=1, sport="gravel"):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"sport": sport, "user_count": 1, "pass_count": pass_count,
                       "heat_score": 0.5},
    }


def _write_corpus(tmp_path):
    """2-sport corpus: gravel near Montpellier, road near Toulouse, plus a
    degenerate single-point road feature (bounds-only in the legacy path)."""
    gj = tmp_path / "hm.geojsonl"
    features = [
        _line([[3.890, 43.610], [3.891, 43.611], [3.892, 43.612]],
              pass_count=50, sport="gravel"),
        _line([[3.900, 43.620], [3.901, 43.621]], pass_count=3, sport="gravel"),
        _line([[1.440, 43.600], [1.441, 43.601], [1.442, 43.602]],
              pass_count=90, sport="road"),
        # Degenerate 1-point feature: legacy extended the road bounds with it
        # but never rendered it — the parsed path must do the same.
        _line([[1.500, 43.650]], pass_count=1, sport="road"),
    ]
    gj.write_text("\n".join(json.dumps(f) for f in features) + "\n",
                  encoding="utf-8")
    return gj


def _render_legacy(gj, sport_filter):
    tiles: dict[tuple[int, int, int], bytes] = {}
    stats = pyr.build_raster_pyramid(
        str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
        min_zoom=6, max_zoom=12, sport_filter=sport_filter,
    )
    return stats, tiles


def _render_parsed(parsed, sport_filter):
    tiles: dict[tuple[int, int, int], bytes] = {}
    stats = pyr.render_pyramid_from_parsed(
        parsed, upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
        min_zoom=6, max_zoom=12, sport_filter=sport_filter,
    )
    return stats, tiles


def test_parse_once_matches_legacy_tiles_and_stats(tmp_path):
    """Parse ONCE, render per sport from the shared feature list → identical
    stats (features/tiles/by_zoom/bounds) AND byte-identical PNGs vs the
    legacy re-parse-per-pyramid path, for every filter the rebuild uses."""
    gj = _write_corpus(tmp_path)
    parsed = pyr.parse_heat_edges(str(gj))

    for sport_filter in (None, {"gravel"}, {"road"}):
        legacy_stats, legacy_tiles = _render_legacy(gj, sport_filter)
        new_stats, new_tiles = _render_parsed(parsed, sport_filter)
        assert new_stats == legacy_stats, f"stats diverged for {sport_filter}"
        assert new_tiles == legacy_tiles, f"tiles diverged for {sport_filter}"

    # Sanity on the corpus itself: filters really filter, bounds really follow
    # the FILTERED extent (gravel ~3.89, road ~1.44 — 700 km apart), and the
    # degenerate 1-pt road feature extended the road bounds without rendering.
    gravel_stats, _ = _render_parsed(parsed, {"gravel"})
    road_stats, _ = _render_parsed(parsed, {"road"})
    assert gravel_stats["features"] == 2
    assert road_stats["features"] == 1  # the 1-pt feature is bounds-only
    assert 3.88 < gravel_stats["bounds"][0] < 3.91
    assert 1.43 < road_stats["bounds"][0] < 1.45
    assert road_stats["bounds"][2] > 1.49  # extended by the 1-pt feature


def test_threaded_uploader_delivers_every_tile_exactly_once(tmp_path):
    gj = _write_corpus(tmp_path)
    parsed = pyr.parse_heat_edges(str(gj))

    lock = threading.Lock()
    delivered: dict[str, int] = {}

    def _upload(key: str, png: bytes) -> None:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        with lock:
            delivered[key] = delivered.get(key, 0) + 1

    stats, written = bp._render_and_upload_pyramid(
        parsed, upload_key_png=_upload, prefix="raster-test",
        min_zoom=6, max_zoom=12, sport_filter=None, workers=8,
    )

    assert stats["tiles"] > 0
    # Exactly once each, and written_keys (what the purge preserves) is exactly
    # the delivered set — no dupes, no drops, no phantom keys.
    assert all(count == 1 for count in delivered.values()), delivered
    assert written == set(delivered)
    assert len(written) == stats["tiles"]
    assert all(k.startswith("raster-test/") and k.endswith(".png")
               for k in written)


def test_threaded_uploader_propagates_upload_exception(tmp_path):
    gj = _write_corpus(tmp_path)
    parsed = pyr.parse_heat_edges(str(gj))

    lock = threading.Lock()
    calls = {"n": 0}

    def _upload(key: str, png: bytes) -> None:
        with lock:
            calls["n"] += 1
            if calls["n"] == 2:  # fail one mid-stream upload
                raise RuntimeError("gcs boom")

    with pytest.raises(RuntimeError, match="gcs boom"):
        bp._render_and_upload_pyramid(
            parsed, upload_key_png=_upload, prefix="raster-test",
            min_zoom=6, max_zoom=12, sport_filter=None, workers=4,
        )
    assert calls["n"] >= 2  # the failing upload really ran


def test_upload_workers_env_default_and_override(monkeypatch):
    monkeypatch.delenv("HEATMAP_RASTER_UPLOAD_WORKERS", raising=False)
    assert bp._raster_upload_workers() == 16
    monkeypatch.setenv("HEATMAP_RASTER_UPLOAD_WORKERS", "4")
    assert bp._raster_upload_workers() == 4
    monkeypatch.setenv("HEATMAP_RASTER_UPLOAD_WORKERS", "not-a-number")
    assert bp._raster_upload_workers() == 16
    monkeypatch.setenv("HEATMAP_RASTER_UPLOAD_WORKERS", "0")
    assert bp._raster_upload_workers() == 1
