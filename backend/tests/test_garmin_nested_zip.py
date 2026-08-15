"""Non-regression: Garmin "Export All" archives nest activity files INSIDE inner
``UploadedFiles_*.zip`` zips. The drain ingests archive members in a SINGLE pass,
straight from the bytes ``archive_intake.iter_zip_members`` yields — so the
load-bearing invariant is that iter_zip_members surfaces each nested member as
``<inner.zip>!<member>`` WITH its real inner bytes (a bug here reproduced tester
2's Garmin archive draining ``imported:0, failed:16361`` on 2026-08-13, before the
single-pass fix).

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


def test_iter_zip_members_yields_nested_members_with_real_inner_bytes():
    """The single-pass drain ingests the bytes iter_zip_members yields, so each
    Garmin nested member must surface as ``<inner.zip>!<member>`` WITH its real
    inner GPX bytes (not the outer-zip entry, which doesn't exist → the old
    ~100% Garmin failure)."""
    with zipfile.ZipFile(io.BytesIO(_mk_garmin_archive())) as zf:
        members = list(archive_intake.iter_zip_members(zf))
    by_name = {n: raw for (n, raw, _s) in members}
    nested = [n for n in by_name if "!" in n]
    assert any(n.endswith("_456984147534.gpx") for n in nested), nested
    assert any(n.endswith("_458924523384.gpx") for n in nested), nested
    # The yielded bytes are the REAL inner GPX (this is what the drain ingests).
    for n in nested:
        raw = by_name[n]
        assert isinstance(raw, bytes)
        assert raw.lstrip().startswith(b"<?xml"), raw[:40]
        assert b"road_biking" in raw
        # And NOT reachable as a flat outer-zip entry (the old KeyError path).
        with zipfile.ZipFile(io.BytesIO(_mk_garmin_archive())) as zf2:
            raised = False
            try:
                zf2.read(n)
            except KeyError:
                raised = True
            assert raised, f"{n} should not be a flat outer-zip entry"


def test_iter_zip_members_flat_member_reads_normally():
    """A non-nested (flat) member yields straight from the outer zip."""
    with zipfile.ZipFile(io.BytesIO(_zip_of({"activities/ride.gpx": _mk_gpx("Flat")}))) as zf:
        members = list(archive_intake.iter_zip_members(zf))
    names = [n for (n, _r, _s) in members]
    assert "activities/ride.gpx" in names
    raw = next(r for (n, r, _s) in members if n == "activities/ride.gpx")
    assert b"road_biking" in raw


def test_gpx_size_cap_accepts_real_garmin_exports():
    """A real single Garmin ride (the reported activite_bug.gpx) was 15.6 MB and
    413'd at the old 10 MB cap. The byte cap must accommodate real long rides —
    the 100k coord cap is the actual dense-coordinate DoS defence."""
    assert gpx_service.MAX_GPX_SIZE >= 16 * 1024 * 1024
    assert gpx_service.MAX_ZIP_MEMBER_UNCOMPRESSED >= 16 * 1024 * 1024
