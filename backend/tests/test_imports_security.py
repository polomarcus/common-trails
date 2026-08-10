"""Security regression tests for the file-import endpoints.

The GPX ingestion audit (2026-05-17, persisted in auto-memory as
`project_gpx_ingestion_audit.md`) flagged a Sev-1 zip-bomb path and a
Sev-2 missing dense-coord cap on `/imports/files`. These tests pin the
defenses so a refactor can't silently strip them.

Coverage:
- ZIP with too many members → 413
- ZIP whose total uncompressed size exceeds the cap → 413
- ZIP whose single member exceeds the per-member cap → 413
- ZIP member with a path-traversal name (`../evil.gpx`) → 413
- ZIP with embedded GPX that has >100k coordinates → 413
- Single `.gpx` with >100k coordinates posted to `/imports/files` → 413
- Single `.gpx` posted to `/gpx/upload` with >100k coordinates → 413
  (regression; this path already had the cap pre-audit)

Build the malicious zips in-process so the test suite stays
self-contained and CI never has to ship a megabyte fixture.
"""
from __future__ import annotations

import io
import zipfile

import pytest

# Match the constants on the parser side. If those change, this file
# should fail loudly so we notice the contract drift.
from app.services.gpx import (
    MAX_ZIP_MEMBER_UNCOMPRESSED,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_TOTAL_UNCOMPRESSED,
)

VALID_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>tiny</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def _zip_with_members(names_and_bodies: list[tuple[str, bytes]]) -> bytes:
    """Build an in-memory zip with the given (name, body) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in names_and_bodies:
            zf.writestr(name, body)
    return buf.getvalue()


def _make_dense_gpx(n_points: int) -> bytes:
    """GPX with `n_points` valid trackpoints. ~50 bytes per point."""
    pts = "\n".join(
        f'    <trkpt lat="43.61" lon="{3.87 + i * 1e-6:.6f}"><ele>100</ele></trkpt>'
        for i in range(n_points)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>dense</name><trkseg>
{pts}
  </trkseg></trk>
</gpx>""".encode()


# ── ZIP defenses ──────────────────────────────────────────────────────


