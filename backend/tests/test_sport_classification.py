"""Sport classification on every import path — non-regression.

The sport decides which community heatmap layer a trace lands in (Route /
Gravel / VTT / Course). Rule: an ambiguous / unknown sport is SKIPPED (or
asked to the user for a loose file), never defaulted to a layer.

Bugs pinned here (all reproduced before the fix):

* FIT ``sub_sport`` was ignored: Garmin MTB / gravel / e-MTB rides
  (sport=cycling + sub_sport=mountain / gravel_cycling / e_bike_mountain)
  were filed as ``road``.
* FIT hiking / walking / e_biking resolved to "unknown" → the archive's
  blanket fallback sport (the UI silently sent ``mtb``) → hikes on VTT.
* The FIT device sport outranked activities.csv: a Strava export whose
  original .fit says "cycling" filed a "Gravel Ride" / "Mountain Bike Ride"
  as road.
* GPX ``<type>`` lookup was exact-string: "GravelRide", "TrailRun",
  "e_bike_mountain", Strava numeric "9" fell through to the form sport.
* A GPX typed only "cycling" in an archive without a CSV row fell to the
  blanket fallback sport instead of being skipped.

Every test drives the REAL parser / handler (parse_fit on a real FIT binary,
parse_gpx, parse_strava_activities_csv, POST /imports/files, the archive
drain's ``_ingest_member_bytes``) — no inline mirror of the mapping.
"""

import io
import zipfile

import pytest

from app.jobs import ingest_pending_archives as drain
from app.services.fit_parser import parse_fit
from app.services.gpx import (
    gpx_track_type_skip_or_sport,
    parse_gpx,
    parse_strava_activities_csv,
)
from app.services.strava_utils import classify_sport, resolve_sport_cascade
from tests.test_fit_parser_skip import _make_fit


