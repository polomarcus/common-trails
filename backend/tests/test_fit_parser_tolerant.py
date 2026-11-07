"""FIT parse robustness — non-regression for the prod drain failure where
EVERY ``.fit.gz`` member of a real Garmin/Strava archive was dropped with

    fitparse.utils.FitParseError: Invalid field size 1 for type 'uint32'
    (expected a multiple of 4)

fitparse 1.2.0 (unmaintained) aborts the whole file when a definition message
declares a field whose size is not a multiple of its base type (recent Garmin
firmwares do this, usually in undocumented messages unrelated to GPS). The FIT
spec says to read such a field as raw bytes. ``parse_fit`` now falls back to
fitdecode for those files.

The FIT binaries are hand-encoded byte-for-byte here (header + definition/data
messages + CRC) and fed to the REAL ``parse_fit`` — no fixture file, no user
data.
"""

import io
import struct
from datetime import datetime

import fitparse
import pytest

from app.services.fit_parser import parse_fit

_CRC_TABLE = (
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
)


def _crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _definition(local: int, global_num: int, fields: list) -> bytes:
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
    for num, size, base in fields:
        out += struct.pack("<BBB", num, size, base)
    return out


N_POINTS = 20
_SEMI = 2**31 / 180.0
_UINT32 = 0x86


def _make_fit(missized: str | None = None) -> bytes:
    """A minimal Garmin cycling activity: file_id + N GPS records (lat, lon,
    timestamp, altitude) + one session.

    ``missized``:
      - ``None``           → a perfectly valid FIT (fitparse reads it);
      - ``"unknown_mesg"`` → an extra manufacturer-specific message (global
        0xFF00) whose definition declares a uint32 field with size 1 — the
        exact prod error;
      - ``"in_record"``    → the same mis-sized uint32 field (unknown field
        number) inside the GPS ``record`` definition itself.
    """
    body = _definition(0, 0, [(0, 1, 0x00), (1, 2, 0x84)])  # file_id
    body += struct.pack("<BBH", 0, 4, 1)  # type=activity, manufacturer=garmin

    if missized == "unknown_mesg":
        body += _definition(3, 0xFF00, [(0, 1, _UINT32), (1, 2, 0x84)])
        body += struct.pack("<BBH", 3, 7, 42)

    record_fields = [(0, 4, 0x85), (1, 4, 0x85), (253, 4, _UINT32), (2, 2, 0x84)]
    if missized == "in_record":
        record_fields.append((200, 1, _UINT32))
    body += _definition(1, 20, record_fields)
    for i in range(N_POINTS):
        lat, lon, alt_m = 43.6 + i * 1e-3, 3.8, 100 + i
        body += struct.pack("<BiiIH", 1, int(lat * _SEMI), int(lon * _SEMI), 1_000_000_000 + i, (alt_m + 500) * 5)
        if missized == "in_record":
            body += b"\x07"

    # session: sport=cycling(2), sub_sport=road(7), total_distance (cm), start_time
    body += _definition(2, 18, [(5, 1, 0x00), (6, 1, 0x00), (9, 4, _UINT32), (2, 4, _UINT32)])
    body += struct.pack("<BBBII", 2, 2, 7, 200_000, 1_000_000_000)

    header = struct.pack("<BBHI4s", 14, 0x10, 2093, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + body
    return data + struct.pack("<H", _crc(data))


def _assert_full_track(parsed: dict) -> None:
    assert parsed["coord_count"] == N_POINTS
    assert parsed["sport"] == "road"
    assert parsed["name"] == "Cycling"
    assert parsed["distance_m"] == 2000.0
    assert parsed["elevation_gain_m"] == N_POINTS - 1
    assert parsed.get("skip_reason") is None
    # fitparse contract: NAIVE UTC datetime (2021-09-08 01:46:40 UTC = FIT 1e9)
    assert parsed["activity_date"] == datetime(2021, 9, 8, 1, 46, 40)
    assert parsed["activity_date"].tzinfo is None
    import json

    coords = json.loads(parsed["geometry_geojson"])["coordinates"]
    assert coords[0] == pytest.approx([3.8, 43.6, 100.0], abs=1e-6)
    assert coords[-1] == pytest.approx([3.8, 43.6 + (N_POINTS - 1) * 1e-3, 100.0 + N_POINTS - 1], abs=1e-6)


@pytest.mark.parametrize("missized", ["unknown_mesg", "in_record"])
def test_fitparse_alone_raises_the_prod_error(missized):
    """Pin the reproduction: fitparse by itself rejects these bytes with the
    exact prod message (this is what dropped every member before the fix)."""
    with pytest.raises(fitparse.FitParseError, match=r"Invalid field size 1 for type 'uint32'"):
        fitparse.FitFile(io.BytesIO(_make_fit(missized))).parse()


@pytest.mark.parametrize("missized", ["unknown_mesg", "in_record"])
def test_missized_field_fit_yields_gps_track(missized):
    _assert_full_track(parse_fit(_make_fit(missized)))


def test_valid_fit_unchanged_and_same_output_as_fallback():
    """A normal FIT still goes through fitparse and yields the same dict the
    fallback produces for the mis-sized variant (minus the file hash)."""
    normal = parse_fit(_make_fit(None))
    _assert_full_track(normal)
    recovered = parse_fit(_make_fit("unknown_mesg"))
    for d in (normal, recovered):
        d.pop("file_hash")
    assert recovered == normal


def test_missized_virtual_fit_still_skipped():
    """The PR #36 indoor/virtual skip must keep working on the fallback path."""
    raw = bytearray(_make_fit("unknown_mesg"))
    # flip session sub_sport road(7) → virtual_activity(58), then re-CRC
    session_data = struct.pack("<BBBII", 2, 2, 7, 200_000, 1_000_000_000)
    idx = bytes(raw).rindex(session_data)
    raw[idx + 2] = 58
    body = bytes(raw[:-2])
    raw = body + struct.pack("<H", _crc(body))
    parsed = parse_fit(raw)
    assert parsed["coord_count"] == N_POINTS
    assert parsed["skip_code"] == "GPX_TYPE_OUT_OF_SCOPE"


@pytest.mark.parametrize(
    "mangle",
    [
        lambda b: b[: len(b) // 2],  # truncated mid-records
        lambda b: b[:-2] + b"\x00\x00",  # bad CRC
        lambda b: b"\x00" * 1024,  # not a FIT at all
    ],
)
@pytest.mark.parametrize("missized", [None, "unknown_mesg"])
def test_corrupt_fit_still_fails_with_fitparse_error(mangle, missized):
    """Truncated / corrupt files must still fail cleanly with the SAME
    fitparse exception type callers already handle (drain → member
    ``failed``, upload → 422) — the fallback must not invent partial tracks."""
    with pytest.raises(fitparse.FitParseError):
        parse_fit(mangle(_make_fit(missized)))


def test_drain_parse_member_recovers_missized_fit_gz():
    """End-to-end on the archive drain's own member parser: a ``.fit.gz``
    member with the mis-sized field (the prod shape) now yields its track
    instead of raising → being counted ``failed``."""
    import gzip

    from app.jobs.ingest_pending_archives import _parse_member

    parsed = _parse_member(gzip.compress(_make_fit("unknown_mesg")), "activities/1.fit.gz")
    _assert_full_track(parsed)
