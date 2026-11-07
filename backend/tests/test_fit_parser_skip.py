"""FIT out-of-scope sport skip — a Garmin swim/virtual/indoor .fit must NOT
pollute the community heatmap (mirrors the GPX skip-list). Drives the real
`_fit_skip_reason` with a lightweight mock FitFile, and the real `parse_fit`
on a hand-encoded FIT binary (no fixture file needed)."""
import struct

import pytest

from app.services.fit_parser import _FIT_EXTRA_SKIP, _fit_skip_reason, parse_fit
from app.services.gpx import gpx_track_type_skip_or_sport
from app.services.strava_utils import classify_sport


class _FakeSession:
    def __init__(self, sport=None, sub_sport=None):
        self._v = {"sport": sport, "sub_sport": sport and sub_sport or sub_sport}

    def get_value(self, k):
        return self._v.get(k)


class _FakeFit:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_messages(self, kind):
        return self._sessions if kind == "session" else []


def _skip(sport=None, sub_sport=None):
    return _fit_skip_reason(_FakeFit([_FakeSession(sport, sub_sport)]))


def test_out_of_scope_sports_are_skipped():
    for s in ("swimming", "open_water_swimming", "alpine_skiing", "rowing",
              "kayaking", "sailing", "training", "fitness_equipment",
              "motorcycling", "driving", "golf"):
        assert _skip(s) is not None, f"{s} should be skipped"


def test_sub_sport_out_of_scope_is_skipped():
    # e.g. sport='cycling', sub_sport='indoor_cycling' → skip (trainer)
    assert _skip("cycling", "indoor_cycling") is not None


def test_in_scope_sports_are_kept():
    for s in ("cycling", "road_cycling", "gravel_cycling", "mountain_biking",
              "running", "trail_running", "walking", "hiking"):
        assert _skip(s) is None, f"{s} should NOT be skipped"


def test_no_sport_field_is_kept():
    assert _skip(None) is None
    assert _fit_skip_reason(_FakeFit([])) is None


def test_fit_extra_skip_covers_fit_native_sports():
    # these are NOT in the GPX list; the FIT-native extras must catch them
    assert "training" in _FIT_EXTRA_SKIP
    assert "fitness_equipment" in _FIT_EXTRA_SKIP
    assert "motorcycling" in _FIT_EXTRA_SKIP


# ── Indoor / virtual FIT (data audit C2, 2026-11) ───────────────────────────
#
# The tests below drive the REAL ``parse_fit`` on a real FIT binary encoded
# here byte-for-byte (header + definition/data messages + CRC), so fitparse
# itself decodes the sport / sub_sport / manufacturer enums. That pins the
# exact strings fitparse yields (sub_sport 58 → "virtual_activity", 5 → "spin")
# instead of trusting a mock. Before the fix, the virtual/spin/Zwift cases
# below parsed with no ``skip_reason`` and landed on the road heatmap.

_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


def _fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


# FIT enum ids (FIT SDK profile, as decoded by fitparse 1.2.0).
SPORT = {"cycling": 2, "running": 1, "training": 10}
SUB_SPORT = {"generic": 0, "road": 7, "spin": 5, "indoor_cycling": 6,
             "virtual_activity": 58, "e_bike_fitness": 28, "treadmill": 1}
MANUFACTURER = {"garmin": 1, "zwift": 260, "development": 255}


