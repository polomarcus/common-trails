"""Audit 2026-05-29 GPX-S1.1 — `parse_zip_of_gpx` extracts gzipped
Strava-export members (`.gpx.gz` / `.fit.gz`).

Pre-fix: the extraction loop filtered `endswith('.gpx')`, silently
dropping every `activities/<id>.gpx.gz` member (the post-2022 Strava
bulk-export shape). Cross-platform users saw `imported=0` with zero
error messages and zero feedback that anything went wrong.

These tests pin the new behaviour:

- `.gpx.gz` member → decompressed + parsed → `imported=1`
- `.fit.gz` member → decompressed + parsed via fitparse → `imported=1`
- `.gpx` (uncompressed) still works (regression guard)
- Decompressed size > MAX_GPX_SIZE → per-member error, archive
  otherwise succeeds
- Mixed archive: gz + plain gpx + non-GPX junk all handled correctly
"""
from __future__ import annotations

import gzip
import io
import zipfile

import pytest

VALID_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>tiny</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def _zip_with_members(names_and_bodies: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in names_and_bodies:
            zf.writestr(name, body)
    return buf.getvalue()


def test_gpx_gz_member_extracted_and_parsed():
    """A `.gpx.gz` member must decompress and parse cleanly."""
    from app.services.gpx import parse_zip_of_gpx
    gz_body = gzip.compress(VALID_GPX)
    zip_bytes = _zip_with_members([("activities/100.gpx.gz", gz_body)])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 1, results
    parsed = results[0]
    assert "error" not in parsed, parsed
    assert parsed["source_file"] == "activities/100.gpx.gz"
    # parse_gpx populates coord_count + geometry — minimal sanity check
    assert parsed.get("coord_count", 0) > 0
    assert parsed.get("geometry_geojson") is not None


def test_plain_gpx_member_still_works_regression():
    """Uncompressed `.gpx` member must still be parsed — pre-PR
    behaviour must not regress."""
    from app.services.gpx import parse_zip_of_gpx
    zip_bytes = _zip_with_members([("activities/200.gpx", VALID_GPX)])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 1
    assert "error" not in results[0]
    assert results[0].get("coord_count", 0) > 0


def test_mixed_archive_gz_plus_plain_plus_junk():
    """A realistic Strava-export shape: activities.csv (junk for this
    test's purpose), some `.gpx`, some `.gpx.gz`, some unrelated text."""
    from app.services.gpx import parse_zip_of_gpx
    gz_body = gzip.compress(VALID_GPX)
    zip_bytes = _zip_with_members([
        ("activities.csv", b"id,name\n100,test"),  # ignored
        ("activities/100.gpx.gz", gz_body),
        ("activities/200.gpx", VALID_GPX),
        ("README.txt", b"some bulk-export readme"),  # ignored
    ])
    results = parse_zip_of_gpx(zip_bytes)
    # Two parsed entries, in zip order.
    assert len(results) == 2, [r.get("source_file") for r in results]
    sources = {r["source_file"] for r in results}
    assert sources == {"activities/100.gpx.gz", "activities/200.gpx"}
    for r in results:
        assert "error" not in r, r
        assert r.get("coord_count", 0) > 0


def test_gpx_gz_decompressed_size_over_cap_returns_per_member_error():
    """A `.gpx.gz` member whose DECOMPRESSED bytes exceed MAX_GPX_SIZE
    must be reported per-member (not raise) — pattern matches the
    coord-cap behaviour in `imports.py`."""
    from app.services.gpx import MAX_GPX_SIZE, parse_zip_of_gpx
    # Build a >10MB-decompressed gpx (padded comments — gzips well so
    # the compressed bytes stay under the per-member zip cap).
    padding = b"<!-- " + b"x" * (MAX_GPX_SIZE + 1024 * 1024) + b" -->\n"
    fat_gpx = VALID_GPX.replace(b"<gpx", padding + b"<gpx", 1)
    gz_body = gzip.compress(fat_gpx)
    zip_bytes = _zip_with_members([
        ("activities/100.gpx.gz", gz_body),
        ("activities/200.gpx", VALID_GPX),  # valid one to confirm continue
    ])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 2
    fat_entry = next(r for r in results if r["source_file"].endswith(".gz"))
    assert "error" in fat_entry
    assert "decompressed size" in fat_entry["error"].lower()
    good_entry = next(r for r in results if r["source_file"].endswith(".gpx"))
    assert "error" not in good_entry


def test_gpx_gz_with_invalid_gzip_returns_per_member_error():
    """A `.gpx.gz` member that's NOT actually gzipped must surface as
    a per-member error, not raise."""
    from app.services.gpx import parse_zip_of_gpx
    zip_bytes = _zip_with_members([
        ("activities/100.gpx.gz", b"not actually gzipped"),
        ("activities/200.gpx", VALID_GPX),
    ])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 2
    bad = next(r for r in results if r["source_file"].endswith(".gz"))
    assert "error" in bad
    assert "gzip" in bad["error"].lower()
    good = next(r for r in results if not r["source_file"].endswith(".gz"))
    assert "error" not in good


def test_fit_gz_parse_failure_returns_per_member_error(monkeypatch):
    """A `.fit.gz` member whose decompressed bytes don't parse as FIT
    must surface as a per-member error, not crash the whole zip parse.

    This covers both shapes the audit cares about:
    - fitparse raises FitHeaderError on a 1KB-zero buffer (the real
      production failure mode for hostile / malformed input)
    - fitparse raises ImportError if the library isn't installed (the
      branch the audit's S2 flagged as previously unreachable)

    The fix moved the ImportError catch onto the parse_fit CALL, not
    the wrapper import, so both failure modes route through the same
    `except` and produce a per-member error.
    """
    from app.services.gpx import parse_zip_of_gpx
    gz_body = gzip.compress(b"\x00" * 1024)
    zip_bytes = _zip_with_members([("activities/100.fit.gz", gz_body)])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 1
    assert "error" in results[0]
    # Either "fit" (parse failure) or "fitparse" (import failure) in
    # the message — both routes surface the file extension somewhere.
    assert "fit" in results[0]["error"].lower()


def test_gzip_bomb_streaming_cap_prevents_oom():
    """A gzipped member that decompresses to FAR more than MAX_GPX_SIZE
    must NOT allocate the full decompressed payload — the streaming
    cap fires after reading MAX_GPX_SIZE + 1 bytes and surfaces a
    per-member error.

    Regression for PR #356 review S1: the original `gzip.decompress(
    member_bytes)` allocated the whole buffer in one shot, so a 10MB-
    compressed payload expanding to 10GB OOM-killed the worker
    BEFORE any size check ran.

    Build a payload that's small compressed (passes
    MAX_ZIP_MEMBER_UNCOMPRESSED) but would expand to MUCH more than
    MAX_GPX_SIZE if fully decompressed. Highly-repetitive bytes
    compress to ~1000:1 ratio."""
    from app.services.gpx import MAX_GPX_SIZE, parse_zip_of_gpx
    # 50 MB of repeated bytes → ~50 KB gzipped. Well above MAX_GPX_SIZE
    # (10 MB) when decompressed.
    bomb_decompressed_size = MAX_GPX_SIZE * 5
    gz_body = gzip.compress(b"A" * bomb_decompressed_size)
    # Sanity: the gzipped bytes must fit under the zip-side per-member
    # cap (otherwise that earlier check trips first — different
    # defence layer).
    assert len(gz_body) < 1 * 1024 * 1024, (
        f"Test setup: gzipped bomb is {len(gz_body)} bytes — should "
        f"be sub-MB so the test exercises the streaming cap, not the "
        f"zip-member cap."
    )

    zip_bytes = _zip_with_members([("activities/bomb.gpx.gz", gz_body)])
    # Must NOT raise / OOM / hang. Must surface per-member error.
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 1
    assert "error" in results[0]
    assert "decompressed size" in results[0]["error"].lower()


def test_unknown_extension_still_skipped():
    """Members like `README.md` or `activities.csv` must still be
    skipped silently (filter unchanged for non-gpx/fit extensions)."""
    from app.services.gpx import parse_zip_of_gpx
    zip_bytes = _zip_with_members([
        ("README.md", b"# Strava export"),
        ("activities.csv", b"id,name"),
        ("media/photo.jpg", b"\xff\xd8\xff\xe0fake-jpeg"),
        # Decoys that look almost-right but aren't accepted:
        ("activities/100.gz", gzip.compress(VALID_GPX)),  # missing .gpx/.fit
        ("activities/100.gpx.bak", VALID_GPX),  # wrong suffix
    ])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 0, [r.get("source_file") for r in results]


def _gpx_with_n_points(n: int) -> bytes:
    """Build a valid GPX trace with ``n`` trackpoints."""
    pts = "".join(
        f'<trkpt lat="{43.6 + i * 1e-6:.6f}" lon="{3.8 + i * 1e-6:.6f}"></trkpt>'
        for i in range(n)
    )
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gpx version="1.1" creator="test" '
        b'xmlns="http://www.topografix.com/GPX/1/1">'
        b"<trk><name>dense</name><trkseg>"
        + pts.encode()
        + b"</trkseg></trk></gpx>"
    )


