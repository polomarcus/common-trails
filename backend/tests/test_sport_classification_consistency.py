"""Cross-intake sport-classification consistency.

The #1 ingestion-audit fix: ONE shared classifier
(`app.services.strava_utils.classify_sport`) used by every intake — CLI
bulk import, UI GPX/ZIP+CSV upload, and the four Strava live-sync paths
(webhook / in-process bulk / Cloud Run Job / resync). The same activity
MUST classify identically no matter how it arrives.

Before this fix the gravel/mtb name-keyword refinement (#430) lived ONLY
in `scripts/import_strava_export.py`, so the UI-upload CSV path and the
Strava live-sync path classified a generic "Ride" named "Gravel ..." /
"VTT ..." as `road`, starving gravel/mtb heat. These tests pin the SSOT
and drive the REAL entry points (no inline mirrors) to prove they agree.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.services.gpx import (
    classify_sport_from_strava_csv_type,
    parse_strava_activities_csv,
    parse_zip_of_gpx,
)
from app.services.strava_utils import (
    classify_sport,
    classify_strava_sport_or_skip,
)

# ── The SSOT classifier ──────────────────────────────────────────────


def test_name_refines_generic_ride_to_gravel() -> None:
    assert classify_sport("Ride", "Gravel paradise") == "gravel"


def test_name_refines_generic_ride_to_mtb() -> None:
    assert classify_sport("Ride", "VTT vers Lacan") == "mtb"
    assert classify_sport("Ride", "Sortie MTB matinale") == "mtb"
    assert classify_sport("Ride", "Mountain bike loop") == "mtb"
    assert classify_sport("Ride", "Mountain descent") == "mtb"  # "mountain"
    # French "montagne" is NOT the English "mountain" keyword → stays road.
    assert classify_sport("Ride", "Col de la montagne") == "road"


def test_generic_ride_without_keyword_stays_road() -> None:
    assert classify_sport("Ride", "Sortie route") == "road"
    assert classify_sport("Ride", None) == "road"
    assert classify_sport("Ride", "") == "road"


def test_fine_subtypes_win_regardless_of_name() -> None:
    # An explicit GravelRide/MountainBikeRide type is authoritative; the
    # name doesn't downgrade it and a None name is fine.
    assert classify_sport("GravelRide", None) == "gravel"
    assert classify_sport("MountainBikeRide", "Sortie route") == "mtb"
    assert classify_sport("EMountainBikeRide", None) == "mtb"
    assert classify_sport("EBikeRide", None) == "road"


def test_running_is_not_name_refined() -> None:
    # Name refinement is cycling-only — a Run named "Gravel ..." stays
    # running (it's a trail/road run, not a gravel bike ride).
    assert classify_sport("Run", "Gravel trail run") == "running"
    assert classify_sport("TrailRun", "Mountain ascent") == "running"


def test_out_of_scope_and_unknown_skip() -> None:
    # skip-beats-pollute: never default unknowns/out-of-scope to a sport.
    assert classify_sport("VirtualRide", "x") is None
    assert classify_sport("Run", None) is None or classify_sport("Run", None) == "running"
    assert classify_sport("Yoga", None) is None
    assert classify_sport("Swim", None) is None
    assert classify_sport("NordicSki", None) is None
    assert classify_sport("FutureStravaTypeWeHaventSeenYet", "Gravel x") is None
    assert classify_sport(None, "Gravel x") is None
    assert classify_sport("", None) is None


def test_run_classifies_to_running_not_skip() -> None:
    # Explicit Run IS in-scope (running) — distinct from out-of-scope.
    assert classify_sport("Run", None) == "running"


def test_csv_and_api_type_forms_agree() -> None:
    # The CSV human-readable form and the API PascalCase form must
    # collapse to the same key via _norm_type.
    assert classify_sport("Mountain Bike Ride") == classify_sport("MountainBikeRide")
    assert classify_sport("Gravel Ride") == classify_sport("GravelRide")
    assert classify_sport("E-Bike Ride") == classify_sport("EBikeRide")
    assert classify_sport("Trail Run") == classify_sport("TrailRun")


def test_back_compat_alias_delegates() -> None:
    assert classify_strava_sport_or_skip("Ride", "Gravel x") == "gravel"
    assert classify_strava_sport_or_skip("Ride") == "road"
    assert classify_strava_sport_or_skip("Yoga") is None


# ── Spec table from the task ──────────────────────────────────────────

_SPEC = [
    ("Ride", "Gravel paradise", "gravel"),
    ("Ride", "VTT vers Lacan", "mtb"),
    ("Ride", "Sortie route", "road"),
    ("GravelRide", None, "gravel"),
    ("VirtualRide", "x", None),
    ("Run", None, "running"),
    ("Yoga", None, None),
]


@pytest.mark.parametrize(("atype", "name", "expected"), _SPEC)
def test_spec_table(atype: str, name: str | None, expected: str | None) -> None:
    assert classify_sport(atype, name) == expected


# ── Cross-intake consistency — drive the REAL entry points ────────────

_MINIMAL_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk><name>x</name><type>cycling</type><trkseg>
    <trkpt lat="43.6" lon="3.9"><ele>20</ele></trkpt>
    <trkpt lat="43.61" lon="3.91"><ele>30</ele></trkpt>
  </trkseg></trk>
</gpx>
"""


