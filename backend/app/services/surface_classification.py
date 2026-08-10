"""Surface classification — single source of truth for OSM tag → surface class.

Pure functions, zero I/O.  Maps raw OSM tags (surface, highway, tracktype,
smoothness) to one of five normalised surface classes used throughout the app.

## Public exports for the SSOT refactor (2026-05-15)

`SURFACE_NORMALIZE` is the *class-only* projection of `_SURFACE_MAP` (the
confidence value is dropped). It used to live duplicated in three places
(`ingest.py`, `api/graph_tiles.py`, `cli/import_osm_roads.py`) — drift
between those copies was a recurring bug ("an edge tagged 'asphalt' by
ingest looks like 'gravel' to the routing graph"). Now there's one map.

`normalize_surface(raw)` is the convenience function those modules used.
"""
from typing import Literal

SurfaceClass = Literal["asphalt", "gravel", "dirt", "rock", "unknown"]

# ── Rule A: explicit surface tag ─────────────────────────────────────────────
_SURFACE_MAP: dict[str, tuple[SurfaceClass, float]] = {
    # asphalt family
    "asphalt": ("asphalt", 1.0),
    "concrete": ("asphalt", 1.0),
    "concrete:plates": ("asphalt", 1.0),
    "concrete:lanes": ("asphalt", 1.0),
    "paving_stones": ("asphalt", 0.95),
    "sett": ("asphalt", 0.90),
    "metal": ("asphalt", 0.90),
    "paved": ("asphalt", 0.85),
    # gravel family
    "gravel": ("gravel", 1.0),
    "fine_gravel": ("gravel", 1.0),
    "compacted": ("gravel", 0.95),
    "pebblestone": ("gravel", 0.90),
    # dirt family
    "dirt": ("dirt", 1.0),
    "earth": ("dirt", 1.0),
    "ground": ("dirt", 1.0),
    "mud": ("dirt", 1.0),
    "sand": ("dirt", 0.95),
    "grass": ("dirt", 0.90),
    "woodchips": ("dirt", 0.85),
    "unpaved": ("dirt", 0.85),
    # rock family
    "rock": ("rock", 1.0),
    "stone": ("rock", 1.0),
    "cobblestone": ("rock", 0.95),
}

# ── Rule B: infer from highway type ──────────────────────────────────────────
_HIGHWAY_DEFAULT: dict[str, tuple[SurfaceClass, float]] = {
    "motorway": ("asphalt", 0.95),
    "motorway_link": ("asphalt", 0.95),
    "trunk": ("asphalt", 0.95),
    "trunk_link": ("asphalt", 0.95),
    "primary": ("asphalt", 0.90),
    "primary_link": ("asphalt", 0.90),
    "secondary": ("asphalt", 0.85),
    "secondary_link": ("asphalt", 0.85),
    "tertiary": ("asphalt", 0.80),
    "tertiary_link": ("asphalt", 0.80),
    "residential": ("asphalt", 0.75),
    "service": ("asphalt", 0.60),
    "unclassified": ("asphalt", 0.50),
    "cycleway": ("asphalt", 0.50),
    "track": ("gravel", 0.40),
    "path": ("dirt", 0.30),
    "bridleway": ("dirt", 0.35),
    "footway": ("dirt", 0.30),
}

# ── Rule B bis: tracktype overrides bare highway ─────────────────────────────
_TRACKTYPE_MAP: dict[str, tuple[SurfaceClass, float]] = {
    "grade1": ("asphalt", 0.70),
    "grade2": ("gravel", 0.65),
    "grade3": ("gravel", 0.50),
    "grade4": ("dirt", 0.50),
    "grade5": ("dirt", 0.40),
}

# ── Rule C: smoothness can only downgrade ────────────────────────────────────
_SURFACE_RANK = {"asphalt": 0, "gravel": 1, "dirt": 2, "rock": 3, "unknown": 4}

_SMOOTHNESS_DOWNGRADE: dict[str, SurfaceClass] = {
    "very_bad": "gravel",
    "horrible": "dirt",
    "very_horrible": "dirt",
    "impassable": "rock",
}


def classify_surface(tags: dict) -> tuple[SurfaceClass, float]:
    """Return (surface_class, confidence) from an OSM-like tags dict.

    Rules applied in order:
      A. Explicit ``surface`` tag (highest priority)
      B. ``tracktype`` (if present) or ``highway`` type
      C. ``smoothness`` can only downgrade the result, never promote
    """
    surface_val = (tags.get("surface") or "").strip().lower()
    highway_val = (tags.get("highway") or "").strip().lower()
    tracktype_val = (tags.get("tracktype") or "").strip().lower()
    smoothness_val = (tags.get("smoothness") or "").strip().lower()

    # Rule A — explicit surface tag
    if surface_val and surface_val in _SURFACE_MAP:
        cls, conf = _SURFACE_MAP[surface_val]
    # Rule B — tracktype takes priority over bare highway
    elif tracktype_val and tracktype_val in _TRACKTYPE_MAP:
        cls, conf = _TRACKTYPE_MAP[tracktype_val]
    elif highway_val and highway_val in _HIGHWAY_DEFAULT:
        cls, conf = _HIGHWAY_DEFAULT[highway_val]
    else:
        cls, conf = "unknown", 0.0

    # Rule C — smoothness only downgrades
    if smoothness_val in _SMOOTHNESS_DOWNGRADE:
        downgrade_cls = _SMOOTHNESS_DOWNGRADE[smoothness_val]
        if _SURFACE_RANK.get(downgrade_cls, 4) > _SURFACE_RANK.get(cls, 4):
            cls = downgrade_cls
            conf = min(conf, 0.6)

    return cls, round(conf, 2)


def classify_surface_simple(tags: dict) -> SurfaceClass:
    """Convenience: return surface class only (no confidence)."""
    cls, _ = classify_surface(tags)
    return cls


# ── SURFACE_NORMALIZE: class-only projection of _SURFACE_MAP ───────────────
#
# Derived at module load so it stays consistent with the source of truth.
# Imported by `ingest.py`, `api/graph_tiles.py`, `cli/import_osm_roads.py`
# (and pinned-equal by test_surface_normalize_ssot.py).

SURFACE_NORMALIZE: dict[str, str] = {raw: cls for raw, (cls, _) in _SURFACE_MAP.items()}


def normalize_surface(raw: str) -> str:
    """Map a raw OSM ``surface=*`` value to its normalised class.

    Returns ``'unknown'`` if the value is empty or not in the map.
    """
    return SURFACE_NORMALIZE.get(raw.lower(), "unknown") if raw else "unknown"