def _make_fit(sport: str, sub_sport: str, manufacturer: str = "garmin",
              n_points: int = 20) -> bytes:
    """A minimal valid .FIT activity: file_id + n GPS records + one session."""
    def definition(local: int, global_num: int, fields: list) -> bytes:
        out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
        for num, size, base in fields:
            out += struct.pack("<BBB", num, size, base)
        return out

    body = b""
    # file_id (global 0): type=activity(4), manufacturer
    body += definition(0, 0, [(0, 1, 0x00), (1, 2, 0x84)])
    body += struct.pack("<BBH", 0, 4, MANUFACTURER[manufacturer])
    # record (global 20): position_lat, position_long (semicircles), timestamp
    body += definition(1, 20, [(0, 4, 0x85), (1, 4, 0x85), (253, 4, 0x86)])
    semi = 2 ** 31 / 180.0
    for i in range(n_points):
        lat, lon = 43.6 + i * 1e-3, 3.8
        body += struct.pack("<BiiI", 1, int(lat * semi), int(lon * semi),
                            1_000_000_000 + i)
    # session (global 18): sport, sub_sport, total_distance (cm)
    body += definition(2, 18, [(5, 1, 0x00), (6, 1, 0x00), (9, 4, 0x86)])
    body += struct.pack("<BBBI", 2, SPORT[sport], SUB_SPORT[sub_sport], 200_000)

    header = struct.pack("<BBHI4s", 14, 0x10, 2093, len(body), b".FIT")
    header += struct.pack("<H", _fit_crc(header))
    data = header + body
    return data + struct.pack("<H", _fit_crc(data))


@pytest.mark.parametrize("sport,sub_sport,manufacturer", [
    ("cycling", "virtual_activity", "garmin"),       # Zwift/Rouvy via head unit
    ("cycling", "virtual_activity", "development"),  # Rouvy/Kinomap app export
    ("cycling", "spin", "garmin"),                   # turbo trainer / spin bike
    ("cycling", "indoor_cycling", "garmin"),
    ("running", "treadmill", "garmin"),
    ("training", "generic", "garmin"),
    ("cycling", "generic", "zwift"),                 # Zwift file, plain sub_sport
    ("cycling", "road", "zwift"),
])
def test_real_fit_indoor_or_virtual_is_skipped(sport, sub_sport, manufacturer):
    parsed = parse_fit(_make_fit(sport, sub_sport, manufacturer))
    assert parsed["coord_count"] == 20, "the fixture really carries GPS records"
    assert parsed.get("skip_reason"), (
        f"{sport}/{sub_sport} by {manufacturer} must be skipped, got {parsed!r}")
    assert parsed["skip_code"] == "GPX_TYPE_OUT_OF_SCOPE"


@pytest.mark.parametrize("sport,sub_sport", [
    ("cycling", "road"), ("cycling", "generic"), ("running", "generic"),
    # e_bike_fitness is Garmin's OUTDOOR e-bike sub_sport; e-bike rides are
    # kept as road everywhere else (gpx.py e_bike, strava_utils "ebikeride").
    ("cycling", "e_bike_fitness"),
])
def test_real_fit_outdoor_ride_is_kept(sport, sub_sport):
    parsed = parse_fit(_make_fit(sport, sub_sport, "garmin"))
    assert parsed.get("skip_reason") is None
    assert parsed["coord_count"] == 20
    assert parsed["sport"] in ("road", "running")


def test_virtual_fit_strings_are_the_ones_fitparse_yields():
    # Guard the skip list against spelling drift: these are the decoded
    # strings fitparse produces for the enum ids used above.
    from fitparse.profile import FIELD_TYPES
    subs = FIELD_TYPES["sub_sport"].values
    assert subs[58] == "virtual_activity" and subs[5] == "spin"
    assert FIELD_TYPES["manufacturer"].values[260] == "zwift"
    for s in ("virtual_activity", "spin", "indoor_cycling"):
        assert _skip("cycling", s) is not None


def test_gpx_and_strava_virtual_types_are_skipped():
    for t in ("VirtualRide", "virtual_ride", "Virtual Ride", "virtual_activity",
              "indoor_cycling", "IndoorCycling", "spin", "VirtualRun"):
        assert gpx_track_type_skip_or_sport(t) == (None, True), t
    for t in ("VirtualRide", "Virtual Ride", "VirtualRun", "Virtual Run"):
        assert classify_sport(t, "Zwift - Watopia") is None, t