def _gpx(track_type: str | None, *, lat0: float = 43.61, name: str = "Sortie") -> bytes:
    type_xml = f"<type>{track_type}</type>" if track_type is not None else ""
    pts = "".join(
        f'<trkpt lat="{lat0 + i * 0.001:.6f}" lon="3.870000"><ele>50</ele>'
        f"<time>2026-05-01T08:{i:02d}:00Z</time></trkpt>"
        for i in range(12)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name>{type_xml}<trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


# ── FIT: sport + sub_sport (real binary through fitparse) ──────────────────


@pytest.mark.parametrize(
    "sport,sub_sport,expected",
    [
        ("cycling", "mountain", "mtb"),
        ("cycling", "downhill", "mtb"),
        ("cycling", "e_bike_mountain", "mtb"),
        ("e_biking", "e_bike_mountain", "mtb"),
        ("cycling", "gravel_cycling", "gravel"),
        ("cycling", "cyclocross", "gravel"),
        ("cycling", "mixed_surface", "gravel"),
        ("cycling", "road", "road"),
        ("cycling", "commuting", "road"),
        ("cycling", "generic", "road"),
        ("e_biking", "generic", "road"),
        ("running", "trail", "running"),
        ("hiking", "generic", "running"),
        ("walking", "generic", "running"),
    ],
)
def test_real_fit_sub_sport_is_honoured(sport, sub_sport, expected):
    parsed = parse_fit(_make_fit(sport, sub_sport))
    assert parsed.get("skip_reason") is None
    assert parsed["sport"] == expected, (sport, sub_sport, parsed["sport"])


@pytest.mark.parametrize(
    "sport,sub_sport",
    [
        ("generic", "generic"),  # no discipline at all
        ("mountaineering", "generic"),  # not a layer we model
        ("cycling", 200),  # sub_sport newer than the fitparse profile
    ],
)
def test_real_fit_without_usable_sport_is_unknown_not_road(sport, sub_sport):
    parsed = parse_fit(_make_fit(sport, sub_sport))
    assert parsed["sport"] is None


def test_real_fit_hand_cycling_is_skipped():
    parsed = parse_fit(_make_fit("cycling", "hand_cycling"))
    assert parsed.get("skip_reason")


# ── GPX <trk><type> (real parse_gpx) ──────────────────────────────────────


@pytest.mark.parametrize(
    "track_type,expected",
    [
        ("mountain_biking", "mtb"),
        ("MountainBikeRide", "mtb"),
        ("Mountain Bike Ride", "mtb"),
        ("EMountainBikeRide", "mtb"),
        ("e_bike_mountain", "mtb"),
        ("downhill_biking", "mtb"),
        ("GravelRide", "gravel"),
        ("gravel_ride", "gravel"),
        ("gravel_cycling", "gravel"),
        ("road_biking", "road"),
        ("EBikeRide", "road"),
        ("e_bike_fitness", "road"),
        ("TrailRun", "running"),
        ("trail_running", "running"),
        ("Hike", "running"),
        ("hiking", "running"),
        ("9", "running"),  # Strava numeric type for Run
    ],
)
def test_gpx_type_maps_to_sport(track_type, expected):
    parsed = parse_gpx(_gpx(track_type))
    assert parsed.get("skip_reason") is None
    assert parsed["sport_from_gpx"] == expected, track_type


@pytest.mark.parametrize("track_type", ["cycling", "Ride", "biking", "1", "", None, "zorglub"])
def test_gpx_generic_or_unknown_type_carries_no_sport(track_type):
    parsed = parse_gpx(_gpx(track_type))
    assert parsed["sport_from_gpx"] is None
    assert parsed.get("skip_reason") is None


@pytest.mark.parametrize("track_type", ["Yoga", "VirtualRide", "Swim", "AlpineSki", "hand_cycling"])
def test_gpx_out_of_scope_type_is_skipped(track_type):
    assert parse_gpx(_gpx(track_type)).get("skip_reason"), track_type
    assert gpx_track_type_skip_or_sport(track_type) == (None, True)


# ── activities.csv (English + localized French headers / labels) ──────────


def test_csv_english_export():
    csv = (
        b"Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        b"1,x,Sortie,Gravel Ride,activities/1.gpx\n"
        b"2,x,Dimanche,Ride,activities/2.gpx\n"
        b"3,x,VTT du dimanche,Ride,activities/3.gpx\n"
        b"4,x,Zwift,Virtual Ride,activities/4.gpx\n"
        b"5,x,Nage,Swim,activities/5.gpx\n"
    )
    assert parse_strava_activities_csv(csv) == {
        "activities/1.gpx": "gravel",
        "activities/2.gpx": "road",
        "activities/3.gpx": "mtb",
        "activities/4.gpx": None,
        "activities/5.gpx": None,
    }


def test_csv_localized_french_headers_and_labels():
    csv = (
        "﻿ID de l'activité,Date de l'activité,Nom de l'activité,Type d'activité,Nom du fichier\n"
        "1,x,Sortie,Vélo,activities/1.fit.gz\n"
        "2,x,Sortie,VTT,activities/2.fit.gz\n"
        "3,x,Sortie,Vélo gravel,activities/3.fit.gz\n"
        "4,x,Sortie,Course à pied,activities/4.gpx\n"
        "5,x,Sortie,Randonnée,activities/5.gpx\n"
        "6,x,Sortie,Vélo virtuel,activities/6.gpx\n"
        "7,x,Sortie,Type inconnu,activities/7.gpx\n"
    ).encode()
    assert parse_strava_activities_csv(csv) == {
        "activities/1.fit.gz": "road",
        "activities/2.fit.gz": "mtb",
        "activities/3.fit.gz": "gravel",
        "activities/4.gpx": "running",
        "activities/5.gpx": "running",
        "activities/6.gpx": None,
        "activities/7.gpx": None,  # unknown label → skip, never a default
    }


def test_csv_with_unrecognised_headers_gives_no_hint():
    csv = b"Foo,Bar\nactivities/1.gpx,Ride\n"
    assert parse_strava_activities_csv(csv) == {}


def test_classify_sport_unknown_and_missing_type_skip():
    assert classify_sport(None) is None
    assert classify_sport("") is None
    assert classify_sport("Pickleball") is None
    assert classify_sport("Vélo électrique") == "road"


# ── Cascade SSOT ──────────────────────────────────────────────────────────


def test_resolve_sport_cascade():
    # first valid wins
    assert resolve_sport_cascade(None, "running", "mtb") == "running"
    # a generic road is refined by a more specific later source
    assert resolve_sport_cascade("road", "mtb") == "mtb"
    assert resolve_sport_cascade("road", None, "gravel") == "gravel"
    # CSV specific beats the FIT's generic road
    assert resolve_sport_cascade("gravel", "road") == "gravel"
    # nothing → None (skip), never a default
    assert resolve_sport_cascade(None, None, None) is None
    assert resolve_sport_cascade("unknown", "") is None


# ── Archive drain: real _ingest_member_bytes ──────────────────────────────


def _param_id(v) -> str:
    return "bytes" if isinstance(v, bytes) else str(v)


@pytest.fixture
def captured_ingest(monkeypatch):
    calls: list[dict] = []

    def _fake_ingest(**kwargs):
        calls.append(kwargs["activity_data"])
        return {"status": "created", "activity_id": f"a{len(calls)}"}

    monkeypatch.setattr(drain.ingest_service, "ingest_activity", _fake_ingest)
    return calls


def _drain(filename: str, raw: bytes, csv_sport):
    return drain._ingest_member_bytes(
        user_id="u1",
        filename=filename,
        raw=raw,
        resolved_sport=csv_sport,
        contribute_heatmap=True,
        source="manual_upload",
        skip_heat_computation=True,
    )


@pytest.mark.parametrize(
    "filename,raw,csv_sport,expected",
    [
        # Strava export: CSV "Gravel Ride" + original FIT saying generic cycling
        ("activities/1.fit", _make_fit("cycling", "generic"), "gravel", "gravel"),
        # CSV generic "Ride" + FIT sub_sport=mountain → the specific FIT wins
        ("activities/2.fit", _make_fit("cycling", "mountain"), "road", "mtb"),
        # Garmin export (no CSV): MTB / gravel / hike FIT
        ("UploadedFiles_0.zip!3.fit", _make_fit("cycling", "mountain"), None, "mtb"),
        ("UploadedFiles_0.zip!4.fit", _make_fit("cycling", "gravel_cycling"), None, "gravel"),
        ("UploadedFiles_0.zip!5.fit", _make_fit("hiking", "generic"), None, "running"),
        # legacy per-member queue row without hint
        ("6.gpx", _gpx("MountainBikeRide"), "unknown", "mtb"),
    ],
    ids=_param_id,
)
def test_archive_member_sport(captured_ingest, filename, raw, csv_sport, expected):
    outcome, _aid, err = _drain(filename, raw, csv_sport)
    assert outcome == "imported", err
    assert captured_ingest[-1]["sport"] == expected


@pytest.mark.parametrize(
    "filename,raw",
    [
        ("7.gpx", _gpx("cycling")),  # Strava-collapsed GPX, no CSV row
        ("8.gpx", _gpx(None)),  # untyped GPX
        ("9.fit", _make_fit("generic", "generic")),
    ],
    ids=_param_id,
)
def test_archive_member_with_unknown_sport_is_skipped(captured_ingest, filename, raw):
    outcome, _aid, err = _drain(filename, raw, None)
    assert (outcome, err) == ("skipped", "sport_unknown")
    assert captured_ingest == []


# ── POST /imports/files (real handler, real DB) ───────────────────────────


def _post(client, auth_headers, name: str, content: bytes, sport: str | None = None):
    data = {"contribute_heatmap": "false"}
    if sport is not None:
        data["sport"] = sport
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": (name, content, "application/octet-stream")},
        data=data,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _sport_of(client, auth_headers, activity_id: str) -> str:
    resp = client.get(f"/me/activities/{activity_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["properties"]["sport"]


def test_loose_generic_gpx_without_sport_is_skipped_not_road(client, auth_headers):
    body = _post(client, auth_headers, "ride.gpx", _gpx("cycling", lat0=44.01))
    assert body["imported"] == 0 and body["skipped"] == 1
    assert any("sport" in e for e in body["errors"])


def test_loose_generic_gpx_takes_the_users_sport(client, auth_headers):
    body = _post(client, auth_headers, "ride.gpx", _gpx("cycling", lat0=44.11), sport="gravel")
    assert body["imported"] == 1
    assert _sport_of(client, auth_headers, body["activity_ids"][0]) == "gravel"


def test_loose_mtb_fit_is_mtb_even_if_user_picked_road(client, auth_headers):
    body = _post(client, auth_headers, "ride.fit", _make_fit("cycling", "mountain"), sport="road")
    assert body["imported"] == 1
    assert _sport_of(client, auth_headers, body["activity_ids"][0]) == "mtb"


def test_loose_hike_fit_never_lands_on_vtt(client, auth_headers):
    raw = _make_fit("hiking", "generic", n_points=21)  # distinct file hash
    body = _post(client, auth_headers, "hike.fit", raw, sport="mtb")
    assert body["imported"] == 1
    assert _sport_of(client, auth_headers, body["activity_ids"][0]) == "running"


def test_zip_csv_beats_the_fit_generic_cycling_and_no_form_fallback(client, auth_headers):
    csv = b"Activity ID,Activity Name,Activity Type,Filename\n1,Sortie,Gravel Ride,activities/1.fit\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/1.fit", _make_fit("cycling", "generic", n_points=22))
        # not in the CSV, Strava-collapsed type → must NOT take the form "mtb"
        zf.writestr("activities/2.gpx", _gpx("cycling", lat0=44.21))
    body = _post(client, auth_headers, "export.zip", buf.getvalue(), sport="mtb")
    assert body["imported"] == 1 and body["skipped"] == 1, body
    assert _sport_of(client, auth_headers, body["activity_ids"][0]) == "gravel"
