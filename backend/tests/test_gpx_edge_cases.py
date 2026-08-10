"""GPX edge-case parsing — coverage gaps flagged in 2026-05-10 audit.

Existing GPX tests cover the happy path. This file pins behaviour
for the malformed / unusual inputs a real user can send:

  - 0-coord GPX (empty <trk>) — gracefully no geometry, no crash
  - Single-point GPX — geometry is None (LineString needs >= 2 points)
  - Multi-track GPX — coordinates from all tracks combined
  - Multi-segment GPX (track-break in middle) — all segments combined
  - Mixed elevation (some points have <ele>, some don't) — falls back
    to 2D so we don't half-emit 3D coords
  - All-elevation GPX — produces 3D coords [lon, lat, ele]
  - GPX with extreme but valid coords (near pole, near anti-meridian)
  - Mangled XML — raises (caller catches)
  - FIT file masquerading as .gpx — content sniffing should fail in
    parse_gpx; the upload endpoint catches the exception → 422

These don't require a DB; pure parser surface.
"""
from __future__ import annotations

import json

import pytest

from app.services.gpx import parse_gpx


def _gpx(track_xml: str) -> bytes:
    """Wrap a <trk>... block in a minimal valid GPX 1.1 envelope."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  {track_xml}
</gpx>""".encode()


def test_zero_coord_gpx_returns_none_geometry() -> None:
    """An empty <trk> with no segments — parser should return None
    geometry, not crash."""
    out = parse_gpx(_gpx("<trk><name>Empty</name></trk>"))
    assert out["geometry_geojson"] is None
    assert out["coord_count"] == 0
    assert out["distance_m"] in (None, 0, 0.0)


def test_single_point_gpx_is_not_a_linestring() -> None:
    """A single trackpoint isn't a valid LineString. We currently emit
    a 1-coord LineString — it's lenient parsing but downstream
    geom_from_geojson_sql refuses (<2 points). The PARSER should at
    least not blow up, and coord_count tracks reality."""
    out = parse_gpx(_gpx(
        "<trk><trkseg><trkpt lat='43.6' lon='3.87'/></trkseg></trk>"
    ))
    assert out["coord_count"] == 1
    # Either None or a 1-coord ls — both acceptable as long as we don't crash
    if out["geometry_geojson"] is not None:
        coords = json.loads(out["geometry_geojson"])["coordinates"]
        assert len(coords) == 1


def test_multi_track_gpx_combines_all_coords() -> None:
    """Two <trk> blocks → all points aggregate into one LineString."""
    out = parse_gpx(_gpx("""
      <trk><trkseg>
        <trkpt lat='43.6' lon='3.87'/>
        <trkpt lat='43.61' lon='3.88'/>
      </trkseg></trk>
      <trk><trkseg>
        <trkpt lat='43.62' lon='3.89'/>
        <trkpt lat='43.63' lon='3.90'/>
      </trkseg></trk>
    """))
    assert out["coord_count"] == 4


def test_multi_segment_track_combines_all_segments() -> None:
    """Track with two <trkseg> (e.g. GPS lost signal, resumed) — all
    points still combine into one LineString. May produce a long
    straight-line edge between segments; the densifier in ingest.py
    handles that with a track-break sentinel, but the PARSER just
    concatenates."""
    out = parse_gpx(_gpx("""
      <trk><name>break</name>
        <trkseg>
          <trkpt lat='43.60' lon='3.87'/>
          <trkpt lat='43.61' lon='3.88'/>
        </trkseg>
        <trkseg>
          <trkpt lat='43.65' lon='3.92'/>
          <trkpt lat='43.66' lon='3.93'/>
        </trkseg>
      </trk>
    """))
    assert out["coord_count"] == 4


def test_mixed_elevation_falls_back_to_2d() -> None:
    """If ANY point lacks <ele>, the parser strips elevation from ALL
    points to keep coords a uniform 2D shape. Otherwise downstream
    code that does ``for lon, lat in coords`` would crash on a
    [lon, lat, ele] tuple from a sibling point."""
    out = parse_gpx(_gpx("""
      <trk><trkseg>
        <trkpt lat='43.6' lon='3.87'><ele>100</ele></trkpt>
        <trkpt lat='43.61' lon='3.88'/>
      </trkseg></trk>
    """))
    coords = json.loads(out["geometry_geojson"])["coordinates"]
    # all 2D
    assert all(len(c) == 2 for c in coords), (
        f"Mixed elevation should be flattened to 2D, got: {coords}"
    )


def test_all_elevation_produces_3d_coords() -> None:
    """When EVERY point has <ele>, output is 3D [lon, lat, ele]."""
    out = parse_gpx(_gpx("""
      <trk><trkseg>
        <trkpt lat='43.6' lon='3.87'><ele>100</ele></trkpt>
        <trkpt lat='43.61' lon='3.88'><ele>105</ele></trkpt>
        <trkpt lat='43.62' lon='3.89'><ele>110</ele></trkpt>
      </trkseg></trk>
    """))
    coords = json.loads(out["geometry_geojson"])["coordinates"]
    assert all(len(c) == 3 for c in coords)
    # Elevation gain should be > 0
    assert out["elevation_gain_m"] is not None
    assert out["elevation_gain_m"] > 0


def test_extreme_but_valid_coords_near_anti_meridian() -> None:
    """Near the anti-meridian (lon ≈ 180) — valid input, parser doesn't
    add interpolation across the seam (that's the renderer's job)."""
    out = parse_gpx(_gpx("""
      <trk><trkseg>
        <trkpt lat='-30.0' lon='179.95'/>
        <trkpt lat='-30.01' lon='179.99'/>
      </trkseg></trk>
    """))
    coords = json.loads(out["geometry_geojson"])["coordinates"]
    assert coords[0][0] > 179
    assert coords[1][0] > 179


def test_mangled_xml_raises() -> None:
    """Garbage / truncated XML — parser raises (the upload endpoint
    catches and returns 422; we don't pin the exact exception type
    so the test is robust to gpxpy version bumps)."""
    with pytest.raises(Exception):  # noqa: B017 — intentional broad catch (gpxpy version-dependent)
        parse_gpx(b"<?xml version='1.0'?><gpx><trk><trkseg><trkpt lat=")


def test_fit_file_content_does_not_parse_as_gpx() -> None:
    """A FIT file (Garmin/Strava native) starts with binary magic and
    parses as malformed XML. Parser must raise so the upload endpoint
    rejects with 422."""
    # Real FIT file headers start with bytes [length, .FIT magic, ...]
    fake_fit_header = b"\x0e\x10F\xc4.FIT\x00\x00\x00\x00"
    with pytest.raises(Exception):  # noqa: B017 — intentional broad catch (gpxpy version-dependent)
        parse_gpx(fake_fit_header)


def test_file_hash_is_deterministic() -> None:
    """Same content → same SHA256 → same dedup decision in
    ingest_activity (file_hash check). If this changes, re-imports
    of the same file would NOT be deduped → user_count + edge volume
    grows unbounded on every re-upload."""
    content = _gpx("<trk><trkseg><trkpt lat='43.6' lon='3.87'/></trkseg></trk>")
    h1 = parse_gpx(content)["file_hash"]
    h2 = parse_gpx(content)["file_hash"]
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex
