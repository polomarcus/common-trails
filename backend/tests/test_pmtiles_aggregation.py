"""Regression test for the by-OSM-way aggregation's pass/direction counts.

Background (2026-05-10): the heatmap display aggregates heat_edges by
``(osm_way_id, sport)``. The popular hover tooltip on the map shows
``pass_count`` for the segment under the cursor.

Before 2026-05-10, the aggregation used ``SUM(pass_count)``. For an OSM
way with ~100 11-m sub-edges (e.g. a popular running path), the sum
inflated to 300k+ "passages" — visible in the UI as
"412127 passages" when the underlying max real pass_count was ~286.

The fix changed it to ``MAX(pass_count)``: represents "the busiest
sub-segment", a stable interpretable number.

Since June 2026 the aggregation SQL lives in ONE place — the shared builder
``app/services/heat_aggregation.py::build_heat_aggregation_sql`` — used by
both the static PMTiles export and the live MVT tile endpoint. This test
pins the MAX semantic there. If a future refactor reverts to SUM or AVG in
the ``osm_grouped`` CTE, this test fails (and BOTH display paths would have
regressed in lockstep — which is exactly the point of the single source of
truth). The real execute-and-assert end-to-end check lives in
``test_pmtiles_kanon_export.py`` (PMTiles) and
``test_heat_aggregation_builder.py`` (the live path).
"""
from __future__ import annotations

import re

import pytest

from app.services.heat_aggregation import build_heat_aggregation_sql


@pytest.fixture(scope="module")
def osm_grouped_cte_body() -> str:
    """The text of the ``osm_grouped`` CTE in the shared builder's output."""
    sql = build_heat_aggregation_sql()
    m = re.search(r"osm_grouped AS \(\s*(.+?)\)\s*,\s*osm_matched", sql, re.DOTALL)
    assert m is not None, "could not locate osm_grouped CTE in the shared builder"
    return m.group(1)


def test_pass_count_uses_max_not_sum_in_osm_grouped_cte(osm_grouped_cte_body: str) -> None:
    """The osm_grouped CTE must aggregate pass_count via MAX, not SUM.

    A SUM would inflate the displayed pass_count proportional to the
    number of sub-edges per OSM way (one per ~11 m). For a popular path
    with 100+ sub-edges, this produces nonsense numbers like 412k.
    """
    assert re.search(r"MAX\s*\(\s*pass_count\s*\)", osm_grouped_cte_body), (
        f"pass_count aggregation in osm_grouped is not MAX. CTE body:\n"
        f"{osm_grouped_cte_body[:600]}"
    )
    # Defensively, refuse SUM(pass_count). If a future commit reverts,
    # this catches it before the misleading number ships to prod.
    assert not re.search(r"SUM\s*\(\s*pass_count\s*\)", osm_grouped_cte_body), (
        "SUM(pass_count) reintroduced in osm_grouped — see test docstring "
        "for why this inflates displayed passages"
    )


def test_forward_backward_count_also_max_not_sum(osm_grouped_cte_body: str) -> None:
    """forward_count / backward_count follow the same MAX semantic for
    consistency with pass_count. SUMming direction counts across
    sub-edges of a single OSM way doesn't have a meaningful tooltip
    interpretation."""
    assert re.search(r"MAX\s*\(\s*forward_count\s*\)", osm_grouped_cte_body), (
        "forward_count aggregation should be MAX, not SUM"
    )
    assert re.search(r"MAX\s*\(\s*backward_count\s*\)", osm_grouped_cte_body), (
        "backward_count aggregation should be MAX, not SUM"
    )
