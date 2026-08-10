"""Nested-zip recursion in archive intake — a Garmin "Export All" archive nests
the activity files inside inner zips, so a flat top-level walk finds 0. This
pins: depth-1 recursion finds nested .gpx/.fit; depth-2 is NOT recursed (zip-bomb
depth guard); flat archives are unchanged; the aggregate cap still fires."""
import io
import zipfile

import gpxpy
import gpxpy.gpx
import pytest

from app.services import archive_intake


def _gpx_bytes(name="Nested", lat0=45.0, lon0=4.0, n=6):
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001))
    return gpx.to_xml().encode()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _members(archive_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        return list(archive_intake.iter_zip_members(zf))


def test_flat_archive_still_works():
    arc = _zip({"activities/1.gpx": _gpx_bytes("A"), "activities/2.gpx": _gpx_bytes("B")})
    names = [m[0] for m in _members(arc)]
    assert sum(n.endswith(".gpx") for n in names) == 2


def test_nested_zip_gpx_is_found_depth1():
    # Garmin-ish: outer.zip → inner.zip → activity .gpx
    inner = _zip({"DI_CONNECT/act_1.gpx": _gpx_bytes("Ride1")})
    outer = _zip({"garmin/uploaded.zip": inner, "readme.txt": b"hi"})
    got = _members(outer)
    gpx_members = [m for m in got if m[0].endswith(".gpx")]
    assert len(gpx_members) == 1, [m[0] for m in got]
    # the bytes are the real gpx (parseable)
    assert b"<trk>" in gpx_members[0][1]
    # name is prefixed with the inner-zip path for traceability
    assert "uploaded.zip!" in gpx_members[0][0]


def test_nested_fit_is_found():
    inner = _zip({"act.fit": b"FITDATA-not-real-but-detected-by-extension"})
    outer = _zip({"g.zip": inner})
    names = [m[0] for m in _members(outer)]
    assert any(n.endswith(".fit") for n in names)


def test_depth2_is_NOT_recursed():
    """A zip inside a zip inside a zip: the innermost .gpx must NOT be reached
    (depth-1 only → zip-bomb depth guard)."""
    deepest = _zip({"deep.gpx": _gpx_bytes("Deep")})
    mid = _zip({"mid.zip": deepest})
    outer = _zip({"outer.zip": mid})
    names = [m[0] for m in _members(outer)]
    assert not any(n.endswith(".gpx") for n in names), names


def test_aggregate_cap_fires_on_nested_bomb(monkeypatch):
    from app.services import gpx as gpx_service
    monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 3)
    # 2 inner zips × 3 gpx each = 6 ingestible > cap 3 → ZipBombError.
    inner = _zip({f"a{i}.gpx": _gpx_bytes(f"R{i}") for i in range(3)})
    outer = _zip({"z1.zip": inner, "z2.zip": inner})
    with pytest.raises(gpx_service.ZipBombError):
        _members(outer)


def test_cap_accommodates_a_real_garmin_export():
    """A real Garmin 'Export All' from a multi-year user is 6000+ .fit in one
    nested zip (Paul's measured 6277). The member cap must sit above that or the
    whole export is rejected with ZipBombError. Pins the 2026-08-07 raise."""
    from app.services import gpx as gpx_service
    assert gpx_service.MAX_ZIP_MEMBERS >= 6277
