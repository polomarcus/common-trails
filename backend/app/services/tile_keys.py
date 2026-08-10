"""SSOT for the BIGINT z14 tile key stored in ``osm_road_edges.tile_key``.

Migration 0056 changed ``tile_key`` from TEXT ``"14/x/y"`` to BIGINT.
Every producer (PBF import, Overpass runtime fetch, fixtures) and every
consumer (HMM tile prewarm, bbox graph builders, tests) must build keys
through these helpers so the encoding never drifts.

Encoding: ``x * 100_000 + y`` with z fixed at 14 (the only zoom the
substrate has ever used — the graph tile endpoints reject z != 14).
``y < 2**14 = 16384 < 100_000`` so the encoding is collision-free, and
the decimal form stays human-readable in psql: tile ``14/8452/5882``
becomes ``845205882``.
"""
from __future__ import annotations

import math

Z14 = 14
_N = 1 << Z14  # 16384 tiles per axis at z14
_Y_BASE = 100_000  # decimal-readable multiplier; must stay > _N


def tile_key_from_xy(x: int, y: int) -> int:
    """Encode z14 slippy-map (x, y) into the BIGINT tile key."""
    return x * _Y_BASE + y


def tile_key_to_xy(key: int) -> tuple[int, int]:
    """Decode the BIGINT tile key back into z14 (x, y)."""
    return divmod(key, _Y_BASE)


def latlon_to_tile_xy(lat: float, lon: float) -> tuple[int, int]:
    """(lat, lon) → z14 slippy-map (x, y), clamped to valid tile range."""
    lon = max(-180.0, min(180.0, lon))
    lat = max(-85.05, min(85.05, lat))
    x = max(0, min(_N - 1, int((lon + 180.0) / 360.0 * _N)))
    y = max(0, min(_N - 1, int(
        (1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi)
        / 2.0 * _N
    )))
    return x, y


def tile_key_from_latlon(lat: float, lon: float) -> int:
    """(lat, lon) → BIGINT z14 tile key."""
    x, y = latlon_to_tile_xy(lat, lon)
    return tile_key_from_xy(x, y)


def tile_key_from_legacy(key: str | int) -> int:
    """Accept a legacy TEXT ``"14/x/y"`` key (pre-0056 fixtures) or an int."""
    if isinstance(key, int):
        return key
    z, x, y = (int(p) for p in key.split("/"))
    if z != Z14:
        raise ValueError(f"only z14 tile keys are supported, got {key!r}")
    return tile_key_from_xy(x, y)
