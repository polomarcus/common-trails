"""Audit 2026-05-27 S2.2 + S2.4 regression — bulk-folder CLI parity with HTTP paths.

`app/cli/import_gpx_folder.py` is the operator-driven bulk-import CLI.
It used to:
- Ignore `<trk><type>` and stamp every file with `--sport` (S2.2)
- Skip the `MAX_GPX_COORDS` check that `/gpx/upload` + `/imports/files`
  enforce, so a 5M-coord file in a 1000-file folder OOM'd the worker (S2.4)

The HTTP-path skip-out-of-scope rule (yoga / virtual / etc.) is already
exercised by `test_gpx_track_type_skip.py`; this file pins the new
behaviour added to the CLI in PR #(forthcoming).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.cli.import_gpx_folder import _ingest_one
from app.db.models import User
from app.db.session import SessionLocal


@pytest.fixture
def fresh_user() -> str:
    db = SessionLocal()
    try:
        u = User(
            id=str(uuid.uuid4()),
            email=f"cligpx_{uuid.uuid4().hex[:8]}@example.com",
            username=f"cligpx_{uuid.uuid4().hex[:6]}",
            hashed_password=None,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _write_gpx(tmp_path: Path, name: str, track_type: str | None, n_points: int = 3) -> Path:
    """Build a minimal valid GPX with the given <trk><type> tag and N points."""
    type_xml = f"<type>{track_type}</type>" if track_type else ""
    pts = "\n".join(
        f'      <trkpt lat="45.5{i:04d}" lon="4.5{i:04d}"><ele>200</ele></trkpt>'
        for i in range(n_points)
    )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <trk>
    <name>test-{name}</name>
    {type_xml}
    <trkseg>
{pts}
    </trkseg>
  </trk>
</gpx>"""
    p = tmp_path / f"{name}.gpx"
    p.write_text(body)
    return p


# ── S2.2 — sport cascade ─────────────────────────────────────────────


def test_cli_prefers_granular_gpx_type_over_operator_sport(fresh_user, tmp_path):
    """Garmin Connect `<trk><type>mountain_biking</type>` must override
    the operator's `--sport gravel` flag. Before the fix the operator
    sport always won, mis-labelling Garmin-export MTB files as gravel."""
    gpx_path = _write_gpx(tmp_path, "garmin_mtb", "mountain_biking")
    status, _msg = _ingest_one(
        user_id=fresh_user,
        path=gpx_path,
        sport="gravel",      # operator's flag
        contribute=False,
    )
    assert status == "created"

    # Verify the activity landed with sport=mtb, not gravel.
    from app.db.models import Activity
    db = SessionLocal()
    try:
        act = db.query(Activity).filter(Activity.user_id == fresh_user).first()
        assert act is not None
        assert act.sport == "mtb", (
            f"GPX <type>=mountain_biking must override --sport=gravel; got {act.sport}"
        )
    finally:
        db.close()


def test_cli_falls_back_to_operator_sport_when_gpx_type_absent(fresh_user, tmp_path):
    """Strava exports collapse `<trk><type>` to generic `cycling` — the
    classifier returns None, and the cascade falls through to the
    operator's `--sport` flag. Same behaviour as `/imports/files`."""
    gpx_path = _write_gpx(tmp_path, "strava_generic", "cycling")
    status, _msg = _ingest_one(
        user_id=fresh_user,
        path=gpx_path,
        sport="road",
        contribute=False,
    )
    assert status == "created"

    from app.db.models import Activity
    db = SessionLocal()
    try:
        act = db.query(Activity).filter(Activity.user_id == fresh_user).first()
        assert act is not None
        assert act.sport == "road", f"generic cycling should fall back to --sport=road; got {act.sport}"
    finally:
        db.close()


def test_cli_falls_back_when_gpx_type_missing(fresh_user, tmp_path):
    """No `<type>` tag at all → fall through to --sport."""
    gpx_path = _write_gpx(tmp_path, "notype", None)
    status, _msg = _ingest_one(
        user_id=fresh_user,
        path=gpx_path,
        sport="offroad",
        contribute=False,
    )
    assert status == "created"

    from app.db.models import Activity
    db = SessionLocal()
    try:
        act = db.query(Activity).filter(Activity.user_id == fresh_user).first()
        assert act is not None
        assert act.sport == "offroad"
    finally:
        db.close()


# ── S2.4 — coord cap ─────────────────────────────────────────────────


def test_cli_rejects_gpx_over_max_coords(fresh_user, tmp_path):
    """A GPX with > MAX_GPX_COORDS points must be rejected before
    ingest. Same defence as `/gpx/upload`. We patch the constant down
    so we don't have to materialise 100k <trkpt> entries on disk.

    Constants live in `app.services.gpx` (canonical layer); the patch
    targets that module so both the HTTP handler and the CLI see it.
    """
    # Build a 10-point file, lower the cap to 5 to simulate over-cap.
    gpx_path = _write_gpx(tmp_path, "huge", "mountain_biking", n_points=10)
    with patch("app.services.gpx.MAX_GPX_COORDS", 5):
        status, msg = _ingest_one(
            user_id=fresh_user,
            path=gpx_path,
            sport="mtb",
            contribute=False,
        )
    assert status == "error"
    assert msg is not None and "coords" in msg


def test_cli_accepts_gpx_under_max_coords(fresh_user, tmp_path):
    """Sanity — a small file under the cap must still ingest."""
    gpx_path = _write_gpx(tmp_path, "small", "mountain_biking", n_points=10)
    with patch("app.services.gpx.MAX_GPX_COORDS", 100):
        status, _msg = _ingest_one(
            user_id=fresh_user,
            path=gpx_path,
            sport="mtb",
            contribute=False,
        )
    assert status == "created"


def test_cli_rejects_gpx_over_max_size_before_parse(fresh_user, tmp_path):
    """Byte-level cap MUST fire BEFORE parse_gpx — otherwise a single
    5M-coord file allocates 300-500 MB before the coord-cap fires (PR
    #347 review #12). Patch MAX_GPX_SIZE down so a tiny file trips it
    AND verify parse_gpx was never called."""
    gpx_path = _write_gpx(tmp_path, "small_but_capped", "mountain_biking", n_points=3)
    parsed_called = {"count": 0}

    def _spy_parse_gpx(content):  # noqa: ARG001
        parsed_called["count"] += 1
        raise AssertionError("parse_gpx was called despite oversize file — byte cap is too late")

    with patch("app.services.gpx.MAX_GPX_SIZE", 50), \
         patch("app.services.gpx.parse_gpx", _spy_parse_gpx):
        status, msg = _ingest_one(
            user_id=fresh_user,
            path=gpx_path,
            sport="mtb",
            contribute=False,
        )
    assert status == "error"
    assert msg is not None and "too large" in msg
    assert parsed_called["count"] == 0, (
        "parse_gpx must not be invoked when the file exceeds MAX_GPX_SIZE — "
        "regression: the byte cap is checked AFTER the parse (S2.4 review #12)"
    )
