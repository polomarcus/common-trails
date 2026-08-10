"""Tests for the GPX <trk><type> skip-out-of-scope policy.

The trap this closes: a user uploads `yoga.gpx` (recorded by their
watch as an indoor session) with `sport=mtb` selected in the import
modal. Pre-PR: the GPX <type> resolver returned None for "yoga"
because it wasn't in the map → cascade fell through to form-supplied
"mtb" → yoga GPS scribble ended up on the public MTB heatmap.

Post-PR: `GPX_TRACK_TYPE_SKIP` set contains explicit entries for
yoga / swim / ski / treadmill / virtual / indoor / etc. Detected at
parse time and stamped as `parsed["skip_reason"]`. Callers
(`gpx_upload.py` returns 422; `imports.py` counts as skipped and
continues) honour the marker without touching form-sport.

See [[feedback_skip_beats_pollute_heatmap]] for the design rule.
"""
from __future__ import annotations

from app.services.gpx import (
    GPX_TRACK_TYPE_SKIP,
    gpx_track_type_skip_or_sport,
)

# ── Direct classifier ────────────────────────────────────────────────


def test_known_cycling_types_resolve_to_sport() -> None:
    """Sanity — Garmin Connect granular types still map correctly."""
    sport, skip = gpx_track_type_skip_or_sport("mountain_biking")
    assert sport == "mtb" and skip is False

    sport, skip = gpx_track_type_skip_or_sport("gravel_cycling")
    assert sport == "gravel" and skip is False

    sport, skip = gpx_track_type_skip_or_sport("road_cycling")
    assert sport == "road" and skip is False


def test_generic_cycling_falls_through() -> None:
    """Strava-export generic `cycling` returns (None, False) — caller's
    form-supplied sport / CSV hint takes over. Same semantics as before."""
    sport, skip = gpx_track_type_skip_or_sport("cycling")
    assert sport is None and skip is False

    sport, skip = gpx_track_type_skip_or_sport("ride")
    assert sport is None and skip is False


def test_unknown_type_falls_through() -> None:
    """Future Strava/Garmin type we haven't seen → fall through, not skip."""
    sport, skip = gpx_track_type_skip_or_sport("future_sport_2030")
    assert sport is None and skip is False


def test_empty_and_none_inputs_fall_through() -> None:
    assert gpx_track_type_skip_or_sport(None) == (None, False)
    assert gpx_track_type_skip_or_sport("") == (None, False)
    assert gpx_track_type_skip_or_sport("   ") == (None, False)


def test_virtual_ride_is_explicit_skip() -> None:
    """The bug: Zwift virtual ride GPS uses fictional Watopia coords
    (~lat -11.6, lon 166.9). Pre-PR mapping to "road" would land that
    fake island on the public road heatmap."""
    sport, skip = gpx_track_type_skip_or_sport("virtual_ride")
    assert sport is None and skip is True

    sport, skip = gpx_track_type_skip_or_sport("Virtual Ride")  # case + space variant
    assert sport is None and skip is True


def test_treadmill_running_is_explicit_skip() -> None:
    """Treadmill GPS is locked to a single point or missing. Was
    bucketed as `running` previously — would pin a treadmill's start
    coordinates as a running route. Now skipped."""
    sport, skip = gpx_track_type_skip_or_sport("treadmill_running")
    assert sport is None and skip is True

    sport, skip = gpx_track_type_skip_or_sport("treadmill")
    assert sport is None and skip is True


def test_indoor_types_are_explicit_skip() -> None:
    for t in ("indoor_cycling", "indoor_running", "stationary_cycling", "trainer", "spinning"):
        sport, skip = gpx_track_type_skip_or_sport(t)
        assert sport is None and skip is True, f"{t} should be skip"


def test_water_sports_are_explicit_skip() -> None:
    """Map a kayak GPS as MTB → community ocean heatmap. No."""
    for t in ("swimming", "kayaking", "rowing", "stand_up_paddling", "surfing"):
        sport, skip = gpx_track_type_skip_or_sport(t)
        assert sport is None and skip is True, f"{t} should be skip"


def test_snow_sports_are_explicit_skip() -> None:
    for t in ("alpine_skiing", "cross_country_skiing", "snowboarding", "snowshoeing"):
        sport, skip = gpx_track_type_skip_or_sport(t)
        assert sport is None and skip is True, f"{t} should be skip"


def test_strength_studio_types_are_explicit_skip() -> None:
    for t in ("yoga", "pilates", "crossfit", "weight_training", "workout"):
        sport, skip = gpx_track_type_skip_or_sport(t)
        assert sport is None and skip is True, f"{t} should be skip"


def test_case_insensitive_and_whitespace_stripped() -> None:
    sport, skip = gpx_track_type_skip_or_sport("  YOGA  ")
    assert sport is None and skip is True

    sport, skip = gpx_track_type_skip_or_sport("Mountain_Biking")
    assert sport == "mtb" and skip is False


def test_skip_set_is_a_set() -> None:
    """Sanity — `GPX_TRACK_TYPE_SKIP` is a set (constant-time lookup
    matters now that this runs on every GPX parse)."""
    assert isinstance(GPX_TRACK_TYPE_SKIP, set)
    assert "yoga" in GPX_TRACK_TYPE_SKIP
    assert "virtual_ride" in GPX_TRACK_TYPE_SKIP


