"""Unit pins for the 0056 substrate slim: BIGINT tile keys, region
partitions, and the import guard.

All pure/mocked — no DB needed (the DB-level behaviour is covered by the
golden suite + the updated fixture tests).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.cli.import_osm_roads import (
    GUARD_MIN_FRACTION_OF_PREVIOUS,
    ImportGuardError,
    _run_import_guard,
    region_partition_name,
)
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


# ── region partition naming ────────────────────────────────────────────


def test_region_partition_name_maps_dashes():
    assert region_partition_name("italia-nord-ovest") == "osm_road_edges_italia_nord_ovest"
    assert region_partition_name("occitanie") == "osm_road_edges_occitanie"


@pytest.mark.parametrize("bad", ["", "UPPER", "a b", "a;drop table", "-lead", "é"])
def test_region_partition_name_rejects_unsafe(bad: str):
    # The name is inlined into DDL — the regex gate is the injection guard.
    with pytest.raises(ValueError):
        region_partition_name(bad)


# ── import guard ───────────────────────────────────────────────────────


def test_guard_fails_below_absolute_floor():
    db = MagicMock()
    with pytest.raises(ImportGuardError, match="floor"):
        _run_import_guard(db, "occitanie", inserted=5_000,
                          previous_count=None, min_rows=100_000)
    db.execute.assert_not_called()  # meta must NOT be recorded on failure


def test_guard_fails_below_fraction_of_previous():
    db = MagicMock()
    prev = 1_000_000
    inserted = int(prev * GUARD_MIN_FRACTION_OF_PREVIOUS) - 1
    with pytest.raises(ImportGuardError, match="previous import"):
        _run_import_guard(db, "occitanie", inserted=inserted,
                          previous_count=prev, min_rows=100_000)
    db.execute.assert_not_called()


def test_guard_records_meta_on_success():
    db = MagicMock()
    _run_import_guard(db, "occitanie", inserted=2_000_000,
                      previous_count=1_900_000, min_rows=100_000)
    assert db.execute.call_count == 1
    sql = str(db.execute.call_args.args[0])
    assert "INSERT INTO osm_import_meta" in sql
    assert "ON CONFLICT (region) DO UPDATE" in sql
    assert db.execute.call_args.args[1] == {"region": "occitanie", "count": 2_000_000}
    db.commit.assert_called_once()


def test_guard_first_import_has_no_previous_baseline():
    db = MagicMock()
    _run_import_guard(db, "corse", inserted=600_000,
                      previous_count=None, min_rows=100_000)
    db.commit.assert_called_once()
