"""Streaming OSM road import — memory and correctness.

The original `RoadSegmentCollector` accumulated every segment in a
Python list before INSERTing → OOM at ~16 GB on france-south.osm.pbf
(2.5 GB PBF → 50M segments × WKT strings). The new
`StreamingRoadCollector` buffers up to `batch_size` segments then
flushes; total memory stays bounded.

Since migration 0056 the collector targets the slim substrate:
- BIGINT tile keys (``app.services.tile_keys``),
- segments → the region's `osm_road_edges` partition,
- full-way polylines → the `osm_ways` side-table (second COPY + upsert
  per flush),
- boundary-tile clearing scoped to OTHER regions (own partition is
  TRUNCATEd by run_import).

These tests verify the streaming behavior at the unit level — no real
PBF needed. We poke `.buffer` / `.way_buffer` directly and assert that
`_flush()` does the right thing with the DB session.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.cli.import_osm_roads import StreamingRoadCollector

_TK_A = 824605961  # x=8246 y=5961
_TK_B = 824705961
_TK_C = 824805961


def _make_segment(
    tile_key: int = _TK_A,
    way_id: int = 1,
    idx: int = 0,
    with_elevation: bool = True,
    surface_confidence: float = 1.0,
    bridge_yes: bool = False,
    tunnel_yes: bool = False,
) -> tuple:
    """Build a fake segment tuple matching the producer in `way()`.

    Buffer layout (16 columns):
    - tile_key (BIGINT), way_id, idx, surface, highway (5)
    - lon1, lat1, lon2, lat2 (4)
    - ele_start_m, ele_end_m, ele_delta_m, slope_grade (4 — NULL when
      no HGT tile available, PR #259)
    - surface_confidence (1 — always set, PR #259)
    - bridge_yes, tunnel_yes (2 — added 2026-05-31 for the
      critical-connectors layer)

    The full-way WKT moved out of the segment tuple to
    ``collector.way_buffer`` (osm_ways side-table, migration 0056).
    """
    return (
        tile_key, way_id, idx, "asphalt", "residential",
        3.87, 43.61, 3.88, 43.62,  # lon1, lat1, lon2, lat2
        (120.5 if with_elevation else None),     # ele_start_m
        (135.2 if with_elevation else None),     # ele_end_m
        (14.7 if with_elevation else None),      # ele_delta_m
        (1.234 if with_elevation else None),     # slope_grade pct
        surface_confidence,                      # surface_confidence
        bridge_yes,                              # bridge_yes
        tunnel_yes,                              # tunnel_yes
    )


def _segments_copy_call(cur):
    """The COPY into _osm_stage (segments); the ways COPY is separate."""
    calls = [c for c in cur.copy_expert.call_args_list
             if "COPY _osm_stage" in str(c.args[0])]
    assert len(calls) == 1, f"expected exactly one segments COPY, got {cur.copy_expert.call_args_list}"
    return calls[0]


# ── _flush: empty buffer is a no-op ────────────────────────────────────

def test_flush_empty_buffer_does_nothing() -> None:
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c._flush()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


# ── _flush: clears tiles, then COPY into staging, then INSERT ──────────

def test_flush_clears_tiles_then_copies_then_inserts() -> None:
    """First flush for new tiles → DELETE on session + COPY+INSERT
    via raw cursor (segments partition + osm_ways upsert)."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.buffer = [_make_segment(tile_key=_TK_A), _make_segment(tile_key=_TK_B)]
    c.way_buffer = {1: "LINESTRING(3.87 43.61,3.88 43.62)"}
    c._flush()

    # Session-level execute is only used for the DELETE
    session_calls = db.execute.call_args_list
    assert len(session_calls) == 1, (
        f"Expected 1 session.execute (DELETE only), got {len(session_calls)}"
    )
    delete_sql = str(session_calls[0][0][0])
    assert "DELETE" in delete_sql
    # Boundary clearing must NEVER touch our own region's fresh rows.
    assert "region <>" in delete_sql, (
        "tile clearing must be scoped to OTHER regions (own partition is TRUNCATEd)"
    )
    assert session_calls[0][0][1]["region"] == "testreg"

    # The COPY + INSERT happen on the raw psycopg cursor
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    cur_execute_calls = [str(call.args[0]) for call in cur.execute.call_args_list]
    assert any("CREATE TEMP TABLE _osm_stage" in s for s in cur_execute_calls), \
        f"Expected CREATE TEMP TABLE in cursor.execute calls, got: {cur_execute_calls}"
    assert any("INSERT INTO osm_road_edges" in s for s in cur_execute_calls), \
        f"Expected INSERT INTO osm_road_edges in cursor.execute, got: {cur_execute_calls}"
    assert any("INSERT INTO osm_ways" in s and "ON CONFLICT (osm_way_id)" in s
               for s in cur_execute_calls), \
        f"Expected osm_ways upsert in cursor.execute, got: {cur_execute_calls}"
    copy_sqls = [str(call.args[0]) for call in cur.copy_expert.call_args_list]
    assert any("COPY _osm_stage" in s and "FROM STDIN" in s for s in copy_sqls)
    assert any("COPY _osm_ways_stage" in s and "FROM STDIN" in s for s in copy_sqls)

    db.commit.assert_called_once()
    assert c.buffer == []
    assert c.way_buffer == {}
    assert c.cleared_tiles == {_TK_A, _TK_B}
    assert c.inserted_total == 2


def test_flush_inserts_with_region_param() -> None:
    """The segments INSERT..SELECT carries the region as a bind param —
    that's what routes rows into the region's LIST partition."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="occitanie", batch_size=10)
    c.buffer = [_make_segment()]
    c._flush()
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    insert_calls = [call for call in cur.execute.call_args_list
                    if "INSERT INTO osm_road_edges" in str(call.args[0])]
    assert len(insert_calls) == 1
    assert insert_calls[0].args[1] == ("occitanie",)


# ── _flush: cleared_tiles is sticky — don't double-DELETE ──────────────

def test_flush_skips_already_cleared_tiles() -> None:
    """A second flush for an already-cleared tile skips the DELETE."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.cleared_tiles = {_TK_A}  # pre-cleared

    c.buffer = [_make_segment(tile_key=_TK_A)]
    c._flush()

    # No DELETE went out on the session; only COPY+INSERT on raw cursor
    session_calls = db.execute.call_args_list
    assert len(session_calls) == 0, (
        f"Expected 0 session.execute (no DELETE), got {len(session_calls)}"
    )
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    _segments_copy_call(cur)


def test_flush_clears_new_tiles_but_not_old() -> None:
    """Mix of new + already-cleared tiles → DELETE only the new ones."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.cleared_tiles = {_TK_A}

    c.buffer = [
        _make_segment(tile_key=_TK_A),  # already cleared
        _make_segment(tile_key=_TK_B),  # new
        _make_segment(tile_key=_TK_C),  # new
    ]
    c._flush()

    session_calls = db.execute.call_args_list
    assert len(session_calls) == 1, (
        f"Expected 1 DELETE, got {len(session_calls)}"
    )
    delete_keys = session_calls[0][0][1]["keys"]
    assert set(delete_keys) == {_TK_B, _TK_C}, (
        f"DELETE should target only new tiles, got {delete_keys}"
    )


# ── Buffer-size invariant ──────────────────────────────────────────────

def test_buffer_size_never_exceeds_batch_size_after_flush() -> None:
    """After _flush(), buffer is always empty. This is the memory
    bound that prevents OOM on large PBFs."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=100)
    # Fill buffer over capacity (a real run would call _flush() automatically
    # via way() — this test just verifies _flush always drains)
    c.buffer = [_make_segment() for _ in range(500)]
    c._flush()
    assert len(c.buffer) == 0


# ── DELETE chunking ────────────────────────────────────────────────────

def test_flush_chunks_delete_when_many_tiles() -> None:
    """DELETE keys list is chunked at 500 to keep bind-parameter count
    bounded — otherwise psycopg can refuse very large IN-lists."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=1500)
    c.buffer = [_make_segment(tile_key=100000 + i) for i in range(1500)]
    c._flush()

    delete_calls = [call for call in db.execute.call_args_list
                    if "DELETE" in str(call[0][0])]
    assert len(delete_calls) == 3, (
        f"Expected 3 chunked DELETE calls (1500 keys / 500 per chunk), got {len(delete_calls)}"
    )
    # One segments COPY for the whole buffer
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    _segments_copy_call(cur)


# ── finalize() flushes remaining buffer ────────────────────────────────

def test_finalize_flushes_remaining_buffer() -> None:
    """End-of-parse `.finalize()` flushes a partial buffer."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=100)
    c.buffer = [_make_segment() for _ in range(5)]  # partial
    c.finalize()
    db.execute.assert_called()
    assert c.buffer == []
    assert c.inserted_total == 5


def test_finalize_with_empty_buffer_is_safe() -> None:
    """Finalize after the last way() callback already flushed."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=100)
    c.finalize()
    db.execute.assert_not_called()


# ── Initial state ──────────────────────────────────────────────────────

def test_initial_state_is_empty() -> None:
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=50_000)
    assert c.buffer == []
    assert c.way_buffer == {}
    assert c.cleared_tiles == set()
    assert c.inserted_total == 0
    assert c.way_count == 0
    assert c.skipped == 0
    assert c.batch_size == 50_000
    assert c.region == "testreg"


# ── DEM columns: NULL when no HGT tile available ───────────────────────

def test_flush_serializes_null_elevation_as_backslash_N() -> None:
    """Segments with no DEM data → TSV emits ``\\N`` for the 4 ele columns;
    surface_confidence is always set."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.buffer = [_make_segment(with_elevation=False, surface_confidence=0.40)]
    c._flush()
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    tsv_stream = _segments_copy_call(cur).args[1]
    tsv_text = tsv_stream.getvalue() if hasattr(tsv_stream, "getvalue") else ""
    line = tsv_text.rstrip("\n")
    fields = line.split("\t")
    # Columns -7..-3: ele_start, ele_end, ele_delta, slope, surf_conf
    # Columns -2..-1: bridge_yes, tunnel_yes (defaults false → "f")
    assert fields[-7:] == [r"\N", r"\N", r"\N", r"\N", "0.40", "f", "f"], (
        f"Last 7 TSV fields should be 4 NULL markers + surf_conf + bridge_yes + tunnel_yes; got {fields[-7:]}"
    )


def test_flush_serializes_real_elevation_as_floats() -> None:
    """Segments with DEM data → TSV emits decimal strings."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.buffer = [_make_segment(with_elevation=True, surface_confidence=0.85)]
    c._flush()
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    tsv_stream = _segments_copy_call(cur).args[1]
    tsv_text = tsv_stream.getvalue() if hasattr(tsv_stream, "getvalue") else ""
    line = tsv_text.rstrip("\n")
    fields = line.split("\t")
    assert fields[-7:] == ["120.50", "135.20", "14.70", "1.234", "0.85", "f", "f"], (
        f"Expected formatted ele/slope/surf_conf + bridge_yes + tunnel_yes, got {fields[-7:]}"
    )


def test_flush_serializes_bridge_and_tunnel_flags() -> None:
    """bridge_yes/tunnel_yes from OSM tags → TSV ``t``/``f`` for the
    boolean COPY columns added 2026-05-31."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.buffer = [
        _make_segment(way_id=1, bridge_yes=True, tunnel_yes=False),
        _make_segment(way_id=2, bridge_yes=False, tunnel_yes=True),
        _make_segment(way_id=3, bridge_yes=True, tunnel_yes=True),
        _make_segment(way_id=4, bridge_yes=False, tunnel_yes=False),
    ]
    c._flush()
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    tsv_stream = _segments_copy_call(cur).args[1]
    tsv_text = tsv_stream.getvalue() if hasattr(tsv_stream, "getvalue") else ""
    lines = [line for line in tsv_text.rstrip("\n").split("\n") if line]
    assert len(lines) == 4
    pairs = [tuple(line.split("\t")[-2:]) for line in lines]
    assert pairs == [("t", "f"), ("f", "t"), ("t", "t"), ("f", "f")], (
        f"Expected bridge/tunnel flags to round-trip as t/f; got {pairs}"
    )


# ── osm_ways side-table COPY ───────────────────────────────────────────

def test_flush_ways_tsv_one_line_per_way() -> None:
    """The osm_ways staging COPY carries one line per unique way."""
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=10)
    c.buffer = [_make_segment(way_id=1), _make_segment(way_id=1, idx=1),
                _make_segment(way_id=2)]
    c.way_buffer = {1: "LINESTRING(0 0,1 1)", 2: "LINESTRING(1 1,2 2)"}
    c._flush()
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    ways_calls = [call for call in cur.copy_expert.call_args_list
                  if "COPY _osm_ways_stage" in str(call.args[0])]
    assert len(ways_calls) == 1
    tsv_stream = ways_calls[0].args[1]
    tsv_text = tsv_stream.getvalue() if hasattr(tsv_stream, "getvalue") else ""
    lines = [line for line in tsv_text.rstrip("\n").split("\n") if line]
    assert len(lines) == 2, f"one TSV line per unique way, got: {lines}"
    assert lines[0].split("\t") == ["1", "LINESTRING(0 0,1 1)"]


@pytest.mark.parametrize("batch_size", [10_000, 50_000, 100_000])
def test_flush_handles_large_batches(batch_size: int) -> None:
    """Build a buffer at full batch_size and flush — verify it doesn't
    explode and the TSV stream is single-pass.

    With COPY, the param-count concern of INSERT VALUES is gone: every
    batch becomes one TSV stream regardless of size. The cap is just
    memory (TSV string).
    """
    db = MagicMock()
    c = StreamingRoadCollector(db, region="testreg", batch_size=batch_size)
    c.buffer = [_make_segment(way_id=i) for i in range(batch_size)]
    c._flush()
    assert c.inserted_total == batch_size
    assert c.buffer == []
    raw_conn = db.connection().connection
    cur = raw_conn.cursor()
    # Exactly one segments COPY for the whole batch, regardless of size
    tsv_stream = _segments_copy_call(cur).args[1]
    tsv_text = tsv_stream.getvalue() if hasattr(tsv_stream, "getvalue") else ""
    if tsv_text:
        assert tsv_text.count("\n") == batch_size, (
            f"Expected {batch_size} TSV lines, got {tsv_text.count(chr(10))}"
        )
