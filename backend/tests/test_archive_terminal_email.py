"""Terminal-state email after a whole-archive drain (MVP audit gap #3).

After "archive reçue ✓" the user got ZERO feedback, ever. The drain now sends
ONE best-effort email when a ``pending_archives`` row reaches a terminal state:

  * ``done``   → summary with imported / skipped / failed counts + map link;
  * ``failed`` → a WHITELISTED user-friendly reason (the zip-bomb "too many
    members"/"too large" class → "découpez votre export"), never a traceback.

Synthetic ``@strava.local`` addresses are skipped silently, and an email
failure must never affect the row state or the drain summary.

All tests drive the REAL ``drain_pending_archives`` (no inline mirrors); only
``send_email`` is mocked via ``patch.object``.
"""
import io
import uuid
import zipfile
from unittest.mock import MagicMock, patch

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.services import email as email_service

# ── Fixtures (same isolation pattern as test_drain_robustness.py) ────────────


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(sa_text(
            "TRUNCATE pending_archive_files, pending_archives, contribution_consents"
        ))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _local_intake_dir(tmp_path, monkeypatch):
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _gpx_xml(name: str, lat0: float, lon0: float, n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001, elevation=100 + i,
        ))
    return gpx.to_xml()


def _zip_bytes(gpx_count: int = 2) -> bytes:
    """Distinct geometries per member so file_hash dedup never collapses them."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(gpx_count):
            zf.writestr(
                f"activities/{i}.gpx",
                _gpx_xml(f"ride {i}", 44.0 + i * 0.05, 4.0 + i * 0.05),
            )
    return buf.getvalue()


def _mk_user(email: str) -> str:
    from app.db.models import User
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        u = User(email=email, username=f"archiver_{uuid.uuid4().hex[:6]}")
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _mk_uploaded_archive(user_id: str, *, locale: str | None = None) -> str:
    """An ``uploaded`` local-backend archive row with real zip bytes in the
    intake dir, ready for the real drain. Optionally attaches a consent row
    carrying ``locale`` (the /init flow records one; the drain reads it back).
    """
    from app.db.models import ContributionConsent, PendingArchive
    from app.db.session import SessionLocal
    from app.services import archive_intake

    bucket_key = f"archive-intake/{user_id}/{uuid.uuid4()}.zip"
    archive_intake.store_archive_local(bucket_key, _zip_bytes())
    db = SessionLocal()
    try:
        consent_id = None
        if locale is not None:
            consent = ContributionConsent(
                user_id=user_id,
                source="manual_upload",
                consent_version="strava-archive-2026-07-v1",
                consent_text="I consent (ODbL).",
                locale=locale,
            )
            db.add(consent)
            db.flush()
            consent_id = consent.id
        row = PendingArchive(
            user_id=user_id,
            consent_id=consent_id,
            storage_backend="local",
            bucket_key=bucket_key,
            fallback_sport="road",
            contribute_heatmap=True,
            status="uploaded",
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _row(archive_id: str) -> dict:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        r = db.execute(sa_text(
            "SELECT status, imported, last_error FROM pending_archives WHERE id = :id"
        ), {"id": archive_id}).mappings().one()
        return dict(r)
    finally:
        db.close()


def _drain():
    from app.jobs.ingest_pending_archives import drain_pending_archives
    return drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)


# ── (a) done → summary email with counts ─────────────────────────────────────


def test_done_archive_sends_summary_email():
    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    rid = _mk_uploaded_archive(uid)

    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        summary = _drain()

    assert summary["imported"] == 2, summary
    assert _row(rid)["status"] == "done"

    send.assert_called_once()
    to, subject, html = send.call_args.args
    assert to == email
    assert "carte" in subject
    assert "<strong>2</strong> importées" in html
    assert "https://chemins-communs.fr/map" in html


def test_done_archive_zero_imported_adapts_wording():
    """Draining the SAME archive content twice: second pass dedups everything
    → imported == 0 → the "nothing new" wording, not "vos traces sont sur la
    carte"."""
    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    _mk_uploaded_archive(uid)
    with patch.object(email_service, "send_email", MagicMock(return_value=True)):
        assert _drain()["imported"] == 2

    _mk_uploaded_archive(uid)  # same _zip_bytes() geometries → all dedup
    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        summary = _drain()

    assert summary["imported"] == 0, summary
    send.assert_called_once()
    _to, subject, html = send.call_args.args
    assert subject == "Votre archive a été traitée"
    assert "aucune nouvelle trace" in html


