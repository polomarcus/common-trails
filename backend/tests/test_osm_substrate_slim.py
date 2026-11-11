"""Unit pins for the 0056 substrate slim: BIGINT tile keys.

All pure — no DB needed. (The region-partition and import-guard pins left
with ``app.cli.import_osm_roads``.)
"""
from __future__ import annotations

import pytest

from app.services.tile_keys import (
    tile_key_from_latlon,
    tile_key_from_legacy,
    tile_key_from_xy,
    tile_key_to_xy,
)

# ── tile_keys encoding ─────────────────────────────────────────────────


def test_tile_key_round_trip():
    key = tile_key_from_xy(8452, 5882)
    assert key == 845205882  # human-readable in psql
    assert tile_key_to_xy(key) == (8452, 5882)


def test_tile_key_from_latlon_matches_slippy_math():
    # Montpellier — the same maths the legacy "14/x/y" producers used.
    key = tile_key_from_latlon(43.61, 3.877)
    x, y = tile_key_to_xy(key)
    assert x == 8368 and y == 5982, f"unexpected tile for Montpellier: {x}/{y}"


def test_tile_key_from_legacy_accepts_text_and_int():
    assert tile_key_from_legacy("14/8452/5882") == tile_key_from_xy(8452, 5882)
    assert tile_key_from_legacy(845205882) == 845205882
    with pytest.raises(ValueError):
        tile_key_from_legacy("12/1/2")  # non-z14 never existed in the substrate


def test_tile_key_edge_tiles_do_not_collide():
    # y just below the 100k multiplier boundary vs x+1, y=0.
    assert tile_key_from_xy(0, 16383) != tile_key_from_xy(1, 0)
