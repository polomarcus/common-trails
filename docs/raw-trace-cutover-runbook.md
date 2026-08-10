# Raw-trace heatmap — cutover runbook

The exact staged, **Paul-gated** procedure to actually realise the raw-trace
display + the DB-size win. Companion to `docs/raw-trace-heatmap-prototype.md`
(the decision package) and PR #505 (the flag-gated code).

**Do not run any step without Paul's explicit go.** Each step is tagged
🔁 **REVERSIBLE** or ☠️ **ONE-WAY**. Stop at any step; nothing after (a) is
required for the display to work — steps (f)/(g) are purely for the cost win and
are irreversible.

Prod is deployed by `scripts/deploy-prod.sh` (the env SSOT — NOT terraform, NOT
hand-typed gcloud; `--update-env-vars` MERGE on jobs, `--env-vars-file` full
set on the service). Every env change below goes through that script.

**Provenance is enforced IN-CODE (PR #505).** The raw build path
(`raw_trace_display.export_raw_geojson`) only reads `source='manual_upload'`
activities (SSOT `provenance.is_community_source`); Strava-API (§5.4/§5.10) and
legacy `NULL` traces are excluded exactly as the matched pipeline gates them at
ingest. So flipping to raw cannot publish personal Strava-API data. ⚠️ Corollary:
the current prod corpus is largely `strava_api`/`NULL` — the raw map will be
**sparse until users upload their own archives** (`manual_upload`). Do not read a
sparse first raw render as a bug.

---

## Still-open pre-cutover items (REQUIRED decisions / soak, before raw REPLACES matching)

These are separate from the reversible display flip and must be resolved before
raw becomes the sole model / the substrate is dropped:

1. **In-RAM build — ✅ ADDRESSED (streaming rework, `perf/raw-build-streaming`).**
   `export_raw_geojson` no longer materialises the run corpus. It makes TWO
   streaming passes over the consented activities (server-side cursor, one
   activity's geometry in flight at a time): pass 1 folds masked points into a
   BOUNDED per-cell lattice (INT `pass_count` + a small distinct-user set), pass
   2 re-streams and writes each `LineString` feature straight to disk. Peak
   memory is bounded by OCCUPIED GEOGRAPHY, NOT by activity/point count —
   measured flat at ~0.36 MB for the accumulate+emit structures across a 5x
   corpus growth (50 → 250 activities), vs the old materialised build's
   ~linear 3.2 → 14 MB (38.8x less at k=250). The drawn geometry is
   byte-for-byte the same masked polyline (equivalence pinned by
   `tests/test_raw_trace_display_streaming.py`), so the render is unchanged; only
   the counting is now streaming. Still: the per-cell distinct-USER sets grow
   with the contributor base per cell (bounded by users, not points) — if that
   ever bites at national scale, swap each set for a HyperLogLog distinct-counter
   (noted in `raw_trace_display._accumulate_lattice`). The intermediate GeoJSONL
   on disk is still ~O(features); tippecanoe already streams it. Reversible until
   cutover (stay on matched).
2. **Privacy model — route-K vs cell-K + mid-route home passes.** `HEATMAP_MIN_USERS`
   gives **cell-K** (a fine cell needs ≥K distinct users) — NOT route-K: an
   individual's distinct *route line* is still drawn wherever its cells clear the
   gate. And endpoint masking trims only the trace's start/end — a rider who
   passes their own home *mid-route* (loop that returns past the house) is NOT
   protected there. Decide explicitly whether cell-K + endpoint masking is an
   acceptable public posture, and pick the `HEATMAP_MIN_USERS` / `TRACE_MASK_METERS`
   values, BEFORE a public launch. Soak at `MIN_USERS=1` in the private beta only.

---

3. **Login posture — public MAP, members-only EXPORT (Paul's ratified decision, 2026-07).**
   The community MAP is **publicly viewable** — an anonymous visitor CAN browse
   the heatmap on the home hero AND on `/map`, "pour donner envie" (a growth
   lever: see the map freely, then log in to take/contribute data). Privacy for
   the raw K=1 traces rests on **endpoint masking** (`TRACE_MASK_METERS`) + the
   `HEATMAP_MIN_USERS` (K) policy — NOT on gating the map. So the map, and the
   PMTiles asset behind it, stay **public**.

   The ONE members-only affordance is the community **DATA EXPORT** — the bulk
   ODbL download in `ExportHeatmapModal`. An anonymous user who opens it gets a
   passwordless login CTA (`export-login-gate`, reuses `EmailLoginForm`); a
   logged-in user exports normally. The user's OWN drawn-route GPX export
   (`RouteEditorPanel`) is personal data and is NOT gated.

   > History: an earlier iteration (#509 first cut) gated the whole `/map` page
   > behind login (`MapLoginGate` + a `getPmtilesUrl()` asset gate). Paul
   > reversed that — the map must be public. The page gate and the map-side
   > asset gate were removed. `MapLoginGate` is gone.

   - **Asset stays PUBLIC.** `getPmtilesUrl()` returns the public GCS URL for
     everyone; the map must never be gated. The display bucket
     (`gs://common-trails-heatmap-prod/heatmap-display.pmtiles`) stays a public
     object — that is intended under this posture, not a residual gap.
   - **Optional future export-download hardening (kept, dormant, default OFF).**
     The backend `GET /heatmap/display-url` signed-URL endpoint (reuses the #491
     signBlob signer via `archive_intake.generate_signed_get_url`) and the
     `NEXT_PUBLIC_HEATMAP_GATED` flag remain in the codebase but are NOT wired to
     map viewing. They are available only if a future decision wants to gate a
     bulk asset *download* (never the map render). Leaving them in is harmless;
     do not flip anything to gate the map.

## (a) Merge PR #505 — 🔁 REVERSIBLE (revert the PR)

Flag-gated, default `HEATMAP_DISPLAY_SOURCE=matched` → **prod behaviour is
byte-identical**. Nothing changes on the map. This is the only step already
requested; it is safe on its own.

## (b) Flip the display source in prod env — 🔁 REVERSIBLE (flip back + rebuild)

In `scripts/deploy-prod.sh`, add to the authoritative `ENV_FILE` block
(alongside the existing `HEATMAP_*` vars, ~line 179):

```yaml
HEATMAP_DISPLAY_SOURCE: "raw"
HEATMAP_MIN_USERS: "1"          # beta: show everything incl. solo traces
TRACE_MASK_METERS: "200"        # home/work endpoint mask (optional, this is the default)
# HEATMAP_RAW_LATTICE_DEG left default (~5 m)
```

Ensure the **`common-trails-build-pmtiles-${ENV}`** job carries these three vars
too (it runs the build) — add them to that job's `job_env` in the JOBS loop so
`--update-env-vars` applies them (never `--set-env-vars`). Then run
`scripts/deploy-prod.sh`. No rebuild has run yet, so the served PMTiles is still
the matched one — this step only stages the env.

**Reverse:** set `HEATMAP_DISPLAY_SOURCE=matched`, re-run the script, rebuild (c).

## (c) One-time raw PMTiles rebuild — 🔁 REVERSIBLE (rebuild in matched mode)

```bash
gcloud run jobs execute common-trails-build-pmtiles-prod --region=europe-west1 --wait
```

Builds `heatmap-display.pmtiles` from consented `activities` (masked) and
publishes to `gs://common-trails-heatmap-prod/`. **Runs on db-f1-micro with no
tier bump** — it never scans `heat_edges`/`heat_edges_agg` in raw mode. The map
now serves raw traces. To revert: flip (b) back and re-execute this job.

## (d) Validate the prod map for a while — 🔁 REVERSIBLE

Eyeball chemins-communs.fr at several zooms + sports; confirm corridors read
like Strava, endpoints are masked (no home blooms), no obvious GPS-jump
spaghetti. Watch `/admin/heatmap-metrics` freshness. Keep both the raw display
AND the matching substrate live during this soak — everything so far is
reversible. Only proceed when confident.

> Privacy tightening (independent, 🔁 reversible): when contributor count grows,
> set `HEATMAP_MIN_USERS=2` (step b + rebuild c). Single-user pixels disappear;
> only ≥2-rider overlaps show — the K=1→K=2 promise, one env var.

## (e) Reword the consent copy — REQUIRED before any public/real-user launch

☠️ semi-one-way in spirit (users who consented under the old wording may need
re-consent — legal call). The current consent string says data is
**"anonymisées"** (K-anonymity). Raw traces + endpoint masking are NOT anonymised
in that sense. Reword the consent text (and the ODbL export description) to
reflect "vos traces précises, début et fin masqués" BEFORE onboarding real users
onto the raw map. **Legal wording is Paul's call** — deliberately not changed in
code in #505. Do not skip this before a public announcement.

---

## Everything below is purely for the DB-size win and is IRREVERSIBLE.

Only after (d) has soaked and Paul explicitly approves. Take a fresh backup
first. None of these are needed for the raw display to keep working.

## (f) Drop the routing substrate — ☠️ ONE-WAY (kills routing; re-import is a big job)

Frees the bulk of the DB (~8.15 GB of 15 GB measured):

```sql
DROP TABLE IF EXISTS ch_shortcuts;          -- ~2.84 GB (routing)
DROP TABLE IF EXISTS osm_ways;              -- ~0.79 GB
-- osm_road_edges is partitioned BY LIST(region): drop parent CASCADE
DROP TABLE IF EXISTS osm_road_edges CASCADE; -- ~4.17 GB (all partitions)
-- optional routing extras:
DROP TABLE IF EXISTS dfci_edges, trail_edges;  -- ~0.35 GB
```

**One-way**: routing (`.fgraph`) is already frozen; without the substrate it is
permanently dead — re-enabling means re-importing 22 region PBFs (hours, tier
bump). The static `.fgraph` shards keep serving read-only until code removes
them; new routing is impossible. Do NOT do this while routing is expected to
work. After dropping, `VACUUM` won't shrink the Cloud SQL PD (autoresize-grown
disks never shrink) — the win is realised by recreating/downsizing the instance
if desired, or simply by not paying for further growth.

## (g) Decommission the matching machinery — ☠️ ONE-WAY

Once raw is the confirmed display model:

- `DROP TABLE heat_edges* (partitions), heat_edges_agg, heat_edge_contributors;`
  (~2.46 GB) and `heat_cells, heat_cell_contributors` (~0.24 GB, grid path).
- Remove/park the now-moot Cloud Run jobs: `rebuild-heatmap`, `rebuild-heat-agg`,
  `verify-heat-agg`, `group-edges-osm`, `import-osm`, `migrate-edge-clustering`
  (and drop them from the `JOBS` list in `deploy-prod.sh`).
- The paused `resync-strava-weekly` / archive-drain schedulers: keep the drain
  (raw ingest still needs uploaded archives → `activities`); the matching-only
  ones can go.
- `build_pmtiles` keeps running in raw mode (reads `activities` only).

Keep at least one backup of the dropped tables (GCS dump) until the raw model has
run in prod for a comfortable margin — this is the last exit before the routing
substrate is gone for good.

---

## Reversibility summary

| Step | Reversible? | How to undo |
|---|---|---|
| (a) merge #505 | 🔁 | revert PR |
| (b) env flip to raw | 🔁 | flip `HEATMAP_DISPLAY_SOURCE=matched`, rebuild |
| (c) raw rebuild | 🔁 | rebuild in matched mode |
| (d) validate / `MIN_USERS` tuning | 🔁 | env + rebuild |
| (e) reword consent | ⚠️ required; re-consent is a legal call |
| (f) drop routing substrate | ☠️ one-way | re-import all region PBFs (big job) |
| (g) drop matching tables + jobs | ☠️ one-way | restore from backup dump |
