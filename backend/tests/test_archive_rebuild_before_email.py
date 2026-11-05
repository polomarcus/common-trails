"""Archive drain: the PMTiles rebuild is requested BEFORE the "done" email.

Bug (Thierry Crouzet, 2026-11): after uploading a Garmin/Strava archive, the
"Vos traces sont sur la carte 🚴" email arrived long before the traces were
visible on /map. ``drain_pending_archives`` emailed each archive as soon as it
finished, but fired the single ``build-pmtiles`` ping only after the WHOLE
batch (up to 5 archives, possibly hours) — and the build itself then takes
~5-30 min.

These tests drive the REAL ``drain_pending_archives`` → ``_ArchiveRebuildPinger``
→ ``_ping_pmtiles_rebuild`` → ``_notify_archive_terminal`` →
``render_archive_done_email`` path. Only the I/O edges are faked (claim /
finish SQL, zip storage, member ingest, the job trigger and ``send_email``),
so they run without Postgres. A shared ``calls`` list records the order of
the rebuild pings and the emails.
"""

import contextlib
import io
import zipfile
from unittest.mock import MagicMock

import pytest

from app.jobs import ingest_pending_archives as drain_mod
from app.services import archive_intake
from app.services import email as email_service


def _zip_bytes(n: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(n):
            zf.writestr(f"activities/{i}.gpx", b"<?xml version='1.0'?><gpx/>")
    return buf.getvalue()


class _FakeSession:
    """Answers ``_notify_archive_terminal``'s owner lookup (the only SQL left
    un-faked on the drain path) with a real email address."""

    locale = "fr"

    def execute(self, *_a, **_k):
        result = MagicMock()
        result.mappings.return_value.first.return_value = {
            "email": "rider@example.com",
            "locale": self.locale,
        }
        return result

    def close(self):
        pass


@pytest.fixture
def harness(monkeypatch):
    """Wire the real drain to fakes; returns ``(calls, set_archives)``."""
    calls: list[tuple[str, str]] = []
    state = {"archives": []}

    @contextlib.contextmanager
    def fake_open(_backend, key):
        with zipfile.ZipFile(io.BytesIO(_zip_bytes(state["members"][key]))) as zf:
            yield zf

    def fake_ingest(**_kw):
        return "imported", "act-id", None

    def fake_trigger():
        calls.append(("ping", ""))
        return True

    def fake_send(to, subject, _html):
        calls.append(("email", subject))
        return True

    monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", lambda: None)
    monkeypatch.setattr(drain_mod, "_reap_stuck_archives", lambda db: [])
    monkeypatch.setattr(drain_mod, "_recompute_heat_agg_batched", lambda w: None)
    monkeypatch.setattr(drain_mod, "_finish_archive", lambda *a, **k: None)
    monkeypatch.setattr(drain_mod, "_ingest_member_bytes", fake_ingest)
    monkeypatch.setattr(drain_mod, "_claim_archives", lambda db, limit: state["archives"][:limit])
    monkeypatch.setattr(drain_mod, "trigger_build_pmtiles_job", fake_trigger)
    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(archive_intake, "archive_size", lambda b, k: None)
    monkeypatch.setattr(archive_intake, "open_archive_zip", fake_open)
    monkeypatch.setattr("app.db.session.SessionLocal", _FakeSession)
    monkeypatch.delenv("DRAIN_PMTILES_REPING_S", raising=False)

    def set_archives(member_counts: list[int]) -> None:
        state["members"] = {f"k{i}": n for i, n in enumerate(member_counts)}
        state["archives"] = [
            {
                "id": f"arch-{i}",
                "user_id": "u1",
                "storage_backend": "local",
                "bucket_key": f"k{i}",
                "fallback_sport": "road",
                "contribute_heatmap": True,
                "source": "manual_upload_archive",
            }
            for i in range(len(member_counts))
        ]

    return calls, set_archives


def _kinds(calls):
    return [kind for kind, _ in calls]


def test_single_archive_pings_rebuild_before_done_email(harness):
    """Old code: [email, ping] — the email went out before the build was even
    requested. Fix: the ping precedes the email."""
    calls, set_archives = harness
    set_archives([2])

    summary = drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert summary["imported"] == 2, summary
    assert _kinds(calls) == ["ping", "email"], calls


def test_batch_pings_first_archive_then_one_catch_up_ping(harness):
    """Two archives finishing within the re-ping window: ONE build requested
    before the first email, the second archive's ping is deferred (no two
    overlapping builds) and fired once at batch end — never N builds."""
    calls, set_archives = harness
    set_archives([2, 3])

    summary = drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert summary["imported"] == 5, summary
    assert _kinds(calls) == ["ping", "email", "email", "ping"], calls


def test_batch_repings_per_archive_once_window_elapsed(harness, monkeypatch):
    """When the previous ping is older than the re-ping window (long archives),
    every contributing archive gets its own ping BEFORE its email, and no
    redundant catch-up ping fires at the end."""
    calls, set_archives = harness
    monkeypatch.setenv("DRAIN_PMTILES_REPING_S", "0")
    set_archives([2, 3])

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert _kinds(calls) == ["ping", "email", "ping", "email"], calls


def test_no_ping_when_archive_imported_nothing(harness, monkeypatch):
    calls, set_archives = harness
    monkeypatch.setattr(drain_mod, "_ingest_member_bytes", lambda **kw: ("skipped", None, None))
    set_archives([2])

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert _kinds(calls) == ["email"], calls


def test_no_ping_when_heat_skipped(harness):
    calls, set_archives = harness
    set_archives([2])

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)

    assert _kinds(calls) == ["email"], calls


# ── Copy: truthful about WHEN the traces appear ──────────────────────────────


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_done_email_does_not_claim_traces_already_on_the_map(locale):
    subject, html = email_service.render_archive_done_email(imported=3, skipped=0, failed=0, locale=locale)
    text = f"{subject} {html}".lower()
    # The old copy claimed the traces were ALREADY on the community heatmap.
    for claim in (
        "sont sur la carte",
        "font maintenant partie",
        "are on the map",
        "now part of the community heatmap",
    ):
        assert claim not in text, (locale, claim)
    # Truthful copy: own rides now in "Mes traces", heatmap within ~30 min.
    assert ("mes traces" if locale == "fr" else "my traces") in text
    assert ("trentaine de minutes" if locale == "fr" else "30 minutes") in text
    assert "https://chemins-communs.fr/map" in html
    assert ("voir la carte" if locale == "fr" else "see the map") in text
