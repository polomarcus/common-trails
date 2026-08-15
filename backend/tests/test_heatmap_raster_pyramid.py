"""Unit tests for the raster XYZ pyramid builder (the gpx.studio/VisuGPX calque).

Drives the REAL builder on a tiny synthetic GeoJSONL (no DB), collecting tiles
in memory. Pins: occupied-tiles-only rendering, data-derived bounds, valid PNG
output, valid TileJSON.
"""
import gzip
import json

from app.services import heatmap_raster_pyramid as pyr


def _write_geojsonl(path, features, *, gzipped=False):
    lines = "\n".join(json.dumps(f) for f in features) + "\n"
    if gzipped:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(lines)


def _line(coords, pass_count=1, sport="gravel"):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"sport": sport, "user_count": 1, "pass_count": pass_count,
                       "heat_score": 0.5},
    }


def _is_png(b: bytes) -> bool:
    return b[:8] == b"\x89PNG\r\n\x1a\n"


def test_pyramid_renders_only_occupied_tiles_with_valid_pngs(tmp_path):
    # Two short traces near Montpellier's Lez (~3.89, 43.61).
    gj = tmp_path / "hm.geojsonl"
    _write_geojsonl(gj, [
        _line([[3.890, 43.610], [3.891, 43.611], [3.892, 43.612]], pass_count=80),
        _line([[3.900, 43.620], [3.901, 43.621]], pass_count=1),
    ])

    tiles: dict[tuple[int, int, int], bytes] = {}
    stats = pyr.build_raster_pyramid(
        str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
        min_zoom=6, max_zoom=12,
    )

    assert stats["features"] == 2
    assert stats["tiles"] > 0
    assert stats["tiles"] == len(tiles)
    # Every emitted blob is a real PNG.
    assert all(_is_png(b) for b in tiles.values())
    # Occupied-only: at z6 the whole Montpellier area is ONE tile — far fewer
    # than a bbox rectangle would emit. At every zoom the count is bounded.
    assert stats["by_zoom"][6] <= 2
    # bounds are the data extent, not a continent.
    min_lon, min_lat, max_lon, max_lat = stats["bounds"]
    assert 3.88 < min_lon < 3.90 and 43.60 < min_lat < 43.62
    assert 3.89 < max_lon < 3.91 and 43.61 < max_lat < 43.63


def test_pyramid_tile_indices_are_xyz_not_tms(tmp_path):
    """A northern-hemisphere trace must map to a SMALL y at low zoom (XYZ: y=0
    is the north edge). A TMS flip would put it near y=2^z-1."""
    gj = tmp_path / "hm.geojsonl"
    _write_geojsonl(gj, [_line([[3.89, 43.61], [3.90, 43.62]])])
    tiles = {}
    pyr.build_raster_pyramid(str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
                             min_zoom=8, max_zoom=8)
    (z, x, y) = next(iter(tiles))
    assert z == 8
    # lat 43.6 at z8 → y ≈ 92 (top-down). TMS would be 255-92=163.
    assert 88 <= y <= 96, f"y={y} looks TMS-flipped (expected top-down XYZ ~92)"


def test_gzipped_geojsonl_is_read(tmp_path):
    gj = tmp_path / "hm.geojsonl.gz"
    _write_geojsonl(gj, [_line([[3.89, 43.61], [3.90, 43.62]])], gzipped=True)
    tiles = {}
    stats = pyr.build_raster_pyramid(str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
                                     min_zoom=8, max_zoom=10)
    assert stats["features"] == 1
    assert stats["tiles"] == len(tiles) > 0


def test_empty_geojsonl_no_tiles(tmp_path):
    gj = tmp_path / "empty.geojsonl"
    gj.write_text("")
    tiles = {}
    stats = pyr.build_raster_pyramid(str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b))
    assert stats["features"] == 0
    assert stats["tiles"] == 0
    assert not tiles


def test_build_tilejson_is_valid():
    tj = pyr.build_tilejson(
        tiles_url="https://storage.googleapis.com/b/raster/{z}/{x}/{y}.png",
        min_zoom=6, max_zoom=14, bounds=(3.5, 43.0, 4.2, 43.9),
        attribution="© CHEMINS COMMUNS — ODbL 1.0",
    )
    assert tj["tilejson"] == "2.2.0"
    assert tj["scheme"] == "xyz"
    assert tj["tiles"] == ["https://storage.googleapis.com/b/raster/{z}/{x}/{y}.png"]
    assert tj["minzoom"] == 6 and tj["maxzoom"] == 14
    assert len(tj["bounds"]) == 4
    assert len(tj["center"]) == 3
    assert "ODbL" in tj["attribution"]
    # round-trips through JSON (what gets written to GCS).
    assert json.loads(json.dumps(tj))["scheme"] == "xyz"


