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
import time
import types
import zipfile
from unittest.mock import MagicMock

import pytest

from app.jobs import ingest_pending_archives as drain_mod
from app.services import archive_intake
from app.services import email as email_service
from app.services import gpx as gpx_service


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

    def fake_send(to, subject, html):
        calls.append(("email", f"{subject}\n{html}"))
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

    def set_archives(member_counts: list[int], *, contribute_heatmap: bool = True) -> None:
        state["members"] = {f"k{i}": n for i, n in enumerate(member_counts)}
        state["archives"] = [
            {
                "id": f"arch-{i}",
                "user_id": "u1",
                "storage_backend": "local",
                "bucket_key": f"k{i}",
                "fallback_sport": "road",
                "contribute_heatmap": contribute_heatmap,
                "source": "manual_upload_archive",
            }
            for i in range(len(member_counts))
        ]

    return calls, set_archives


def _intro(html: str) -> str:
    """The email's first paragraph (the intro, before the counts list)."""
    start = html.index("<p ")
    return html[start : html.index("</p>", start)]


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
    overlapping builds in this run) and flushed at batch end — never N builds."""
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


def test_deferred_ping_fires_as_soon_as_window_expires_mid_batch(harness, monkeypatch):
    """A deferred ping must NOT wait for batch end: it fires from the member
    loop of a LATER archive once the re-ping window has expired. Fake clock:
    every member ingest takes 40 s, window = 100 s.

    A (1 member) ends t=40 → ping. B (1) ends t=80 → deferred. C (3 members):
    window expires before its 3rd member (t=160) → B's ping fires mid-C, then C
    ends t=200 → deferred → flushed at batch end."""
    calls, set_archives = harness
    clock = {"t": 0.0}
    fake_time = types.SimpleNamespace(monotonic=lambda: clock["t"], sleep=lambda _s: None, time=time.time)

    def slow_ingest(**_kw):
        clock["t"] += 40.0
        calls.append(("ingest", ""))
        return "imported", "act-id", None

    monkeypatch.setattr(drain_mod, "time", fake_time)
    monkeypatch.setattr(drain_mod, "_ingest_member_bytes", slow_ingest)
    monkeypatch.setenv("DRAIN_PMTILES_REPING_S", "100")
    set_archives([1, 1, 3])

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert _kinds(calls) == [
        "ingest",
        "ping",
        "email",  # A
        "ingest",
        "email",  # B (deferred)
        "ingest",
        "ingest",
        "ping",
        "ingest",
        "email",  # C: B's ping mid-archive
        "ping",  # C's deferred ping flushed at batch end
    ], calls


def test_non_contributing_archive_no_ping_and_no_heatmap_promise(harness):
    """contribute_heatmap=False → nothing new on the heatmap: no rebuild, and
    the email only points to "Mes traces" (no community-heatmap promise)."""
    calls, set_archives = harness
    set_archives([2], contribute_heatmap=False)

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert _kinds(calls) == ["email"], calls
    body = calls[0][1].lower()
    assert "ma heatmap" in body
    assert "dans l'heure" not in body
    assert "intégrera" not in body


@pytest.mark.parametrize(
    "boom",
    [gpx_service.ZipBombError("too many members"), RuntimeError("storage hiccup")],
    ids=["zipbomb", "generic"],
)
def test_failed_archive_after_partial_import_still_pings(harness, monkeypatch, boom):
    """Members ingested before the archive blew up are committed heat — the
    failure paths must request the rebuild too (before the failure email)."""
    calls, set_archives = harness

    def iter_then_boom(_zf):
        yield "activities/0.gpx", b"<?xml version='1.0'?><gpx/>", "road"
        raise boom

    monkeypatch.setattr(archive_intake, "iter_zip_members", iter_then_boom)
    set_archives([1])

    summary = drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert summary["archives_failed"] == 1, summary
    assert summary["imported"] == 1, summary
    assert _kinds(calls) == ["ping", "email"], calls


def test_unfired_ping_does_not_open_the_window(harness, monkeypatch):
    """A ping whose trigger did NOT fire (falsy ``jobs:run``) must not start the
    re-ping window — no build is running. The next archive pings again (and
    that one fires), with nothing left over for the batch end."""
    calls, set_archives = harness
    results = iter([False, True])

    def flaky_trigger():
        calls.append(("ping", ""))
        return next(results)

    monkeypatch.setattr(drain_mod, "trigger_build_pmtiles_job", flaky_trigger)
    set_archives([2, 3])

    drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert _kinds(calls) == ["ping", "email", "ping", "email"], calls


def test_archive_counted_once_when_finish_raises_on_done_path(harness, monkeypatch):
    """``_finish_archive_now`` raising on the done path falls through to the
    generic ``except``, which calls ``archive_finished`` again — the archive
    must still be pinged ONCE (window 0 would expose a double count)."""
    calls, set_archives = harness

    def finish(_db, _archive_id, status, *_a, **_k):
        if status == "done":
            raise RuntimeError("db hiccup on the terminal UPDATE")

    monkeypatch.setattr(drain_mod, "_finish_archive", finish)
    monkeypatch.setenv("DRAIN_PMTILES_REPING_S", "0")
    set_archives([2])

    summary = drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)

    assert summary["archives_failed"] == 1, summary
    assert _kinds(calls) == ["ping", "email"], calls


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
    # Truthful copy: own rides now in "Ma heatmap", heatmap usually within the hour
    # (build ~30 min + cold start + pointer cache — "~30 min" was optimistic).
    assert ("ma heatmap" if locale == "fr" else "my heatmap") in text
    assert ("en général dans l'heure" if locale == "fr" else "usually within the hour") in text
    # The count lives in the list only — not duplicated in the intro.
    assert "<strong>3</strong>" in html
    assert "3" not in _intro(html), _intro(html)
    assert "30 minutes" not in text and "trentaine" not in text
    assert "https://chemins-communs.fr/map" in html
    assert ("voir la carte" if locale == "fr" else "see the map") in text


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_done_email_without_heatmap_contribution_only_points_to_my_traces(locale):
    _subject, html = email_service.render_archive_done_email(
        imported=3, skipped=0, failed=0, locale=locale, contribute_heatmap=False
    )
    text = html.lower()
    assert ("ma heatmap" if locale == "fr" else "my heatmap") in text
    assert ("dans l'heure" if locale == "fr" else "within the hour") not in text
    intro = _intro(html).lower()
    assert "heatmap communautaire" not in intro and "community heatmap" not in intro, intro
