"""Non-regression for two raw-mode startup bugs surfaced 2026-08-05 after the
prod heat_edges DROP:

B. `load_persisted_activities` ran `SELECT COUNT(*) FROM heat_edges` at startup —
   the table is DROPPED under the raw pivot → the query raised. Under raw the
   whole matched-era heat_edges rebuild is dead; the function must skip it.
C. `_prewarm_caches` did `await heatmap_summary()`, but `heatmap_summary` is a
   SYNC def returning a JSONResponse → `await <JSONResponse>` raised TypeError,
   so the summary cache never pre-warmed. It must run in the threadpool.
"""
import asyncio
from unittest.mock import MagicMock, patch


def test_load_persisted_activities_raw_skips_heat_edges():
    """B: under raw display, load_persisted_activities returns after the activity
    count and NEVER queries heat_edges (which is dropped in prod)."""
    from app.services import ingest

    fake_db = MagicMock()
    fake_db.query.return_value.count.return_value = 7  # act_count = 7 (non-zero)
    # Any db.execute(...) here would be the heat_edges COUNT → fail loudly.
    fake_db.execute.side_effect = AssertionError(
        "load_persisted_activities must not query heat_edges under raw mode"
    )

    with patch("app.db.session.SessionLocal", return_value=fake_db), \
         patch("app.services.raw_trace_display.raw_display_enabled", return_value=True):
        n = ingest.load_persisted_activities()

    assert n == 7
    fake_db.execute.assert_not_called()


def test_heatmap_summary_is_sync_not_awaitable():
    """C: heatmap_summary is a sync def (returns JSONResponse) — it must be run
    via run_in_executor, never `await`ed. Pin the sync-ness so the await bug
    can't silently return."""
    from app.api.heatmap import heatmap_summary

    assert not asyncio.iscoroutinefunction(heatmap_summary)
    # And the threadpool prewarm pattern completes without TypeError:
    async def _prewarm():
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, heatmap_summary)

    resp = asyncio.run(_prewarm())
    assert resp.status_code == 200