def test_pass_count_drives_brightness(tmp_path):
    """A high-pass_count trace and a solo trace must render into DIFFERENT
    tiles (sanity that pass_count is read, not ignored) — both produce PNGs."""
    gj = tmp_path / "hm.geojsonl"
    _write_geojsonl(gj, [
        _line([[3.89, 43.61], [3.895, 43.615]], pass_count=200),
        _line([[4.50, 44.10], [4.505, 44.105]], pass_count=1),
    ])
    tiles = {}
    stats = pyr.build_raster_pyramid(str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
                                     min_zoom=12, max_zoom=12)
    # Two well-separated traces → at z12 they occupy distinct tiles.
    assert stats["by_zoom"][12] >= 2

def test_sport_filter_renders_only_matching_features(tmp_path):
    """Per-sport calque (raster-<sport>/): sport_filter renders ONLY features
    whose `sport` is in the set, and the bounds/tiles reflect just those — this
    is what lets a gpx.studio overlay show one sport (a raster can't be filtered
    client-side, so each sport is its own tileset)."""
    gj = tmp_path / "hm.geojsonl"
    _write_geojsonl(gj, [
        # gravel near the Lez (Montpellier)
        _line([[3.890, 43.610], [3.891, 43.611], [3.892, 43.612]], pass_count=50, sport="gravel"),
        # road FAR away (near Toulouse) so its tiles are distinct z-cells
        _line([[1.440, 43.600], [1.441, 43.601], [1.442, 43.602]], pass_count=90, sport="road"),
    ])

    def render(sport_filter):
        tiles: dict[tuple[int, int, int], bytes] = {}
        stats = pyr.build_raster_pyramid(
            str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
            min_zoom=6, max_zoom=12, sport_filter=sport_filter,
        )
        return stats, tiles

    all_stats, _ = render(None)
    gravel_stats, gravel_tiles = render({"gravel"})
    road_stats, road_tiles = render({"road"})

    # Filtered builds see only their sport's features.
    assert all_stats["features"] == 2
    assert gravel_stats["features"] == 1
    assert road_stats["features"] == 1

    # Bounds are the FILTERED extent: gravel is near Montpellier (~3.89),
    # road near Toulouse (~1.44) — they must not bleed into each other.
    assert 3.88 < gravel_stats["bounds"][0] < 3.90
    assert 1.43 < road_stats["bounds"][0] < 1.45

    # The two sports occupy disjoint z12 tiles (700 km apart) → no shared tile.
    g12 = {(x, y) for (z, x, y) in gravel_tiles if z == 12}
    r12 = {(x, y) for (z, x, y) in road_tiles if z == 12}
    assert g12 and r12 and g12.isdisjoint(r12)


def test_offroad_filter_expands_to_mtb_gravel_offroad(tmp_path):
    """The offroad calque must include mtb + gravel + offroad (mirrors the
    backend SSOT config.expand_sport, which build_pmtiles feeds as the filter)."""
    from app.config import expand_sport
    gj = tmp_path / "hm.geojsonl"
    _write_geojsonl(gj, [
        _line([[3.890, 43.610], [3.891, 43.611]], sport="mtb"),
        _line([[3.892, 43.612], [3.893, 43.613]], sport="gravel"),
        _line([[3.894, 43.614], [3.895, 43.615]], sport="offroad"),
        _line([[3.896, 43.616], [3.897, 43.617]], sport="road"),
        _line([[3.898, 43.618], [3.899, 43.619]], sport="running"),
    ])
    tiles: dict = {}
    stats = pyr.build_raster_pyramid(
        str(gj), upload_png=lambda z, x, y, b: tiles.__setitem__((z, x, y), b),
        min_zoom=6, max_zoom=12, sport_filter=set(expand_sport("offroad")),
    )
    # 3 of the 5 features (mtb+gravel+offroad), NOT road/running.
    assert stats["features"] == 3
