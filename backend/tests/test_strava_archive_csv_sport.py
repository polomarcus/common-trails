"""Tests for `activities.csv` sport inference on Strava ZIP archives.

Covers `parse_strava_activities_csv` directly + the end-to-end behavior
through `parse_zip_of_gpx` (which is what `/imports/files` calls).

Goal: prove the cascade in `imports.py` picks the CSV-derived sport
above the in-GPX hint, so a user uploading a Strava archive doesn't
need to touch the sport selector to get the right per-file sport.
"""
from __future__ import annotations

import io
import zipfile

from app.services.gpx import (
    classify_sport_from_strava_csv_type,
    parse_strava_activities_csv,
    parse_zip_of_gpx,
)

# ── classify_sport_from_strava_csv_type ──────────────────────────────


def test_classify_known_cycling_types() -> None:
    assert classify_sport_from_strava_csv_type("Mountain Bike Ride") == "mtb"
    assert classify_sport_from_strava_csv_type("Gravel Ride") == "gravel"
    assert classify_sport_from_strava_csv_type("Ride") == "road"
    assert classify_sport_from_strava_csv_type("E-Bike Ride") == "road"


def test_classify_running_family() -> None:
    assert classify_sport_from_strava_csv_type("Run") == "running"
    assert classify_sport_from_strava_csv_type("Trail Run") == "running"
    assert classify_sport_from_strava_csv_type("Hike") == "running"
    assert classify_sport_from_strava_csv_type("Walk") == "running"


def test_classify_case_insensitive_and_whitespace() -> None:
    assert classify_sport_from_strava_csv_type("  mountain bike ride  ") == "mtb"
    assert classify_sport_from_strava_csv_type("RIDE") == "road"


def test_classify_out_of_scope_returns_none() -> None:
    # Swim/ski/yoga are real Strava values but we don't model them — let
    # the cascade fall through to form-supplied sport.
    assert classify_sport_from_strava_csv_type("Swim") is None
    assert classify_sport_from_strava_csv_type("Alpine Ski") is None
    assert classify_sport_from_strava_csv_type("Yoga") is None


def test_classify_unknown_returns_none() -> None:
    assert classify_sport_from_strava_csv_type("Spelunking") is None
    assert classify_sport_from_strava_csv_type("") is None
    assert classify_sport_from_strava_csv_type(None) is None


# ── parse_strava_activities_csv ──────────────────────────────────────