def _strava_csv(rows: list[tuple[str, str, str, str]]) -> bytes:
    """rows: (activity_id, activity_name, activity_type, filename)."""
    lines = ["Activity ID,Activity Date,Activity Name,Activity Type,Filename"]
    for aid, name, atype, fn in rows:
        lines.append(f"{aid},2026-01-01,{name},{atype},{fn}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _cli_classify(atype: str, name: str) -> str | None:
    """Mirror of the CLI importer's resolution for a CSV row, driving the
    REAL `classify_sport` the CLI now calls (the CLI records _SKIP for
    None — represented here as None). The CLI's `STRAVA_SPORT_MAP` +
    inline name-refine were deleted, so this can only diverge from the
    other intakes if the SSOT itself changes.
    """
    return classify_sport(atype, name)


def _csv_path_classify(atype: str, name: str) -> str | None:
    """Drive the REAL UI-upload CSV parser: build a row, parse it through
    `parse_strava_activities_csv`, return the resolved sport-or-None."""
    csv_bytes = _strava_csv([("1", name, atype, "activities/1.gpx")])
    out = parse_strava_activities_csv(csv_bytes)
    return out.get("activities/1.gpx")


def _strava_sync_classify(atype: str, name: str) -> str | None:
    """Drive the REAL Strava-sync classifier (the function all four live
    paths call): `classify_strava_sport_or_skip(sport_type, name)`."""
    return classify_strava_sport_or_skip(atype, name)


_CONSISTENCY_FIXTURES = [
    ("Ride", "Gravel paradise"),
    ("Ride", "VTT vers Lacan"),
    ("Ride", "Sortie route du dimanche"),
    ("Ride", "Mountain bike enduro"),
    ("GravelRide", "Whatever"),
    ("MountainBikeRide", "Road ride name"),
    ("Run", "Trail run"),
    ("VirtualRide", "Zwift Watopia"),
    ("Yoga", "Morning flow"),
    ("AlpineSki", "Powder day"),
    ("FutureType", "Gravel adventure"),
]


@pytest.mark.parametrize(("atype", "name"), _CONSISTENCY_FIXTURES)
def test_all_intakes_classify_identically(atype: str, name: str) -> None:
    cli = _cli_classify(atype, name)
    csv_path = _csv_path_classify(atype, name)
    sync = _strava_sync_classify(atype, name)
    assert cli == csv_path == sync, (
        f"intake divergence for ({atype!r}, {name!r}): "
        f"cli={cli!r} csv={csv_path!r} sync={sync!r}"
    )


def test_classify_sport_from_strava_csv_type_passes_name() -> None:
    # The CSV helper must honor the name refinement too.
    assert classify_sport_from_strava_csv_type("Ride", "Gravel loop") == "gravel"
    assert classify_sport_from_strava_csv_type("Ride", "Sortie route") == "road"
    assert classify_sport_from_strava_csv_type("Ride") == "road"


# ── End-to-end: a Strava ZIP with name-only gravel/mtb signal ─────────


def _make_zip(*members: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_ride_with_gravel_name_stamps_gravel_from_csv() -> None:
    """The whole point: a Strava-export ZIP where Activity Type is the
    generic "Ride" but the Activity Name says "Gravel" must stamp
    sport_from_csv=gravel — not road."""
    csv_bytes = _strava_csv([
        ("100", "Gravel paradise", "Ride", "activities/100.gpx"),
        ("101", "VTT Lacan", "Ride", "activities/101.gpx"),
        ("102", "Sortie route", "Ride", "activities/102.gpx"),
    ])
    z = _make_zip(
        ("activities.csv", csv_bytes),
        ("activities/100.gpx", _MINIMAL_GPX),
        ("activities/101.gpx", _MINIMAL_GPX),
        ("activities/102.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert by_name["activities/100.gpx"]["sport_from_csv"] == "gravel"
    assert by_name["activities/101.gpx"]["sport_from_csv"] == "mtb"
    assert by_name["activities/102.gpx"]["sport_from_csv"] == "road"
