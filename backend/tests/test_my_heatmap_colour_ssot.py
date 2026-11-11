"""« Ma heatmap » colour — one value, three places.

2026-11-11: the colour was retuned in the frontend (cyan → blue, unreadable at
regional zoom) and in the shared VECTOR style, but the shared RASTER tiles kept
the old cyan (``mvt_raster.LINE_RGB``) — a rider pasting the raster link into
OsmAnd / Locus got a different colour than on /map. Pin the three together.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api import shared_heatmap
from app.services import mvt_raster


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def test_shared_raster_and_vector_style_use_the_same_colour():
    assert _hex_to_rgb(shared_heatmap._COLOR) == mvt_raster.LINE_RGB


def test_backend_colour_matches_frontend_my_traces_color():
    constants = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "lib" / "constants.ts"
        if candidate.is_file():
            constants = candidate
            break
    if constants is None:
        pytest.skip("frontend/lib/constants.ts not mounted")
    m = re.search(r"MY_TRACES_COLOR\s*=\s*'(#[0-9a-fA-F]{6})'", constants.read_text())
    assert m, "MY_TRACES_COLOR not found in frontend/lib/constants.ts"
    assert m.group(1).lower() == shared_heatmap._COLOR.lower()