def _csv(*rows: tuple[str, str, str]) -> bytes:
    """Helper to build a minimal Strava-shaped CSV.

    Columns: Activity ID, Filename, Activity Type — only Filename and
    Activity Type are read by the parser, the rest are header padding
    to mimic the real archive shape.
    """
    lines = ["Activity ID,Activity Date,Activity Name,Activity Type,Filename"]
    for aid, atype, filename in rows:
        lines.append(f"{aid},2026-01-01,Some ride,{atype},{filename}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def test_parse_csv_maps_filenames_to_sports() -> None:
    csv = _csv(
        ("100", "Mountain Bike Ride", "activities/100.gpx"),
        ("101", "Gravel Ride", "activities/101.gpx"),
        ("102", "Ride", "activities/102.gpx"),
        ("103", "Run", "activities/103.gpx"),
    )
    out = parse_strava_activities_csv(csv)
    assert out == {
        "activities/100.gpx": "mtb",
        "activities/101.gpx": "gravel",
        "activities/102.gpx": "road",
        "activities/103.gpx": "running",
    }


def test_parse_csv_stores_unmapped_types_as_none_skip_signal() -> None:
    """Out-of-scope rows (Yoga / Swim / Ski / unknown) are stored with
    value=None — that's the SKIP signal the ZIP parser turns into a
    `skip_reason` so we don't pollute the heatmap by falling back to
    a form-supplied sport."""
    csv = _csv(
        ("100", "Swim", "activities/100.gpx"),
        ("101", "Alpine Ski", "activities/101.gpx"),
        ("102", "Mountain Bike Ride", "activities/102.gpx"),
        ("103", "Yoga", "activities/103.gpx"),
        ("104", "FutureStravaTypeWeHaventSeenYet", "activities/104.gpx"),
    )
    out = parse_strava_activities_csv(csv)
    assert out == {
        "activities/100.gpx": None,
        "activities/101.gpx": None,
        "activities/102.gpx": "mtb",
        "activities/103.gpx": None,
        "activities/104.gpx": None,  # unknown future type — skip, not fall-through
    }


def test_parse_csv_handles_utf8_bom() -> None:
    """Strava emits files with a leading BOM. Must be stripped."""
    csv = b"\xef\xbb\xbf" + _csv(("100", "Ride", "activities/100.gpx"))
    out = parse_strava_activities_csv(csv)
    assert out == {"activities/100.gpx": "road"}


def test_parse_csv_handles_empty_bytes() -> None:
    assert parse_strava_activities_csv(b"") == {}


def test_parse_csv_handles_missing_columns() -> None:
    """If the CSV doesn't have the expected columns, return empty rather
    than crashing. Caller falls back to form-supplied sport."""
    bad_csv = b"foo,bar,baz\r\n1,2,3\r\n"
    assert parse_strava_activities_csv(bad_csv) == {}


def test_parse_csv_skips_rows_with_no_filename() -> None:
    csv = _csv(
        ("100", "Ride", "activities/100.gpx"),
        ("101", "Run", ""),  # blank Filename — skip
    )
    out = parse_strava_activities_csv(csv)
    assert out == {"activities/100.gpx": "road"}


# ── parse_zip_of_gpx integration ─────────────────────────────────────


_MINIMAL_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <name>x</name>
    <type>cycling</type>
    <trkseg>
      <trkpt lat="45.5" lon="4.5"><ele>200</ele></trkpt>
      <trkpt lat="45.51" lon="4.51"><ele>210</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


def _make_zip(*members: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_with_csv_stamps_sport_from_csv() -> None:
    """End-to-end: ZIP contains activities.csv + 2 GPX files, parser
    stamps `sport_from_csv` so the cascade in imports.py uses it."""
    csv = _csv(
        ("100", "Mountain Bike Ride", "activities/100.gpx"),
        ("101", "Gravel Ride", "activities/101.gpx"),
    )
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
        ("activities/101.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert by_name["activities/100.gpx"]["sport_from_csv"] == "mtb"
    assert by_name["activities/101.gpx"]["sport_from_csv"] == "gravel"


def test_zip_without_csv_no_sport_from_csv_key() -> None:
    """When the ZIP has no activities.csv, sport_from_csv must be absent
    so the cascade falls through to the existing GPX/form path."""
    z = _make_zip(
        ("activities/100.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert "sport_from_csv" not in by_name["activities/100.gpx"]


def test_zip_csv_lookup_per_filename_not_global() -> None:
    """A CSV row for one file must NOT bleed onto sibling files."""
    csv = _csv(("100", "Mountain Bike Ride", "activities/100.gpx"))
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
        ("activities/200.gpx", _MINIMAL_GPX),  # not in CSV
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert by_name["activities/100.gpx"].get("sport_from_csv") == "mtb"
    assert "sport_from_csv" not in by_name["activities/200.gpx"]


def test_zip_with_malformed_csv_no_crash() -> None:
    """A broken activities.csv must not blow up the whole ZIP — fall back
    to the in-GPX hint for every file."""
    z = _make_zip(
        ("activities.csv", b"garbage,not,a,real,csv\n"),
        ("activities/100.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert "sport_from_csv" not in by_name["activities/100.gpx"]


def test_zip_csv_out_of_scope_row_sets_skip_reason() -> None:
    """A Yoga / Swim / Ski row in the CSV must result in skip_reason on
    the parsed dict — heatmap-pollution defense. The cascade in
    imports.py drops these files before they hit ingest."""
    csv = _csv(
        ("100", "Yoga", "activities/100.gpx"),
        ("101", "Mountain Bike Ride", "activities/101.gpx"),
    )
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
        ("activities/101.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out}
    # Yoga GPX → skip_reason set, no sport_from_csv
    assert "skip_reason" in by_name["activities/100.gpx"]
    assert "out-of-scope" in by_name["activities/100.gpx"]["skip_reason"]
    assert "sport_from_csv" not in by_name["activities/100.gpx"]
    # MTB GPX → sport_from_csv set, no skip_reason
    assert by_name["activities/101.gpx"].get("sport_from_csv") == "mtb"
    assert "skip_reason" not in by_name["activities/101.gpx"]


def test_zip_csv_file_not_in_csv_falls_through_no_skip() -> None:
    """A GPX in the ZIP but absent from the CSV must NOT trigger skip —
    the CSV simply doesn't cover that file, fall back to the in-GPX
    <trk><type> hint (or form-supplied sport). Otherwise we'd lose every
    file the CSV happens to miss."""
    csv = _csv(("100", "Mountain Bike Ride", "activities/100.gpx"))
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
        ("activities/200.gpx", _MINIMAL_GPX),  # not in CSV
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert "skip_reason" not in by_name["activities/200.gpx"]
    assert "sport_from_csv" not in by_name["activities/200.gpx"]


# ── Heatmap-pollution hardening from 2nd-pass review ──────────────────


def test_virtual_ride_is_skipped_not_classified_as_road() -> None:
    """**Mission-critical**: Zwift / virtual rides use fictional Watopia
    coordinates (~lat -11.6, lon 166.9 — South Pacific). Mapping them
    to "road" would pin that fake island onto the community road
    heatmap. Must be None → caller skips. Same for virtual_run /
    treadmill_run."""
    assert classify_sport_from_strava_csv_type("Virtual Ride") is None
    assert classify_sport_from_strava_csv_type("Virtual Run") is None
    assert classify_sport_from_strava_csv_type("Treadmill Run") is None
    assert classify_sport_from_strava_csv_type("Indoor Cycling") is None
    assert classify_sport_from_strava_csv_type("Indoor Running") is None


def test_csv_filename_case_mismatch_still_matches_zip_member() -> None:
    """A hand-edited / re-zipped archive may end up with the CSV row
    saying `Activities/100.GPX` while the ZIP carries
    `activities/100.gpx`. The skip-gate must survive that — otherwise
    the file silently bypasses the gate and pollutes via form-sport.
    Lookup is keyed on a normalized form."""
    csv = _csv(("100", "Yoga", "Activities/100.GPX"))  # mixed case
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),  # canonical
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out}
    assert "skip_reason" in by_name["activities/100.gpx"], (
        "Yoga in CSV must skip even when filename casing differs"
    )


def test_csv_filename_with_gz_suffix_matches_decompressed_gpx() -> None:
    """Older Strava archives listed `activities/100.gpx.gz` in the CSV
    even when the ZIP member was already-decompressed
    `activities/100.gpx`. The normalizer strips `.gz` so the lookup
    still finds the row."""
    csv = _csv(("100", "Gravel Ride", "activities/100.gpx.gz"))
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert by_name["activities/100.gpx"].get("sport_from_csv") == "gravel"


def test_evil_nested_activities_csv_does_not_shadow_root() -> None:
    """Hardening: a crafted ZIP with `evil/activities.csv` next to the
    real one (or only the nested one) must NOT be trusted. We accept
    only the exact root-level `activities.csv`. A nested CSV's rows
    are silently ignored — the GPX files fall through to their in-GPX
    hint / form-sport."""
    # Only a nested CSV — no root CSV. Should produce NO sport_from_csv.
    nested_csv = _csv(("100", "Mountain Bike Ride", "activities/100.gpx"))
    z = _make_zip(
        ("evil/activities.csv", nested_csv),
        ("activities/100.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out if "error" not in r}
    assert "sport_from_csv" not in by_name["activities/100.gpx"]
    assert "skip_reason" not in by_name["activities/100.gpx"]


def test_csv_duplicate_filename_last_row_wins() -> None:
    """Duplicate Filename in the CSV is an edge case (shouldn't happen
    from Strava, but might from a hand-edited archive). Document the
    behavior: last row wins. Captured here so future refactors that
    change this semantics fail visibly."""
    csv = _csv(
        ("100", "Mountain Bike Ride", "activities/100.gpx"),
        ("100", "Gravel Ride", "activities/100.gpx"),
    )
    out = parse_strava_activities_csv(csv)
    assert out == {"activities/100.gpx": "gravel"}


def test_csv_trailing_whitespace_in_filename_still_matches() -> None:
    """Defensive: a CSV row with stray whitespace in Filename must
    still resolve. The parse function strips before keying."""
    csv = _csv(("100", "Yoga", "  activities/100.gpx  "))
    z = _make_zip(
        ("activities.csv", csv),
        ("activities/100.gpx", _MINIMAL_GPX),
    )
    out = parse_zip_of_gpx(z)
    by_name = {r["source_file"]: r for r in out}
    assert "skip_reason" in by_name["activities/100.gpx"]
