"""GPX sport classification — `<trk><type>` → sport enum cascade.

Paul exported 1400 mixed-sport GPX from Strava across years and can no
longer recall which sport each one was. The fix uses a cascade:

  1. GPX `<trk><type>` if granular enough to map (Garmin Connect:
     ``mountain_biking``, ``gravel_cycling``, ``road_cycling`` …)
  2. Form-supplied ``sport`` (current behaviour) when the GPX type is
     generic (``cycling`` from Strava export) or absent.

These tests pin the parser-level classification and the endpoint
cascade. They don't hit the DB — pure unit coverage.
"""
from __future__ import annotations

import pytest

from app.services.gpx import classify_sport_from_gpx_type, parse_gpx

# ── classify_sport_from_gpx_type — pure mapping ────────────────────────

@pytest.mark.parametrize(
    "track_type,expected",
    [
        # Garmin Connect granular cycling
        ("mountain_biking", "mtb"),
        ("Mountain_Biking", "mtb"),  # case-insensitive
        ("mountain biking", "mtb"),
        ("gravel_cycling", "gravel"),
        ("gravel cycling", "gravel"),
        ("road_cycling", "road"),
        ("road cycling", "road"),
        ("cyclocross", "gravel"),
        # Garmin / Strava generic — defer to caller
        ("cycling", None),
        ("ride", None),
        ("CYCLING", None),
        # Running family
        ("running", "running"),
        ("trail_running", "running"),
        ("trail running", "running"),
        # `treadmill_running` no longer bucketed as running (PR #342) —
        # GPS is locked to start coords or absent, pinning the treadmill
        # as a fake "run" on the public heatmap. Now in GPX_TRACK_TYPE_SKIP.
        # The classifier returns None (caller should observe `skip_reason`).
        ("treadmill_running", None),
        ("run", "running"),
        ("hiking", "running"),
        ("walking", "running"),
        # Unknown / unmodelled — these now also live in GPX_TRACK_TYPE_SKIP
        # but the legacy classifier still returns None (same observable
        # result). The new `gpx_track_type_skip_or_sport` distinguishes
        # "skip" from "unknown / fall-through" via the second tuple element.
        ("swimming", None),
        ("kayaking", None),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_classify_sport_from_gpx_type(track_type, expected) -> None:
    assert classify_sport_from_gpx_type(track_type) == expected


def test_classify_strips_whitespace() -> None:
    """Some exporters pad the <type> with leading/trailing whitespace."""
    assert classify_sport_from_gpx_type("  mountain_biking  ") == "mtb"


# ── parse_gpx surfaces track_type + sport_from_gpx ─────────────────────

def _gpx(track_xml: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  {track_xml}
</gpx>""".encode()


def _gpx_with_type(track_type: str | None) -> bytes:
    type_tag = f"<type>{track_type}</type>" if track_type is not None else ""
    return _gpx(f"""<trk>
        <name>Test</name>
        {type_tag}
        <trkseg>
          <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
          <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
        </trkseg>
      </trk>""")


def test_parse_gpx_extracts_garmin_mtb_type() -> None:
    """Real Garmin Connect MTB export: <type>mountain_biking</type>."""
    out = parse_gpx(_gpx_with_type("mountain_biking"))
    assert out["track_type"] == "mountain_biking"
    assert out["sport_from_gpx"] == "mtb"


def test_parse_gpx_extracts_garmin_gravel_type() -> None:
    out = parse_gpx(_gpx_with_type("gravel_cycling"))
    assert out["track_type"] == "gravel_cycling"
    assert out["sport_from_gpx"] == "gravel"


def test_parse_gpx_strava_cycling_is_generic() -> None:
    """Strava bulk-export collapses every cycling variant to plain
    `cycling` — caller must fall back to form-supplied sport."""
    out = parse_gpx(_gpx_with_type("cycling"))
    assert out["track_type"] == "cycling"
    assert out["sport_from_gpx"] is None


def test_parse_gpx_no_type_tag() -> None:
    """Old / hand-rolled GPX without a <type> element."""
    out = parse_gpx(_gpx_with_type(None))
    assert out["track_type"] is None
    assert out["sport_from_gpx"] is None


def test_parse_gpx_unknown_type_returns_none_sport() -> None:
    """Swim / kayak / windsurf etc. — we don't model them; caller
    should fall back to form-supplied sport (probably 'road' default)."""
    out = parse_gpx(_gpx_with_type("swimming"))
    assert out["track_type"] == "swimming"
    assert out["sport_from_gpx"] is None
