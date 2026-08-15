# Archive-import cost optimization (10K-file `.zip` drains)

**Status:** analysis + plan for review (Paul asked 2026-08-14, "optimiser les coûts serveurs" for big-`.zip` imports). Nothing here is deployed — the drain is the load-bearing ingest path, so the changes below want a reviewed PR + the `test_archive_drain_golden` guard, not an overnight prod push.

## ⭐ Measured 2026-08-15 (raw mode = prod) — pacing AND per-member work both matter

Two measurements against a local **docker** Postgres in `HEATMAP_DISPLAY_SOURCE=raw`
(prod's config, **no OSM substrate** — raw mode gates `_update_heat_edges` off, so
the drain does zero map-matching):

1. **Tiny synthetic GPX (6 points/file):** 500 members in 1.3 s = **2.5 ms/member**.
   This is a *floor for trivial files* and MASSIVELY under-represents real rides.
2. **Real ride files** (600 from `data/strava-export`, thousands of points each,
   real `.gpx`/`.fit.gz`, 146 MB uncompressed):
   - pure work (`pace=0`): 93.9 s = **156.5 ms/member**
   - adaptive (ratio 1.0): 192.9 s = **321.6 ms/member** (≈ 2× work — the 50 %
     duty design: sleep ≈ the previous member's real work).

**The earlier "pacing is the ENTIRE cost / batching is marginal / ~3.5 min"
conclusion was WRONG** — it extrapolated from the 2.5 ms synthetic figure. On real
rides the **per-member WORK (~156 ms of GPX parse + dedup SELECT + INSERT)
dominates**, and the adaptive sleep is only ≈ half the wall-time. Projected to a
16,361-member Garmin archive (**docker**, extrapolated):

| Pacing | ms/member | 16 k archive |
|---|---|---|
| pure work floor (irreducible without cutting work) | 156 | **~43 min** |
| **adaptive (PR B, ratio 1.0)** | 322 | **~88 min** |
| 0.25 s fixed (PR A) | 407 | ~111 min |
| old 1.0 s fixed | 1157 | **~5.3 h** |

So PR B's real win is **~5.3 h → ~88 min ≈ 3.6×** (docker), NOT the 77× I first
claimed. ⚠️ **All on docker — f1-micro is weaker (CPU-bound parsing + slower DB),
so the true prod numbers are larger and UNMEASURED.** The definitive figure needs a
real prod replay (e.g. re-drain tester 2's retained archive after #7+#8 deploy).

**Revised recommendation (corrected):**
- **Adaptive pacing (PR B)** is still the right, safe first lever — it self-tunes
  to the live DB (so it right-sizes itself on f1-micro), env-tunable ratio/cap.
- **Batching DB writes is NOT "marginal" after all** — but it only helps the DB
  slice of the ~156 ms; a lot of that work is **GPX/FIT parsing** (CPU), which
  batching doesn't touch. Worth profiling parse-vs-DB before investing.
- If a real prod drain is still too slow, the cheap next knob is **lowering
  `DRAIN_PACE_RATIO`** (e.g. 0.25 → sleep ≈ 40 ms → 16 k ≈ ~53 min on docker),
  watched against f1-micro CPU / conns / `connection reset`.

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
