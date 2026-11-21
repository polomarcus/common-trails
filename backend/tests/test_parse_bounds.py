"""Parser bounds of the GPX / FIT import paths.

* GPX: points (``<trkpt``/``<rtept``/``<wpt``) and XML tags are counted on the
  raw bytes BEFORE gpxpy builds its tree, on every path that parses GPX bytes
  (/gpx/upload, /imports/files single + zip member, archive drain).
* FIT: the parse cost (messages + field values) is counted from the record
  headers before fitparse decodes anything.
* A process-wide slot bounds concurrent parses; a request that cannot get one
  in time gets 503.
* Parser / ingest internals never reach the client.

Every endpoint test drives the real handler, with the XML / FIT decoder
replaced by a tripwire so a parse that should not happen fails loudly.
"""
from __future__ import annotations

import io
import struct
import threading
import time
import zipfile

import pytest

from app.services import fit_parser
from app.services import gpx as gpx_service

VALID_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>tiny</name><type>road_biking</type><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def _dense_gpx(n_points: int) -> bytes:
    pts = "".join(
        f'<trkpt lat="43.61" lon="{3.87 + i * 1e-6:.6f}"><ele>100</ele></trkpt>' for i in range(n_points)
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="t"><trk><trkseg>'
        f"{pts}</trkseg></trk></gpx>"
    ).encode()


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members:
            zf.writestr(name, body)
    return buf.getvalue()


