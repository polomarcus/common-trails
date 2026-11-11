"""Canonical surface / highway classes produced by the ingest normalizers.

`app/services/ingest.py` normalizes raw OSM `surface` / `highway` tags into
a small set of canonical classes. (The cross-module drift guards against
`app/cli/import_osm_roads.py` left with that CLI — ingest is now the only
consumer of the shared maps in `surface_classification.py`.)
"""
from __future__ import annotations

import pytest

# ── Canonical classes pinned ──────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("asphalt", "asphalt"),
    ("paved", "asphalt"),
    ("concrete", "asphalt"),
    ("gravel", "gravel"),
    ("compacted", "gravel"),
    ("fine_gravel", "gravel"),
    ("dirt", "dirt"),
    ("earth", "dirt"),
    ("rock", "rock"),
    ("", "unknown"),
    ("unrecognized_value", "unknown"),
])
def test_surface_canonical_values_stable(raw: str, expected: str) -> None:
    """Canonical surface classes (asphalt/gravel/dirt/rock/unknown)
    are the API contract for the frontend's surface overlay. Changing
    one of these renames is a frontend-visible breaking change —
    handle in same PR or break the UI."""
    from app.services.ingest import _normalize_surface
    assert _normalize_surface(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("residential", "residential"),
    ("tertiary", "tertiary"),
    ("tertiary_link", "tertiary"),
    ("primary_link", "primary"),
    ("track", "track"),
    ("path", "path"),
    ("footway", "path"),
    ("bridleway", "path"),
    ("pedestrian", "path"),
    ("living_street", "path"),
    ("service", "service"),
    ("motorway", "unknown"),
    ("trunk", "unknown"),
    ("", "unknown"),
])
def test_highway_canonical_values_stable(raw: str, expected: str) -> None:
    """Highway normalization classes drive the routing cost model.
    Renames are observable in route quality scores; treat as a contract."""
    from app.services.ingest import _normalize_highway
    assert _normalize_highway(raw) == expected