def test_consent_locale_en_switches_language():
    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    _mk_uploaded_archive(uid, locale="en")

    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        _drain()

    send.assert_called_once()
    _to, subject, _html = send.call_args.args
    assert subject == "Your rides are on the map 🚴"


# ── (b) synthetic @strava.local user → no email ──────────────────────────────


def test_strava_local_synthetic_email_is_skipped():
    uid = _mk_user(f"strava_{uuid.uuid4().hex[:8]}@strava.local")
    rid = _mk_uploaded_archive(uid)

    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        summary = _drain()

    assert summary["imported"] == 2
    assert _row(rid)["status"] == "done"  # drain unaffected
    send.assert_not_called()


def test_missing_user_is_skipped_silently():
    rid = _mk_uploaded_archive(str(uuid.uuid4()))  # user_id with no users row

    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        _drain()

    assert _row(rid)["status"] == "done"
    send.assert_not_called()


# ── (c) failed (zip-bomb class) → friendly "découpez" copy, no leak ──────────


def test_failed_too_many_members_sends_friendly_reason(monkeypatch):
    from app.services import gpx as gpx_service
    monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 1)  # 2 members > 1 → reject

    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    rid = _mk_uploaded_archive(uid)

    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        summary = _drain()

    assert summary["archives_failed"] == 1
    row = _row(rid)
    assert row["status"] == "failed"
    assert "too many members" in (row["last_error"] or "")

    send.assert_called_once()
    to, subject, html = send.call_args.args
    assert to == email
    assert subject == "Votre archive n'a pas pu être traitée"
    assert "découpez" in html
    # NEVER leak internals: neither the raw stored error nor exception names.
    assert "too many members" not in html
    assert "ZipBombError" not in html
    assert "Traceback" not in html


def test_failed_unknown_error_sends_generic_copy_without_leak():
    from app.services import archive_intake

    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    rid = _mk_uploaded_archive(uid)

    boom = RuntimeError("psycopg2.OperationalError: SSLSocket gone at 0x7f")
    with patch.object(archive_intake, "open_archive_zip", MagicMock(side_effect=boom)), \
         patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        _drain()

    assert _row(rid)["status"] == "failed"
    send.assert_called_once()
    _to, subject, html = send.call_args.args
    assert subject == "Votre archive n'a pas pu être traitée"
    assert "Réessayez" in html
    assert "psycopg2" not in html and "0x7f" not in html


# ── (d) email failure never affects the row / summary ────────────────────────


def test_send_email_raising_leaves_row_terminal_and_summary_intact():
    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    uid = _mk_user(email)
    rid = _mk_uploaded_archive(uid)

    with patch.object(
        email_service, "send_email", MagicMock(side_effect=RuntimeError("resend down"))
    ) as send:
        summary = _drain()

    send.assert_called_once()  # the attempt happened…
    assert summary == {
        "archives": 1, "imported": 2, "skipped": 0, "failed": 0, "archives_failed": 0,
    }  # …but the drain summary is untouched
    row = _row(rid)
    assert row["status"] == "done"  # …and the row stays terminal
    assert row["imported"] == 2


def test_done_email_promotes_the_gpxstudio_visugpx_calque():
    """The "your traces are on the map" email must invite the user to add the
    community heatmap as a custom overlay ("calque") in gpx.studio / VisuGPX,
    with the public raster TileJSON URL. Both locales."""
    from app.services.email import render_archive_done_email

    for locale in ("fr", "en"):
        _subject, html = render_archive_done_email(imported=3, skipped=0, failed=0, locale=locale)
        assert "gpx.studio" in html
        assert "VisuGPX" in html
        assert "raster/tiles.json" in html
        # Default (HEATMAP_PUBLIC_BASE_URL unset) = today's raw GCS host.
        assert "storage.googleapis.com/common-trails-heatmap-prod/raster/tiles.json" in html


def test_done_email_calque_respects_public_base_url_env(monkeypatch):
    """When HEATMAP_PUBLIC_BASE_URL is set (prod → the first-party CDN domain),
    the calque tile URL in the email uses it instead of storage.googleapis.com."""
    from app.services.email import render_archive_done_email

    monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr")
    for locale in ("fr", "en"):
        _subject, html = render_archive_done_email(imported=3, skipped=0, failed=0, locale=locale)
        assert "https://tiles.chemins-communs.fr/raster/tiles.json" in html
        assert "storage.googleapis.com" not in html
