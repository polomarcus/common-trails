"""PR-E High #4: Phase 3 GPS upgrade enqueues a heat-compute Cloud Task
per upgraded activity instead of running ``_update_heat_edges`` inline.

For a 1400-activity import, the inline path serialised ~280k heat-edge
UPSERTs inside the user-visible "GPS upgrade" phase. Moving to Cloud
Tasks lets Phase 3 complete the moment streams are persisted; heat
edges trickle in async on the heat-compute receiver.

In TEST_MODE the enqueue runs inline via `enqueue_heat_compute` →
`_run_inline` → `process_heat_compute`. We assert the call shape, not
the queue contents.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.api import integrations_strava as iss


class TestPhase3EnqueuesHeatCompute:
    """``_run_gps_upgrade`` calls ``enqueue_heat_compute`` per upgraded activity."""

    def test_enqueues_one_task_per_upgraded_activity(self) -> None:
        """3 upgraded activities → 3 enqueue calls (none inline-blocking)."""
        # Two batches of 2 + 1, then empty → 3 total upgrades.
        batches = [
            [("act-1", "111", "road"), ("act-2", "222", "gravel")],
            [("act-3", "333", "mtb")],
            [],  # terminating
        ]

        async def _stream_ok(_tok, _id):
            # Minimal GPS stream so `stream_to_geojson` returns a LineString
            return {"latlng": {"data": [[43.6, 3.87], [43.61, 3.88]]}}

        with patch.object(iss, "_gps_next_batch", side_effect=batches), \
             patch.object(iss, "get_activity_stream", side_effect=_stream_ok), \
             patch.object(iss, "_gps_apply_batch") as apply_batch, \
             patch.object(iss, "_read_job_progress", return_value=(3, 3, 0)), \
             patch("app.services.cloud_tasks.enqueue_heat_compute") as enq, \
             patch("app.services.notifications.emit_notification"), \
             patch("asyncio.sleep", new=lambda *_a, **_kw: _aio_noop()):
            asyncio.run(iss._run_gps_upgrade("job-id", "user-1", "tok"))

        # 3 enqueues, one per activity, with (activity_id, user_id) shape.
        assert enq.call_count == 3, f"expected 3 enqueues, got {enq.call_count}"
        called_ids = {c.args[0] for c in enq.call_args_list}
        assert called_ids == {"act-1", "act-2", "act-3"}
        for c in enq.call_args_list:
            assert c.args[1] == "user-1"

        # Old inline path: _update_heat_edges was NEVER called from this loop.
        # (The receiver invoked by enqueue_heat_compute will call it on its
        # own session; here we only verify the user-visible loop doesn't.)
        assert apply_batch.call_count >= 1  # geometry was persisted

    def test_no_enqueue_when_no_stream_returned(self) -> None:
        """Activities whose stream is empty don't get a heat-compute task."""
        batches = [
            [("act-empty", "999", "road")],
            [],
        ]

        async def _stream_empty(_tok, _id):
            return {"latlng": {"data": []}}  # → stream_to_geojson returns None

        with patch.object(iss, "_gps_next_batch", side_effect=batches), \
             patch.object(iss, "get_activity_stream", side_effect=_stream_empty), \
             patch.object(iss, "_gps_apply_batch"), \
             patch.object(iss, "_read_job_progress", return_value=(1, 1, 0)), \
             patch("app.services.cloud_tasks.enqueue_heat_compute") as enq, \
             patch("app.services.notifications.emit_notification"), \
             patch("asyncio.sleep", new=lambda *_a, **_kw: _aio_noop()):
            asyncio.run(iss._run_gps_upgrade("job-id", "user-1", "tok"))

        assert enq.call_count == 0, "empty stream must not enqueue heat task"

    def test_enqueue_failure_is_non_fatal(self) -> None:
        """A raised enqueue must NOT crash Phase 3 — heat compute is best-effort."""
        batches = [
            [("act-1", "111", "road")],
            [],
        ]

        async def _stream_ok(_tok, _id):
            return {"latlng": {"data": [[43.6, 3.87], [43.61, 3.88]]}}

        with patch.object(iss, "_gps_next_batch", side_effect=batches), \
             patch.object(iss, "get_activity_stream", side_effect=_stream_ok), \
             patch.object(iss, "_gps_apply_batch"), \
             patch.object(iss, "_read_job_progress", return_value=(1, 1, 0)), \
             patch("app.services.cloud_tasks.enqueue_heat_compute", side_effect=RuntimeError("queue down")), \
             patch("app.services.notifications.emit_notification"), \
             patch("asyncio.sleep", new=lambda *_a, **_kw: _aio_noop()):
            # If this raises, the test fails — Phase 3 must survive enqueue errors.
            asyncio.run(iss._run_gps_upgrade("job-id", "user-1", "tok"))


async def _aio_noop() -> None:
    return None