# ── Minimal FIT writer (header + definition + data messages + CRC) ───────────
_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def _crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _fit_records(n: int) -> bytes:
    """FIT file of ``n`` record messages (timestamp, lat, lon, altitude)."""
    body = bytearray()
    # Definition, local type 0 → global 20 (record): 4 fields.
    body += bytes([0x40, 0, 0]) + struct.pack("<HB", 20, 4)
    body += bytes([253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85, 2, 2, 0x84])
    for i in range(n):
        lat = int((44.0 + i * 1e-5) / 180 * 2**31)
        lon = int((3.0 + i * 1e-5) / 180 * 2**31)
        body += bytes([0x00]) + struct.pack("<IiiH", 1_000_000_000 + i, lat, lon, 3000)
    header = struct.pack("<BBHI4s", 14, 0x10, 2100, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    data = header + bytes(body)
    return data + struct.pack("<H", _crc16(data))


@pytest.fixture
def no_xml_parse(monkeypatch):
    """Tripwire: gpxpy must not be reached."""
    def _boom(*_a, **_kw):
        raise AssertionError("gpxpy.parse called on a file that should be rejected before parsing")

    monkeypatch.setattr(gpx_service.gpxpy, "parse", _boom)


@pytest.fixture
def small_coord_cap(monkeypatch):
    monkeypatch.setattr(gpx_service, "MAX_GPX_COORDS", 50)
    return 50


# ── GPX: byte-level caps, parser layer ───────────────────────────────────────

class TestGpxPreParseCaps:
    def test_dense_gpx_rejected_without_calling_gpxpy(self, no_xml_parse, small_coord_cap):
        with pytest.raises(gpx_service.TraceTooDenseError) as exc:
            gpx_service.parse_gpx(_dense_gpx(120))
        # Same user-facing reason as the post-parse coordinate cap.
        assert str(exc.value) == gpx_service.too_many_coords_message(120)
        assert "too many coordinates" in str(exc.value)

    def test_route_and_way_points_count_too(self, no_xml_parse, small_coord_cap):
        body = b'<gpx version="1.1">' + b'<rtept lat="1" lon="1"/>' * 30 + b'<wpt lat="1" lon="1"/>' * 30 + b"</gpx>"
        with pytest.raises(gpx_service.TraceTooDenseError):
            gpx_service.parse_gpx(body)

    def test_element_budget_rejects_non_point_flood(self, no_xml_parse, monkeypatch):
        """A file with few points but a flood of other elements is bounded too
        (the parser's memory follows the element count, not the point count)."""
        monkeypatch.setattr(gpx_service, "MAX_GPX_XML_TAGS", 1000)
        body = b'<gpx version="1.1"><trk><trkseg><trkpt lat="1" lon="1"/><extensions>' + b"<a/>" * 2000
        body += b"</extensions></trkseg></trk></gpx>"
        with pytest.raises(gpx_service.TraceTooDenseError) as exc:
            gpx_service.parse_gpx(body)
        assert "too many XML elements" in str(exc.value)

    def test_under_the_caps_still_parses(self, small_coord_cap):
        parsed = gpx_service.parse_gpx(_dense_gpx(40))
        assert parsed["coord_count"] == 40

    def test_default_caps_keep_a_long_real_ride(self):
        """~38k points with Garmin-style extensions (an ultra ride) passes."""
        n = 38_000
        pt = (
            b'<trkpt lat="44.0000000" lon="3.0000000"><ele>100.0</ele><time>2024-01-01T10:00:00Z</time>'
            b"<extensions><ns3:TrackPointExtension><ns3:atemp>21.0</ns3:atemp><ns3:hr>140</ns3:hr>"
            b"<ns3:cad>85</ns3:cad></ns3:TrackPointExtension></extensions></trkpt>\n"
        )
        body = b'<gpx version="1.1"><trk><trkseg>' + pt * n + b"</trkseg></trk></gpx>"
        gpx_service.check_gpx_bounds(body)  # no raise


# ── GPX: every HTTP / drain path rejects before gpxpy ───────────────────────

class TestGpxCapsOnEveryPath:
    def test_gpx_upload(self, client, auth_headers, no_xml_parse, small_coord_cap):
        resp = client.post(
            "/gpx/upload", headers=auth_headers,
            files={"file": ("dense.gpx", _dense_gpx(120), "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 413, resp.text
        assert "too many coordinates" in resp.json()["detail"]

    def test_imports_files_single_gpx(self, client, auth_headers, no_xml_parse, small_coord_cap):
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("dense.gpx", _dense_gpx(120), "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["imported"] == 0
        assert any("too many coordinates" in e for e in body["errors"]), body

    def test_imports_files_zip_member(self, client, auth_headers, no_xml_parse, small_coord_cap):
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("bulk.zip", _zip([("dense.gpx", _dense_gpx(120))]), "application/zip")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        assert any("too many coordinates" in e for e in resp.json()["errors"]), resp.json()

    def test_archive_drain_member(self, no_xml_parse, small_coord_cap):
        from app.jobs.ingest_pending_archives import _parse_member

        with pytest.raises(gpx_service.TraceTooDenseError):
            _parse_member(_dense_gpx(120), "activities/1.gpx")


# ── FIT: record budget before fitparse ──────────────────────────────────────

class TestFitPreParseCap:
    def test_count_walks_record_headers(self):
        # 25 record messages × (4 fields + 1 per message).
        assert fit_parser.count_fit_field_values(_fit_records(25)) == 25 * 5

    def test_count_ignores_non_fit_bytes(self):
        assert fit_parser.count_fit_field_values(b"not a fit file at all") == 0

    def test_over_budget_fit_rejected_without_decoding(self, monkeypatch):
        monkeypatch.setattr(fit_parser, "MAX_FIT_FIELD_VALUES", 100)

        def _boom(_content):
            raise AssertionError("fitparse reached on a file over the budget")

        monkeypatch.setattr(fit_parser, "_open_fit", _boom)
        with pytest.raises(gpx_service.TraceTooDenseError) as exc:
            fit_parser.parse_fit(_fit_records(25))  # 125 units > 100
        assert "FIT has too many records" in str(exc.value)

    def test_under_budget_fit_parses(self, monkeypatch):
        monkeypatch.setattr(fit_parser, "MAX_FIT_FIELD_VALUES", 1000)
        parsed = fit_parser.parse_fit(_fit_records(25))
        assert parsed["coord_count"] == 25

    def test_imports_files_fit_over_budget(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(fit_parser, "MAX_FIT_FIELD_VALUES", 100)

        def _boom(_content):
            raise AssertionError("fitparse reached on a file over the budget")

        monkeypatch.setattr(fit_parser, "_open_fit", _boom)
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("ride.fit", _fit_records(25), "application/octet-stream")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        assert any("FIT has too many records" in e for e in resp.json()["errors"]), resp.json()


# ── Process-wide parse slot ──────────────────────────────────────────────────

class TestParseSlot:
    def test_parses_never_overlap_beyond_the_slots(self, monkeypatch):
        monkeypatch.setattr(gpx_service, "_PARSE_SLOTS", threading.BoundedSemaphore(1))
        real_parse = gpx_service.gpxpy.parse
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()

        def _tracking_parse(*a, **kw):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            try:
                return real_parse(*a, **kw)
            finally:
                with lock:
                    state["active"] -= 1

        monkeypatch.setattr(gpx_service.gpxpy, "parse", _tracking_parse)
        threads = [threading.Thread(target=gpx_service.parse_gpx, args=(VALID_GPX,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["peak"] == 1, state

    def test_busy_slot_answers_503_without_parsing(self, client, auth_headers, monkeypatch, no_xml_parse):
        slots = threading.BoundedSemaphore(1)
        monkeypatch.setattr(gpx_service, "_PARSE_SLOTS", slots)
        monkeypatch.setattr(gpx_service, "PARSE_SLOT_TIMEOUT_S", 0.05)
        assert slots.acquire(timeout=1)  # another parse holds the only slot
        try:
            up = client.post(
                "/gpx/upload", headers=auth_headers,
                files={"file": ("t.gpx", VALID_GPX, "application/gpx+xml")},
                data={"sport": "road"},
            )
            files = client.post(
                "/imports/files", headers=auth_headers,
                files={"file": ("t.gpx", VALID_GPX, "application/gpx+xml")},
                data={"sport": "road"},
            )
        finally:
            slots.release()
        assert up.status_code == 503, up.text
        assert up.headers.get("Retry-After")
        assert files.status_code == 503, files.text


# ── Internals stay server-side ───────────────────────────────────────────────

class TestGenericErrorMessages:
    def test_gpx_upload_parse_error_is_generic(self, client, auth_headers):
        resp = client.post(
            "/gpx/upload", headers=auth_headers,
            files={"file": ("bad.gpx", b"<gpx><trk><unclosed></gpx>", "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail == "GPX parse error: invalid or corrupted file"
        assert "mismatched" not in detail.lower() and "line" not in detail.lower()

    def test_imports_files_parse_error_is_generic(self, client, auth_headers):
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("bad.gpx", b"<gpx><trk><unclosed></gpx>", "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["errors"] == ["GPX parse error: invalid or corrupted file"]

    def test_imports_files_ingest_error_is_generic(self, client, auth_headers, monkeypatch):
        from app.services import ingest as ingest_service

        def _boom(**_kw):
            raise RuntimeError("relation activities: connection detail 10.0.0.3")

        monkeypatch.setattr(ingest_service, "ingest_activity", _boom)
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("ride.gpx", VALID_GPX, "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["failed"] == 1
        assert not any("10.0.0.3" in e or "relation" in e for e in body["errors"]), body
        assert any("import failed (server error)" in e for e in body["errors"]), body

    def test_zip_member_parse_error_is_generic(self, client, auth_headers):
        resp = client.post(
            "/imports/files", headers=auth_headers,
            files={"file": ("bulk.zip", _zip([("bad.gpx", b"<gpx><trk><unclosed></gpx>")]), "application/zip")},
            data={"sport": "road"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["errors"] == ["bad.gpx: GPX parse failed (invalid or corrupted file)"]
