"""Non-regression: Garmin "Export All" archives nest activity files INSIDE inner
``UploadedFiles_*.zip`` zips. ``iter_zip_members`` emits them as
``<inner.zip>!<member>``; the drain's PASS 2 must read them from the inner zip,
NOT ``zf.read()`` the composite name off the OUTER archive — which KeyErrors and
was the root cause of a real Garmin archive draining with ~100% member failures
(``imported: 0, failed: 16361``, tester 2, 2026-08-13).

Also pins the single-file size cap: real long Garmin rides run 12-20 MB, which the
old 10 MB ``MAX_GPX_SIZE`` 413'd.
"""
import io
import zipfile

from app.services import archive_intake
from app.services import gpx as gpx_service


def _mk_gpx(name: str = "Ride") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="Garmin Connect" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name><type>road_biking</type><trkseg>"
        '<trkpt lat="43.61" lon="3.89"><ele>21</ele>'
        "<time>2026-08-09T05:30:52.000Z</time></trkpt>"
        '<trkpt lat="43.62" lon="3.90"><ele>25</ele>'
        "<time>2026-08-09T05:31:52.000Z</time></trkpt>"
        "</trkseg></trk></gpx>"
    ).encode()


def _zip_of(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in members.items():
            z.writestr(n, b)
    return buf.getvalue()


def _mk_garmin_archive() -> bytes:
    """Outer zip whose only ingestible content is .gpx files inside an inner
    ``UploadedFiles_*.zip`` — the Garmin 'Export All' layout."""
    inner = _zip_of({
        "simon@example.com_456984147534.gpx": _mk_gpx("Morning Ride"),
        "simon@example.com_458924523384.gpx": _mk_gpx("Evening Ride"),
    })
    return _zip_of({
        "DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_20250094-_Part1.zip": inner,
    })


def test_iter_zip_members_yields_nested_members_as_composite_names():
    with zipfile.ZipFile(io.BytesIO(_mk_garmin_archive())) as zf:
        names = [n for (n, _raw, _s) in archive_intake.iter_zip_members(zf)]
    assert any("!" in n and n.endswith("_456984147534.gpx") for n in names), names
    assert any("!" in n and n.endswith("_458924523384.gpx") for n in names), names


def test_read_planned_member_resolves_nested_composite_name():
    """The exact PASS-2 read the drain performs. OLD code did
    ``zf.read('<inner.zip>!<member>')`` → KeyError on every Garmin member; the
    fix descends into the inner zip and returns the real bytes."""
    with zipfile.ZipFile(io.BytesIO(_mk_garmin_archive())) as zf:
        names = [n for (n, _r, _s) in archive_intake.iter_zip_members(zf)]
        nested = next(n for n in names if n.endswith("_456984147534.gpx"))
        assert "!" in nested

        # Reproduce the old failure: the composite name is NOT an outer-zip entry.
        raised = False
        try:
            zf.read(nested)
        except KeyError:
            raised = True
        assert raised, "composite name unexpectedly present in the outer zip"

        # The fix returns the real inner GPX bytes (no KeyError).
        inner_cache: dict = {}
        raw = archive_intake.read_planned_member(zf, nested, inner_cache)
        archive_intake.close_inner_cache(inner_cache)
        assert raw.lstrip().startswith(b"<?xml"), raw[:40]
        assert b"road_biking" in raw


def test_read_planned_member_flat_member_unchanged():
    """A non-nested (flat) member still reads straight from the outer zip."""
    archive = _zip_of({"activities/ride.gpx": _mk_gpx("Flat")})
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        raw = archive_intake.read_planned_member(zf, "activities/ride.gpx", {})
    assert b"road_biking" in raw


def test_read_planned_member_flat_name_with_bang_reads_from_outer():
    """A FLAT member whose own filename contains '!' must still read from the
    outer archive — only a '<inner.zip>!<member>' composite is nested."""
    archive = _zip_of({"activities/ride!2.gpx": _mk_gpx("Bang")})
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        raw = archive_intake.read_planned_member(zf, "activities/ride!2.gpx", {})
    assert b"road_biking" in raw


def test_gpx_size_cap_accepts_real_garmin_exports():
    """A real single Garmin ride (the reported activite_bug.gpx) was 15.6 MB and
    413'd at the old 10 MB cap. The byte cap must accommodate real long rides —
    the 100k coord cap is the actual dense-coordinate DoS defence."""
    assert gpx_service.MAX_GPX_SIZE >= 16 * 1024 * 1024
    assert gpx_service.MAX_ZIP_MEMBER_UNCOMPRESSED >= 16 * 1024 * 1024