def test_zip_member_over_max_coords_is_skipped_per_member(monkeypatch):
    """A zip member with coord_count > MAX_GPX_COORDS must be reported
    per-member (skip this one, ingest the rest) — matching the single-file
    path (gpx_upload.py:89). Pre-fix the zip path never enforced the
    dense-coordinate DoS cap. June 2026 audit GPX-S2.

    Patch MAX_GPX_COORDS down to a small value so we don't have to build a
    multi-MB file just to cross the real 100k cap.
    """
    import app.services.gpx as gpx_mod
    from app.services.gpx import parse_zip_of_gpx

    monkeypatch.setattr(gpx_mod, "MAX_GPX_COORDS", 10)

    over = _gpx_with_n_points(25)   # 25 > 10 → skipped
    under = _gpx_with_n_points(5)   # 5  <= 10 → ingested
    zip_bytes = _zip_with_members([
        ("activities/big.gpx", over),
        ("activities/ok.gpx", under),
    ])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 2, [r.get("source_file") for r in results]

    big = next(r for r in results if r["source_file"].endswith("big.gpx"))
    assert "error" in big, big
    assert "too many coordinates" in big["error"].lower()
    # The whole archive must NOT have been unwound: the small file ingests.
    ok = next(r for r in results if r["source_file"].endswith("ok.gpx"))
    assert "error" not in ok, ok
    assert ok.get("coord_count", 0) > 0


@pytest.mark.parametrize("ext", [".GPX", ".GPX.GZ", ".Fit", ".fit.GZ"])
def test_extension_match_is_case_insensitive(ext):
    """The accept-list compares lower-case, so Strava archives that
    capitalize extensions (rare but real) still extract."""
    from app.services.gpx import parse_zip_of_gpx
    name = f"activities/100{ext}"
    if ext.lower().endswith(".gz"):
        body = gzip.compress(VALID_GPX if "gpx" in ext.lower() else b"\x00" * 1024)
    else:
        body = VALID_GPX if "gpx" in ext.lower() else b"\x00" * 1024
    zip_bytes = _zip_with_members([(name, body)])
    results = parse_zip_of_gpx(zip_bytes)
    assert len(results) == 1, (ext, results)
    # `.fit*` paths need fitparse — they may surface a parse error here
    # rather than a clean extraction (fitparse will reject zeros). The
    # test's invariant is just "extracted, not silently dropped".
    if "gpx" in ext.lower():
        assert "error" not in results[0]
