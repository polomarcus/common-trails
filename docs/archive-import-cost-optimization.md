# Archive-import cost optimization (10K-file `.zip` drains)

**Status:** analysis + plan for review (Paul asked 2026-08-14, "optimiser les coûts serveurs" for big-`.zip` imports). Nothing here is deployed — the drain is the load-bearing ingest path, so the changes below want a reviewed PR + the `test_archive_drain_golden` guard, not an overnight prod push.

## The cost driver (measured)

`app.jobs.ingest_pending_archives.drain_pending_archives` processes a member archive in two passes:

1. **PASS 1** — walk every member (`iter_zip_members`), compute a `_member_spatial_key`, buffer a `(name, sport, tile_key)` plan.
2. **PASS 2** — sort the plan by spatial key, then ingest members **one at a time**, `time.sleep(DRAIN_PACE_SECONDS)` **between each** (default **`_DEFAULT_PACE_SECONDS = 1.0`**), each member a full `ingest_activity` round-trip.

For tester 2's real Garmin archive (**16,361 members**):

| Component | Cost |
|---|---|
| Inter-member pacing | 16,361 × pace ≈ **~hours of paid 8Gi/cpu2 job runtime spent `sleep()`ing** |
| Per-member DB work | 16,361 × (`ingest_activity`: dedup query + INSERT + commit) |
| Passes over the zip | 2× (scan, then re-read per member) |

**The job spends most of its wall-clock (and therefore cost) asleep**, protecting the shared **db-f1-micro** from a per-activity ingest storm. So the lever is not "sleep less" (that just moves the storm onto the f1-micro — see the Aug-13 DB `connection reset by peer` events) but **make each DB interaction cheaper and fewer**, so the same protection needs far less pacing.

## Why the pacing exists (and why it's now oversized)

The 1.0s pace was tuned for the **OSM-matching era**, when `ingest_activity` reloaded 17k–65k `osm_road_edges` per activity (the smoke-night DoS). **Post raw-pivot, OSM matching is dead** — a member ingest now only: parse GPX/FIT → dedup check → INSERT one `activities` row (+ geometry) → (heat compute is already deferred/batched via `collect_touched_ways`). That is a *light* op, badly overserved by a 1s sleep.

The `_member_spatial_key` + `_spatial_sort_key` two-pass sort exists **only** to keep the OSM segment cache warm — also **vestigial** post-pivot (there is no segment cache anymore).

## Options, ranked by impact ÷ risk

1. **Bulk the DB writes (biggest win).** Accumulate parsed activities and INSERT in batches (executemany / `COPY`) inside one transaction per batch, committing every N (e.g. 200). Fewer round-trips + fewer fsyncs → the f1-micro load per unit-time *drops*, so the pace can drop with it. Expected: a 16k-member drain from **hours → minutes**. **Effort/risk: high** — `ingest_activity` does per-activity cross-source dedup (±5 min / ±5 % seeded from user history) + #453 promotion; batching must preserve those. Needs a `batch_ingest_activities` path + golden coverage.
2. **Drop the vestigial two-pass spatial sort → one-pass streaming ingest.** Ingest directly from `iter_zip_members`' yielded bytes (which also removes PASS-2's re-read and the nested-zip inner-cache entirely). Halves the zip walk, removes the per-member spatial-key compute + the sort. **Effort/risk: low** — the sort is provably dead post-OSM-pivot; golden test guards behaviour.
3. **Adaptive pacing.** Scale `DRAIN_PACE_SECONDS` down for the raw-pivot's lighter member cost, and/or make it a function of archive size (big archives get a smaller per-member pace once batching bounds the burst). **Risk: medium** — must watch f1-micro CPU/conns (the Aug-13 resets). Safer *after* (1).
4. **Parallelise parsing.** Parse GPX/FIT (CPU-bound) with a small worker pool while a single writer serialises the DB batch. Overlaps parse with commit. **Risk: medium**, only worth it after (1).
5. **Right-size the job.** 8Gi is for streaming a 2GB `.zip` into tmpfs; if a drain is mostly sleeping, cpu/memory are idle — after (1)+(3) the job is short and could drop to cpu1/4Gi. **Risk: low**, do last.

## Recommended sequence
- **PR A (safe, ship first):** option 2 (one-pass, drop vestigial sort) + heat-agg recompute already batched. Pure simplification, big readability win, small speed win. Golden test unchanged-green.
- **PR B (the real cost win, reviewed):** option 1 (`batch_ingest_activities`, commit every N) + option 3 (adaptive/lower pace, gated on f1-micro headroom). Add a golden case for a 1k-member archive asserting correctness + a runtime assertion.
- **PR C (polish):** option 4/5 if the numbers still warrant.

## Guardrails
- Keep `test_archive_drain_golden` (upload → drain → `heat_edges`/activities, provenance) green through every step.
- Watch Cloud SQL `connection reset by peer` + worker `/healthz` during a real large drain — the whole point is *less* DB stress, so a regression there means the batch sizing is wrong.
- Never let a batch hold the whole archive in RAM: batch by count (e.g. 200), not "all".
