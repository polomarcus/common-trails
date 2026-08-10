"""Tests for the local HGT reader (`app.services.local_dem`).

We build a synthetic 1° tile in a tmp dir, point the module at it via
``DEM_DIR``, and assert:

- filename derivation (N/S/E/W naming)
- bilinear interpolation at known points
- void cells (-32768) return None instead of leaking
- missing tile returns None (and is negative-cached)
- ``slope_grade`` returns sane meters + percent
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

# Module is imported lazily inside fixture so DEM_DIR is set before import
# (the module reads the env var at import time).


_HGT_SIDE = 1201


def _write_hgt(path: Path, arr: np.ndarray) -> None:
    """Serialize a 1201×1201 int16 array as big-endian HGT."""
    assert arr.shape == (_HGT_SIDE, _HGT_SIDE)
    assert arr.dtype == np.int16
    path.write_bytes(arr.astype(">i2").tobytes())


def _make_ramp_tile(elev_north: int = 100, elev_south: int = 200) -> np.ndarray:
    """Tile elevation ramps linearly from north (row 0) to south (row 1200).
    Lon is constant. Useful for predictable bilinear checks."""
    rows = np.linspace(elev_north, elev_south, _HGT_SIDE, dtype=np.float32)
    arr = np.tile(rows[:, None], (1, _HGT_SIDE)).astype(np.int16)
    return arr


@pytest.fixture
def dem_dir(tmp_path, monkeypatch):
    """Stage a synthetic DEM_DIR and force a fresh import of local_dem."""
    monkeypatch.setenv("DEM_DIR", str(tmp_path))
    # Force re-import so module-level _DEM_DIR picks up the env var
    import importlib

    import app.services.local_dem as ld
    importlib.reload(ld)
    ld.clear_cache()
    yield tmp_path, ld
    ld.clear_cache()


def test_tile_filename_naming(dem_dir):
    _, ld = dem_dir
    # Northern hemisphere, eastern hemisphere
    assert ld._tile_filename(43, 3) == "N43E003.hgt"
    # Southern, western
    assert ld._tile_filename(-12, -57) == "S12W057.hgt"
    # Zero-padding
    assert ld._tile_filename(0, 0) == "N00E000.hgt"
    assert ld._tile_filename(5, 5) == "N05E005.hgt"


def test_missing_tile_returns_none(dem_dir):
    """No HGT on disk → elevation_at returns None (and tile_available is False)."""
    _, ld = dem_dir
    assert ld.tile_available(43.5, 3.5) is False
    assert ld.elevation_at(43.5, 3.5) is None


def test_basic_lookup_returns_expected_elevation(dem_dir):
    """Constant tile of 500m → all lookups return 500m."""
    tmp, ld = dem_dir
    arr = np.full((_HGT_SIDE, _HGT_SIDE), 500, dtype=np.int16)
    _write_hgt(tmp / "N43E003.hgt", arr)
    assert ld.tile_available(43.5, 3.5) is True
    assert ld.elevation_at(43.5, 3.5) == pytest.approx(500.0)
    # Edges
    assert ld.elevation_at(43.0, 3.0) == pytest.approx(500.0)
    assert ld.elevation_at(43.999, 3.999) == pytest.approx(500.0)


def test_bilinear_ramp(dem_dir):
    """North→South ramp 100m→200m: midpoint should be ~150m."""
    tmp, ld = dem_dir
    arr = _make_ramp_tile(100, 200)
    _write_hgt(tmp / "N43E003.hgt", arr)

    # At lat=43.0 we're on the southern edge (row 1200) → 200m
    assert ld.elevation_at(43.0, 3.5) == pytest.approx(200.0, abs=0.5)
    # At lat=44.0 we're on the northern edge (row 0) → 100m
    assert ld.elevation_at(43.999, 3.5) == pytest.approx(100.0, abs=0.5)
    # Midpoint
    assert ld.elevation_at(43.5, 3.5) == pytest.approx(150.0, abs=1.0)
    # Quarter point
    assert ld.elevation_at(43.75, 3.5) == pytest.approx(125.0, abs=1.0)


def test_void_returns_none(dem_dir):
    """A void cell anywhere among the four bilinear corners → None."""
    tmp, ld = dem_dir
    arr = np.full((_HGT_SIDE, _HGT_SIDE), 500, dtype=np.int16)
    # Plant a void roughly at lat 43.5, lon 3.5 (centre of tile).
    # That maps to (row, col) ≈ (600, 600). Stick a void at (600, 600).
    arr[600, 600] = -32768
    _write_hgt(tmp / "N43E003.hgt", arr)

    # Exact centre — the void is one of the four corners → None
    # (lat=43.5 → row_f=600.0; lon=3.5 → col_f=600.0)
    assert ld.elevation_at(43.5, 3.5) is None
    # Far from the void cell: should be the 500m background
    assert ld.elevation_at(43.0, 3.0) == pytest.approx(500.0)


def test_corrupt_tile_returns_none(dem_dir):
    """A wrong-sized HGT file is rejected (warning logged) → None."""
    tmp, ld = dem_dir
    # Write a too-short file
    (tmp / "N43E003.hgt").write_bytes(struct.pack(">h", 100) * 100)
    assert ld.elevation_at(43.5, 3.5) is None


def test_slope_grade_haversine(dem_dir):
    """Ramp tile: 100m N → 200m S over ~111 km → ~0.09% slope."""
    tmp, ld = dem_dir
    arr = _make_ramp_tile(100, 200)
    _write_hgt(tmp / "N43E003.hgt", arr)

    # 1° lat ≈ 111 km. Climb 100m over 111 km → 100/111000 = 0.09%
    e1, e2, slope = ld.slope_grade(43.999, 3.5, 43.0, 3.5)
    assert e1 == pytest.approx(100.0, abs=1.0)
    assert e2 == pytest.approx(200.0, abs=1.0)
    assert slope == pytest.approx(0.09, abs=0.02)


def test_slope_grade_missing_tile(dem_dir):
    """No tile on disk → all three Nones."""
    _, ld = dem_dir
    e1, e2, slope = ld.slope_grade(43.5, 3.5, 43.5001, 3.5001)
    assert (e1, e2, slope) == (None, None, None)


def test_slope_grade_degenerate(dem_dir):
    """Same start/end point → slope 0, elevations equal."""
    tmp, ld = dem_dir
    _write_hgt(tmp / "N43E003.hgt", np.full((_HGT_SIDE, _HGT_SIDE), 500, dtype=np.int16))
    e1, e2, slope = ld.slope_grade(43.5, 3.5, 43.5, 3.5)
    assert e1 == pytest.approx(500.0)
    assert e2 == pytest.approx(500.0)
    assert slope == pytest.approx(0.0)


def test_negative_cache_for_missing(dem_dir):
    """A miss caches the absence; later misses don't re-stat the disk."""
    tmp, ld = dem_dir
    ld.clear_cache()
    assert ld.elevation_at(50.5, 0.5) is None
    # Now write the file — should still report None because of negative cache
    _write_hgt(tmp / "N50E000.hgt", np.full((_HGT_SIDE, _HGT_SIDE), 42, dtype=np.int16))
    assert ld.elevation_at(50.5, 0.5) is None
    # Cache clear restores correctness
    ld.clear_cache()
    assert ld.elevation_at(50.5, 0.5) == pytest.approx(42.0)


def test_southern_hemisphere_naming(dem_dir):
    """Negative lat → S prefix, floor() gives the right tile."""
    tmp, ld = dem_dir
    # lat=-12.3 → floor = -13 → filename S13...
    _write_hgt(tmp / "S13E045.hgt", np.full((_HGT_SIDE, _HGT_SIDE), 7, dtype=np.int16))
    assert ld.elevation_at(-12.3, 45.5) == pytest.approx(7.0)