def test_parse_gpx_stamps_skip_reason_on_out_of_scope_type() -> None:
    """End-to-end: `parse_gpx` must stamp `skip_reason` on the parsed
    dict so `imports.py` + `gpx_upload.py` short-circuit before the
    sport cascade."""
    from app.services.gpx import parse_gpx

    yoga_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <name>morning_yoga</name>
    <type>yoga</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.501" lon="4.501"><ele>200</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""
    parsed = parse_gpx(yoga_gpx)
    assert "skip_reason" in parsed
    assert "yoga" in parsed["skip_reason"].lower()
    assert parsed.get("sport_from_gpx") is None


def test_parse_gpx_does_not_set_skip_reason_for_legit_type() -> None:
    """Real MTB ride must NOT get skip_reason stamped."""
    from app.services.gpx import parse_gpx

    mtb_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <type>mountain_biking</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.501" lon="4.501"><ele>210</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""
    parsed = parse_gpx(mtb_gpx)
    assert "skip_reason" not in parsed
    assert parsed.get("sport_from_gpx") == "mtb"


def test_motorcycling_is_skipped() -> None:
    """Heatmap-pollution review caught: motorcycle GPS at 90km/h
    uploaded with sport=road would skew popularity scoring on every
    road segment. Must be in skip set."""
    sport, skip = gpx_track_type_skip_or_sport("motorcycling")
    assert sport is None and skip is True
    sport, skip = gpx_track_type_skip_or_sport("scooter")
    assert sport is None and skip is True
    sport, skip = gpx_track_type_skip_or_sport("atv")
    assert sport is None and skip is True


def test_strava_export_underscore_variants_resolve() -> None:
    """Strava re-exports GPX with `<trk><type>` like `gravel_ride`,
    `e_bike_ride`, `mountain_bike_ride` (lowercased + underscored
    PascalCase). Without explicit entries they fall through to the
    form, defeating the auto-classification promise."""
    sport, skip = gpx_track_type_skip_or_sport("gravel_ride")
    assert sport == "gravel" and skip is False
    sport, skip = gpx_track_type_skip_or_sport("mountain_bike_ride")
    assert sport == "mtb" and skip is False
    sport, skip = gpx_track_type_skip_or_sport("e_bike_ride")
    assert sport == "road" and skip is False


def test_parse_gpx_skip_detail_includes_structured_code() -> None:
    """The 422 detail returned by /gpx/upload (and the parsed dict
    propagated through /imports/files) must include the structured
    `skip_code` enum so the frontend can localise without string-
    matching the human message."""
    from app.services.gpx import parse_gpx

    yoga_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <type>yoga</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.501" lon="4.501"><ele>200</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""
    parsed = parse_gpx(yoga_gpx)
    assert parsed.get("skip_code") == "GPX_TYPE_OUT_OF_SCOPE"
    assert parsed.get("skip_track_type") == "yoga"


def test_parse_gpx_sanitizes_track_type_for_logging() -> None:
    """A malformed GPX whose `<trk><type>` contains newlines or other
    control chars must NOT inject those into the error message
    (log-injection vector)."""
    from app.services.gpx import parse_gpx

    # `<type>yoga\n\rATTACKER_LINE</type>` — newline + carriage return.
    # If we naively used the raw value in the error string, downstream
    # log aggregation would see an apparently-separate log line.
    nasty_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <type>yoga
INJECTED</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.501" lon="4.501"><ele>200</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""
    parsed = parse_gpx(nasty_gpx)
    skip_type = parsed.get("skip_track_type", "")
    # Must not contain newlines / carriage returns / nulls.
    assert "\n" not in skip_type
    assert "\r" not in skip_type
    assert "\x00" not in skip_type


def test_imports_zip_counts_skipped_not_failed_for_yoga() -> None:
    """End-to-end on the ZIP path: a yoga GPX inside an import bundle
    increments `skipped`, NOT `failed`. The UI surfaces these counts
    so a 1400-file Strava bulk with 30 yoga GPXs must show
    `skipped: 30 + dedup_hits` not `failed: 30`."""
    import io
    import zipfile

    from app.services.gpx import parse_zip_of_gpx

    yoga_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t">
  <trk>
    <type>yoga</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.501" lon="4.501"><ele>200</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""
    mtb_gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t">
  <trk>
    <type>mountain_biking</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.51" lon="4.51"><ele>210</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("activities/yoga1.gpx", yoga_gpx)
        zf.writestr("activities/ride1.gpx", mtb_gpx)
        zf.writestr("activities/yoga2.gpx", yoga_gpx)
    results = parse_zip_of_gpx(buf.getvalue())

    skipped = [r for r in results if r.get("skip_reason")]
    normal = [r for r in results if "skip_reason" not in r and "error" not in r]
    assert len(skipped) == 2, f"expected 2 yoga skips, got {len(skipped)}"
    assert len(normal) == 1, f"expected 1 MTB through, got {len(normal)}"
    # Each yoga entry has its own per-member skip_reason — proves the
    # loop doesn't share state across members.
    assert all("yoga" in r["skip_reason"].lower() for r in skipped)
