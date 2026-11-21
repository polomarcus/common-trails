"""Log lines and object keys never carry raw client data: recipient addresses
are masked, uploaded file / archive member names are reduced to safe
characters and a bounded length."""
from __future__ import annotations

import io
import logging
import zipfile

import pytest

from app.services import email as email_service
from app.services.email import mask_email
from app.services.gpx_archive import SAFE_NAME_MAX_LEN, safe_filename

ADDRESS = "jean.dupont@example.org"


# ── Recipient addresses ──────────────────────────────────────────────────────

class TestMaskedRecipients:
    def test_mask_email(self):
        assert mask_email(ADDRESS) == "j***@example.org"
        assert mask_email("not-an-address") == "***"
        assert mask_email(None) == "***"

    def test_successful_send_logs_masked_address(self, monkeypatch, caplog):
        class _Resp:
            status_code = 200
            text = "{}"

        monkeypatch.setattr(email_service, "TEST_MODE", False)
        monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(email_service.httpx, "post", lambda *a, **kw: _Resp())
        with caplog.at_level(logging.DEBUG, logger="app.services.email"):
            assert email_service.send_email(ADDRESS, "Sujet", "<p>x</p>") is True
        assert ADDRESS not in caplog.text
        assert "j***@example.org" in caplog.text

    def test_failed_send_logs_masked_address(self, monkeypatch, caplog):
        class _Resp:
            status_code = 422
            text = '{"message": "invalid"}'

        monkeypatch.setattr(email_service, "TEST_MODE", False)
        monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(email_service.httpx, "post", lambda *a, **kw: _Resp())
        with caplog.at_level(logging.DEBUG, logger="app.services.email"):
            assert email_service.send_email(ADDRESS, "Sujet", "<p>x</p>") is False
        assert ADDRESS not in caplog.text

    def test_test_mode_and_missing_key_paths_mask_too(self, monkeypatch, caplog):
        with caplog.at_level(logging.DEBUG, logger="app.services.email"):
            email_service.send_email(ADDRESS, "Sujet", "<p>x</p>")  # TEST_MODE path
            monkeypatch.setattr(email_service, "TEST_MODE", False)
            monkeypatch.setattr(email_service, "RESEND_API_KEY", "")
            email_service.send_email(ADDRESS, "Sujet", "<p>x</p>")
        assert ADDRESS not in caplog.text

    def test_resync_reminder_logs_masked_addresses(self, monkeypatch, caplog):
        from app.jobs import resync_reminder

        monkeypatch.setattr(resync_reminder, "_select_stale_contributors", lambda db, months: [("u1", ADDRESS)])
        monkeypatch.setattr(resync_reminder, "send_email", lambda *a: False)
        with caplog.at_level(logging.INFO, logger="app.jobs.resync_reminder"):
            resync_reminder.send_resync_reminders(dry_run=True)
            resync_reminder.send_resync_reminders(dry_run=False)
        assert ADDRESS not in caplog.text
        assert "j***@example.org" in caplog.text


# ── File / member names ──────────────────────────────────────────────────────

class TestSafeFilename:
    @pytest.mark.parametrize("raw, expected", [
        ("ride.gpx", "ride.gpx"),
        ("../../etc/passwd", "etc_passwd"),
        ("a b\nc\r\x00d.gpx", "a_b_c_d.gpx"),
        ("", "upload.bin"),
        (None, "upload.bin"),
        ("...", "upload.bin"),
        ("ünïcödé.fit", "n_c_d_.fit"),
    ])
    def test_reduces_to_safe_characters(self, raw, expected):
        assert safe_filename(raw) == expected

    def test_truncates_and_keeps_the_extension(self):
        out = safe_filename("x" * 500 + ".gpx")
        assert len(out) == SAFE_NAME_MAX_LEN
        assert out.endswith(".gpx")

    def test_path_mode_keeps_separators_but_no_control_chars(self):
        out = safe_filename("export/activities/1\n2.gpx", allow_path=True)
        assert out == "export/activities/1_2.gpx"


class TestArchiveObjectKeys:
    def test_archive_gpx_key_and_log_use_the_safe_name(self, monkeypatch, caplog):
        from app.services import gpx_archive

        written: list[str] = []

        class _Blob:
            def __init__(self, name):
                written.append(name)

            def upload_from_string(self, _content):
                return None

        class _Bucket:
            def blob(self, name):
                return _Blob(name)

        class _Client:
            def bucket(self, _name):
                return _Bucket()

        monkeypatch.setattr(gpx_archive, "UPLOADS_BUCKET", "test-bucket")
        monkeypatch.setattr(gpx_archive, "_get_client", lambda: _Client())
        raw_name = "../dir\nname" + "y" * 300 + ".gpx"
        with caplog.at_level(logging.INFO, logger="app.services.gpx_archive"):
            gpx_archive.archive_gpx("user-1", "act-1", raw_name, b"<gpx/>")
            gpx_archive.archive_bundle("user-1", "f" * 64, raw_name, b"PK")
        assert len(written) == 2
        for key in written:
            name = key.rsplit("/", 1)[-1]
            assert ".." not in key and "\n" not in key
            assert len(name) <= SAFE_NAME_MAX_LEN
            assert name.endswith(".gpx")
        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 2
        assert all("\n" not in m and ".." not in m for m in messages), messages


class TestArchiveMemberNames:
    def _zip(self, members: list[tuple[str, bytes]]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, body in members:
                zf.writestr(name, body)
        return buf.getvalue()

    def test_unsafe_path_reason_is_sanitised(self):
        """The reject reason (stored in last_error, logged) carries a reduced
        member name: no control characters, bounded length."""
        from app.services import archive_intake
        from app.services import gpx as gpx_service

        raw = "../" + "z" * 300 + "\nsecond line.gpx"
        content = self._zip([(raw, b"<gpx/>")])
        with zipfile.ZipFile(io.BytesIO(content)) as zf, pytest.raises(gpx_service.ZipBombError) as exc:
            list(archive_intake.iter_zip_members(zf))
        reason = str(exc.value)
        assert "\n" not in reason and "second line" not in reason
        assert len(reason) < 200

        with pytest.raises(gpx_service.ZipBombError) as exc2:
            gpx_service.parse_zip_of_gpx(content)
        assert "\n" not in str(exc2.value) and len(str(exc2.value)) < 200

    def test_unreadable_nested_zip_log_is_sanitised(self, caplog):
        from app.services import archive_intake

        raw_inner = "inner\nline" + "q" * 300 + ".zip"
        content = self._zip([(raw_inner, b"not a zip")])
        with caplog.at_level(logging.WARNING, logger="app.services.archive_intake"), \
                zipfile.ZipFile(io.BytesIO(content)) as zf:
            assert list(archive_intake.iter_zip_members(zf)) == []
        assert "inner\nline" not in caplog.text
        assert "q" * (SAFE_NAME_MAX_LEN + 1) not in caplog.text
