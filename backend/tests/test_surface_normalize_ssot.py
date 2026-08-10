"""`_SURFACE_NORMALIZE` consistency across modules — SSOT drift guard.

Two modules carry an identical OSM-surface → normalized-class mapping:
- `app/services/ingest.py` — used during heat_edge ingest to tag each
  matched edge with surface_type.
- `app/cli/import_osm_roads.py` — used during bulk OSM PBF import to
  write `osm_road_edges.surface`.

(The third copy in `app/api/graph_tiles.py` was removed with the routing
subsystem decommission — chore/decommission-wasm-routing.)

If these drift apart, you get the **drift trap**: an edge tagged
`'asphalt'` by ingest looks like `'gravel'` to the routing graph,
producing weird cost results no one can repro. These tests pin them
identical at import time.

Same idea for `_normalize_highway` / `HIGHWAY_KNOWN`.

Not testing `surface_classification.py`'s `_SURFACE_MAP` here — that
one carries confidence weights for ML-style classification, a
different concern.
"""
from __future__ import annotations

import pytest

# ── SURFACE_NORMALIZE: three copies must be identical ─────────────────

def _get_ingest_surface() -> dict[str, str]:
    from app.services import ingest
    return ingest._SURFACE_NORMALIZE


def _get_import_osm_surface() -> dict[str, str]:
    from app.cli import import_osm_roads
    return import_osm_roads.SURFACE_NORMALIZE


def test_ingest_and_import_osm_surface_maps_match() -> None:
    """ingest._SURFACE_NORMALIZE == import_osm_roads.SURFACE_NORMALIZE."""
    ingest_map = _get_ingest_surface()
    osm_map = _get_import_osm_surface()
    assert ingest_map == osm_map, (
        "ingest._SURFACE_NORMALIZE has drifted from "
        "import_osm_roads.SURFACE_NORMALIZE. "
        f"In ingest only: {set(ingest_map.items()) - set(osm_map.items())}. "
        f"In import_osm_roads only: {set(osm_map.items()) - set(ingest_map.items())}."
    )


# ── HIGHWAY normalization: same idea ──────────────────────────────────

def test_normalize_highway_outputs_match_across_modules() -> None:
    """Run the OSM-canonical highway tags through both normalizers
    and assert identical outputs. Tags come from OpenStreetMap's
    routable-highway taxonomy."""
    osm_tags = [
        "residential", "tertiary", "tertiary_link", "secondary",
        "secondary_link", "primary", "primary_link", "unclassified",
        "track", "path", "cycleway", "footway", "bridleway",
        "living_street", "pedestrian", "service",
        "motorway", "trunk",  # NOT routable but should normalize to "unknown"
        "",  # empty → "unknown"
        "weird_tag",  # unknown → "unknown"
    ]
    from app.cli.import_osm_roads import normalize_highway as osm_norm
    from app.services.ingest import _normalize_highway as ingest_norm

    mismatches = []
    for tag in osm_tags:
        o = osm_norm(tag)
        i = ingest_norm(tag)
        if o != i:
            mismatches.append((tag, {"osm": o, "ingest": i}))
    assert not mismatches, (
        f"normalize_highway diverged across modules for {len(mismatches)} tag(s):\n"
        + "\n".join(f"  {tag}: {result}" for tag, result in mismatches)
    )


# ── normalize_surface output parity ────────────────────────────────────

def test_normalize_surface_outputs_match_across_modules() -> None:
    """Run typical OSM surface values through all three normalizers
    and assert identical outputs."""
    osm_tags = [
        "asphalt", "paved", "concrete", "concrete:plates", "sett",
        "paving_stones", "gravel", "compacted", "fine_gravel",
        "pebblestone", "dirt", "earth", "mud", "sand", "ground",
        "grass", "rock", "stone", "cobblestone",
        "",  # → "unknown"
        "wood", "metal_grid",  # weird → "unknown"
    ]
    from app.cli.import_osm_roads import normalize_surface as osm_norm
    from app.services.ingest import _normalize_surface as ingest_norm

    mismatches = []
    for tag in osm_tags:
        o = osm_norm(tag)
        i = ingest_norm(tag)
        if o != i:
            mismatches.append((tag, {"osm": o, "ingest": i}))
    assert not mismatches, (
        f"normalize_surface diverged across modules for {len(mismatches)} tag(s):\n"
        + "\n".join(f"  {tag}: {result}" for tag, result in mismatches)
    )


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
