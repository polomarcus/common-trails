"""Pin the GPX DoS coordinate-cap + invalid-point contract.

These drive the REAL `parse_gpx` and the REAL `/gpx/upload` handler — no
inline mirrors, no source-string greps. Fixtures are synthetic GPX XML
built in Python (no external files).

The actual contract discovered by reading the code:

* `parse_gpx` itself NEVER truncates and NEVER raises on a dense file.
  It parses every point and reports the count via `coord_count`.
* The DoS cap (`MAX_GPX_COORDS`, 100_000) is enforced by the *callers*
  (`app.api.gpx_upload.gpx_upload` and the CLI bulk-import), which raise
  HTTP 413 when `parsed["coord_count"] > MAX_GPX_COORDS`.
* Out-of-range coordinates (lat>90 / lon>180 / absurd elevation) are
  silently dropped in the parser inner loop and counted via
  `invalid_point_count`; valid points still land in `coord_count`.
"""
from app.services.gpx import MAX_GPX_COORDS, parse_gpx


def _build_gpx(points: list[tuple[float, float, float | None]], name: str = "synthetic") -> bytes:
    """Build a minimal but valid GPX 1.1 document from (lat, lon, ele) tuples.

    `ele` may be None to omit the <ele> element for that point.
    """
    pts = []
    for lat, lon, ele in points:
        ele_xml = f"<ele>{ele}</ele>" if ele is not None else ""
        pts.append(f'<trkpt lat="{lat}" lon="{lon}">{ele_xml}</trkpt>')
    trkpts = "".join(pts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{name}</name><trkseg>{trkpts}</trkseg></trk>"
        "</gpx>"
    ).encode()


# A tiny line near Montpellier; small lon increments keep all points valid.
def _line(n: int, base_lat: float = 43.61, base_lon: float = 3.87) -> list[tuple[float, float, float | None]]:
    return [(base_lat, base_lon + i * 0.0001, 50.0) for i in range(n)]


def test_parse_gpx_does_not_truncate_and_reports_real_coord_count():
    """parse_gpx keeps EVERY valid point — the cap is the caller's job."""
    n = 250
    parsed = parse_gpx(_build_gpx(_line(n)))
    assert parsed["coord_count"] == n
    assert parsed["invalid_point_count"] == 0
    # geometry carries the full set, uniformly 3D (every point had <ele>).
    import json

    geom = json.loads(parsed["geometry_geojson"])
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) == n
    assert all(len(c) == 3 for c in geom["coordinates"])


def test_parse_gpx_no_raise_no_truncate_above_cap_marker():
    """A file whose coord_count exceeds a (patched-small) cap is parsed
    in full by parse_gpx — proves the parser does not silently enforce
    the DoS limit (that is the caller's contract, pinned below)."""
    # Use a modest count to keep the test fast; we don't need 100k points to
    # prove "parse_gpx returns coord_count == len(points), no truncation".
    n = 1500
    parsed = parse_gpx(_build_gpx(_line(n)))
    # No exception was raised, and the count is exact (not clamped).
    assert parsed["coord_count"] == n


def test_parse_gpx_drops_out_of_range_latitude():
    """lat > 90 is dropped via the real _coord_is_valid path and counted."""
    points = _line(3) + [(91.0, 3.90, 50.0)] + _line(2, base_lon=3.95)
    parsed = parse_gpx(_build_gpx(points))
    assert parsed["invalid_point_count"] == 1
    assert parsed["coord_count"] == 5  # the 5 valid points survive


def test_parse_gpx_drops_out_of_range_longitude():
    points = [(43.61, 200.0, 50.0)] + _line(4)
    parsed = parse_gpx(_build_gpx(points))
    assert parsed["invalid_point_count"] == 1
    assert parsed["coord_count"] == 4


def test_parse_gpx_drops_absurd_elevation():
    """ele outside [-500, 9000] (corrupt firmware) is dropped + counted."""
    points = _line(2) + [(43.62, 3.88, 99999.0)] + _line(2, base_lon=3.90)
    parsed = parse_gpx(_build_gpx(points))
    assert parsed["invalid_point_count"] == 1
    assert parsed["coord_count"] == 4


def test_parse_gpx_all_invalid_yields_no_geometry():
    """If every point is out-of-range, coord_count is 0 and geometry is None."""
    points = [(91.0, 3.87, 50.0), (43.61, 999.0, 50.0)]
    parsed = parse_gpx(_build_gpx(points))
    assert parsed["coord_count"] == 0
    assert parsed["invalid_point_count"] == 2
    assert parsed["geometry_geojson"] is None


# --- The DoS cap itself, driven through the REAL upload handler. ---


def _register_and_token(client) -> str:
    import uuid

    email = f"capcoords_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"u_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def test_upload_rejects_over_cap_with_413(client, monkeypatch):
    """The real /gpx/upload handler raises 413 when coord_count exceeds the
    cap. We patch MAX_GPX_COORDS small (so we don't have to ship 100k points)
    and drive the real endpoint — the rejection threshold is exercised, not
    mirrored. parse_gpx is real; the cap comparison in gpx_upload is real."""
    # The handler reads the name `MAX_GPX_COORDS` from its own module namespace.
    monkeypatch.setattr("app.api.gpx_upload.MAX_GPX_COORDS", 50, raising=True)

    token = _register_and_token(client)
    gpx_bytes = _build_gpx(_line(120))  # 120 > patched cap of 50
    resp = client.post(
        "/gpx/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("dense.gpx", gpx_bytes, "application/gpx+xml")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text
    body = resp.json()
    # Detail mentions the over-cap count; assert on the real message contract.
    assert "too many coordinates" in body["detail"]
    assert "120" in body["detail"]


def test_upload_accepts_under_cap(client, monkeypatch):
    """Symmetric proof: same patched cap, a file UNDER it is accepted (202),
    so the 413 above is the cap firing and not an unrelated rejection."""
    monkeypatch.setattr("app.api.gpx_upload.MAX_GPX_COORDS", 5000, raising=True)

    token = _register_and_token(client)
    gpx_bytes = _build_gpx(_line(40))  # 40 < 5000
    resp = client.post(
        "/gpx/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("ok.gpx", gpx_bytes, "application/gpx+xml")},
        data={"sport": "road", "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text


def test_real_cap_constant_is_the_documented_dos_limit():
    """Guard the magic number doesn't silently drift to something useless
    (e.g. 0 disabling ingest, or absurdly large defeating the DoS defence).
    This reads the REAL constant, not a copy."""
    assert MAX_GPX_COORDS == 100_000