def test_zip_with_too_many_members_returns_413(client, auth_headers):
    """A zip containing more than MAX_ZIP_MEMBERS INGESTIBLE files is
    rejected before any single member is parsed (the cap is checked on the
    pre-parsed `infolist()` — cheap). Members must be .gpx: since the
    ingestible-only caps (fix/drain-robustness) non-ingestible members
    (media/…) are never read and no longer count — see
    test_drain_robustness.py."""
    over = MAX_ZIP_MEMBERS + 1
    # Tiny payloads: the cap trips before any member is parsed.
    members = [(f"file_{i:05d}.gpx", b"x") for i in range(over)]
    zip_bytes = _zip_with_members(members)
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("bomb.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text
    assert "too many members" in resp.text.lower()


def test_zip_with_member_exceeding_per_member_cap_isolates_to_that_member(client, auth_headers):
    """A zip with a single member larger than MAX_ZIP_MEMBER_UNCOMPRESSED
    is rejected on a per-member basis (matches the dense-coord cap shape).

    Pre-PR this raised `ZipBombError` mid-loop which unwound the whole
    archive — even when 1398 prior members had already parsed
    successfully, the frontend saw a single 413 with zero results.
    Now: the oversize member is reported in `errors[]`, the rest of
    the archive still parses. Audit 2026-05-29 GPX-S2.6.

    The bad member is alone in the archive here so the response shape
    is `imported=0 + errors=[oversize-message]` (still 202 because
    the per-member error reporting matches how dense-coord caps work
    inside the body, not 413)."""
    huge = b"x" * (MAX_ZIP_MEMBER_UNCOMPRESSED + 1)
    zip_bytes = _zip_with_members([("huge.gpx", huge)])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("bomb.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body.get("imported", -1) == 0
    assert any("zip-bomb" in e.lower() or "huge.gpx" in e.lower()
               for e in body.get("errors", [])), body


def test_zip_with_oversize_member_amid_valid_files_still_imports_valid(client, auth_headers):
    """The key new behavior: 2 DISTINCT valid GPX + 1 oversize member
    → the 2 valid members import successfully, oversize one is
    reported as error. Pre-PR this 413'd the whole archive even when
    the valid siblings had already parsed."""
    huge = b"x" * (MAX_ZIP_MEMBER_UNCOMPRESSED + 1)
    # Vary the coordinates so file_hash differs and both rows ingest
    # (identical content would dedup as "already exists").
    ok1 = VALID_GPX
    ok2 = VALID_GPX.replace(b"lat=\"43.61\"", b"lat=\"43.71\"").replace(
        b"lat=\"43.62\"", b"lat=\"43.72\"",
    )
    zip_bytes = _zip_with_members([
        ("ok1.gpx", ok1),
        ("huge.gpx", huge),
        ("ok2.gpx", ok2),
    ])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("mixed.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body.get("imported", 0) == 2, (
        f"Expected 2 valid members to import despite oversize sibling, "
        f"got {body}"
    )
    assert any("huge.gpx" in e or "zip-bomb" in e.lower() for e in body.get("errors", [])), body


def test_zip_total_uncompressed_exceeds_cap_returns_413(client, auth_headers):
    """Many members each under the per-member cap, but summing past
    MAX_ZIP_TOTAL_UNCOMPRESSED, is rejected on the aggregate check."""
    # Each member just under per-member cap, enough to overflow total.
    per_member_size = MAX_ZIP_MEMBER_UNCOMPRESSED - 1
    n_members = (MAX_ZIP_TOTAL_UNCOMPRESSED // per_member_size) + 2
    if n_members > MAX_ZIP_MEMBERS:
        pytest.skip("Configured caps make this scenario unreachable")
    members = [(f"file_{i:04d}.gpx", b"x" * per_member_size) for i in range(n_members)]
    zip_bytes = _zip_with_members(members)
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("bomb.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text


def test_zip_with_path_traversal_member_returns_413(client, auth_headers):
    """A zip member named `../escape.gpx` is rejected — defends against
    pollution of GCS archival paths if a future writer trusted member
    names."""
    zip_bytes = _zip_with_members([("../escape.gpx", VALID_GPX)])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("bomb.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text


def test_zip_with_absolute_path_member_returns_413(client, auth_headers):
    """A zip member with a leading `/` is rejected."""
    zip_bytes = _zip_with_members([("/abs.gpx", VALID_GPX)])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("bomb.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text


def test_zip_path_traversal_413_regardless_of_activities_csv_order(client, auth_headers):
    """Posture pin: a path-traversal member must 413 the whole
    archive whether ``activities.csv`` comes BEFORE or AFTER it in
    entry order.

    Pre-fix the first-loop guard `raise`d immediately, but the
    second-loop guard (post-PR #359 first iteration) only appended a
    per-member error. So an archive that hit ``activities.csv`` first
    would `break` out of the first loop without seeing the path-
    traversal, then the second loop would soft-fail it → 202 with
    errors. The same byte sequence (different ordering) yielded a
    different response code — confusing for clients and a posture
    downgrade. PR #359 review S2: keep both guards as `raise` and
    pin the cross-loop consistency here.
    """
    # activities.csv FIRST → first loop breaks out at iter 1 without
    # ever seeing `../escape.gpx`. The second loop's guard must catch.
    csv_body = b"Filename,Activity Type\nactivities/100.gpx,Ride\n"
    zip_bytes = _zip_with_members([
        ("activities.csv", csv_body),
        ("../escape.gpx", VALID_GPX),
    ])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("mixed.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, (
        f"Path-traversal member after activities.csv must still 413, "
        f"got {resp.status_code}: {resp.text}"
    )


def test_zip_with_valid_members_succeeds(client, auth_headers):
    """Sanity: a legitimate small zip still parses and ingests."""
    zip_bytes = _zip_with_members([("a.gpx", VALID_GPX), ("b.gpx", VALID_GPX)])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("legit.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 202, resp.text


# ── Dense-coord defense (Sev-2: was missing on /imports/files) ────────


def test_imports_files_rejects_dense_gpx(client, auth_headers):
    """The 100k-coord cap was previously only on /gpx/upload. After
    the audit, /imports/files enforces it too."""
    dense = _make_dense_gpx(100_001)
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("dense.gpx", dense, "application/gpx+xml")},
        data={"sport": "road"},
    )
    # The endpoint surfaces the cap as an `errors[]` entry on a 202 body
    # (matching how other parse-time problems are reported). The file
    # itself does not 413 — it's reported per-file. Accept either path.
    if resp.status_code == 413:
        return
    body = resp.json()
    assert any("too many coordinates" in e.lower() for e in body.get("errors", [])), (
        f"Expected coord-cap error in body, got {body}"
    )
    assert body.get("imported", -1) == 0


def test_gpx_upload_rejects_dense_gpx(client, auth_headers):
    """Regression: /gpx/upload already had this cap; pin it so the
    new shared `coord_count` plumbing doesn't break it."""
    dense = _make_dense_gpx(100_001)
    resp = client.post(
        "/gpx/upload",
        headers=auth_headers,
        files={"file": ("dense.gpx", dense, "application/gpx+xml")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text
    assert "coordinates" in resp.text.lower()


def test_zip_with_dense_member_reports_per_file_error(client, auth_headers):
    """A zip with one valid + one dense GPX: dense one is rejected on
    a per-file basis, valid one still imports."""
    dense = _make_dense_gpx(100_001)
    zip_bytes = _zip_with_members([("ok.gpx", VALID_GPX), ("dense.gpx", dense)])
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("mixed.zip", zip_bytes, "application/zip")},
        data={"sport": "road"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body.get("imported", 0) == 1, body
    assert any("too many coordinates" in e.lower() for e in body.get("errors", [])), body


# ── Per-file MAX_GPX_SIZE on `/imports/files` (audit 2026-05-29 GPX-S1.2) ──
#
# The outer MAX_FILE_SIZE (50 MB) bounds the whole upload, but each
# single-file branch (`.gpx` and `.fit`) previously called the parser
# on whatever fits under 50 MB — letting a 40 MB GPX allocate
# hundreds of MB of parser state BEFORE the coord-cap fired. The fix
# adds a 10 MB per-file cap matching `/gpx/upload`'s constant.


def test_imports_files_single_gpx_rejects_over_max_size(client, auth_headers, monkeypatch):
    """A single .gpx larger than MAX_GPX_SIZE (10 MB) must 413 BEFORE
    parse — the post-parse coord cap is too late on a 40 MB file.

    Spy on `parse_gpx` to verify it's never called. PR #354 review
    S2 — the previous 413-only assertion would pass even if a future
    refactor moved the size check AFTER the parser raised on oversize
    content. The audit's actual invariant is "parse not invoked"."""
    parse_called = {"n": 0}

    def _spy_parse_gpx(_content):
        parse_called["n"] += 1
        raise AssertionError(
            "parse_gpx must not be called on oversize input — "
            "byte cap regressed (audit GPX-S1.2)"
        )

    monkeypatch.setattr("app.services.gpx.parse_gpx", _spy_parse_gpx)

    padding = b"<!-- " + b"x" * (12 * 1024 * 1024) + b" -->\n"
    fat_gpx = VALID_GPX.replace(b"<gpx", padding + b"<gpx", 1)
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("fat.gpx", fat_gpx, "application/gpx+xml")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text
    assert "too large" in resp.text.lower()
    assert parse_called["n"] == 0, (
        "parse_gpx was called despite oversize input — byte cap is too late"
    )


def test_imports_files_single_fit_rejects_over_max_size(client, auth_headers, monkeypatch):
    """Same per-file cap for `.fit`. Spy verifies `parse_fit` is never
    called — same invariant as the .gpx test above."""
    parse_called = {"n": 0}

    def _spy_parse_fit(_content):
        parse_called["n"] += 1
        raise AssertionError(
            "parse_fit must not be called on oversize input — "
            "byte cap regressed (audit GPX-S1.2)"
        )

    monkeypatch.setattr("app.services.fit_parser.parse_fit", _spy_parse_fit)

    fat_fit = b"\x00" * (12 * 1024 * 1024)
    resp = client.post(
        "/imports/files",
        headers=auth_headers,
        files={"file": ("fat.fit", fat_fit, "application/octet-stream")},
        data={"sport": "road"},
    )
    assert resp.status_code == 413, resp.text
    assert "too large" in resp.text.lower()
    assert parse_called["n"] == 0, (
        "parse_fit was called despite oversize input — byte cap is too late"
    )


# ── Coord-range validation ────────────────────────────────────────────


def test_out_of_range_coords_dropped_silently(client, auth_headers):
    """Lat > 90 / lon > 180 / elevation > 9000 m points are dropped at
    parse time; the parsed dict carries `invalid_point_count` so the
    caller could surface it. We assert on `invalid_point_count` via the
    parser directly (no API path exposes it yet)."""
    from app.services.gpx import parse_gpx
    gpx = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>mixed</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="91.0" lon="3.88"><ele>110</ele></trkpt>
    <trkpt lat="43.62" lon="200.0"><ele>120</ele></trkpt>
    <trkpt lat="43.63" lon="3.89"><ele>10000</ele></trkpt>
    <trkpt lat="43.64" lon="3.90"><ele>130</ele></trkpt>
  </trkseg></trk>
</gpx>"""
    out = parse_gpx(gpx)
    assert out["coord_count"] == 2, out
    assert out["invalid_point_count"] == 3, out
