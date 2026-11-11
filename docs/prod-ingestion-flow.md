# Production ingestion flow — how an upload becomes a heatmap update

Current flow (raw-trace display, `HEATMAP_DISPLAY_SOURCE=raw`). The previous
version of this doc described the matched pipeline (Valhalla, `heat_edges`,
matview refresh) and is superseded; see [`archive/`](archive/) for history.
System overview with diagram: [`architecture.md`](architecture.md).

## 1. Small files — `POST /imports/files`

`backend/app/api/imports.py::import_files`. The frontend dropzone posts **one
file per request** (GPX, FIT, or a small `.zip` of them) with the sport and the
consent fields (`contribute_heatmap`, `consent_version`, …).

1. The activity is parsed and stored in `activities` (`source='manual_upload'`).
2. When the request stored at least one community-eligible activity, the web
   calls `request_community_rebuild()` (`backend/app/services/cloud_tasks.py`)
   → `enqueue_artefact_rebuild()` → Cloud Task to the **worker**
   `POST /internal/artefacts/rebuild` (const task name, debounced), which runs
   `build_pmtiles.main()` in-process. `POST /gpx/upload` does the same. (The
   former per-activity hop through `POST /internal/ingest/heat` is gone; that
   route is a 200 no-op kept for one release so already-queued tasks drain.)
3. On the **last file of a batch** (`rebuild_display=True`), the web also fires
   the `common-trails-build-pmtiles-prod` Cloud Run Job
   (`run_jobs.trigger_build_pmtiles_job`), debounced per instance by
   `BUILD_PMTILES_DEBOUNCE_S` (600 s).

## 2. Archives — signed upload + drain job

1. `POST /imports/strava-archive/init` returns a signed GCS URL; the browser
   uploads the `.zip` (≤ `MAX_ARCHIVE_BYTES`, 2 GB in prod) **directly** to the
   uploads bucket (`UPLOADS_BUCKET`).
2. `POST /imports/strava-archive/complete` marks it uploaded and fires the
   `common-trails-ingest-pending-archives-prod` job
   (`run_jobs.trigger_ingest_archives_job`). Code comments describe a daily
   backstop scheduler for the drain.
3. The drain (`backend/app/jobs/ingest_pending_archives.py`, 8Gi job) streams
   the archive and ingests each member into `activities`.
4. **Per archive, before the "done" email**, `_ArchiveRebuildPinger.archive_finished`
   requests a build-pmtiles run: the first contributing archive pings at once;
   later ones re-ping only if the previous ping is older than
   `DRAIN_PMTILES_REPING_S` (1800 s), otherwise the ping is deferred and fired by
   `tick()` in the member loop or flushed by `batch_finished()`. No ping when the
   archive imported nothing or `contribute_heatmap=False`.
5. The email (`backend/app/services/email.py`, « Votre archive est importée 🚴 »)
   says the rides are visible immediately in **« Ma heatmap »** and, only if the
   user contributes, that the community heatmap will include them « en général
   dans l'heure ».

## 3. Legacy Strava API activities (personal only)

The Strava API sync (OAuth connect, webhook, import and resync jobs) is
removed. The activities it stored (`source='strava_api'`) stay in the
database: they show in « Ma heatmap » but **never** reach the community map
(provenance gate, `backend/app/services/provenance.py`).

## 4. Build and publish

`build-pmtiles` reads consented `manual_upload` activities, applies endpoint
masking and the `HEATMAP_MIN_USERS` floor, runs tippecanoe and publishes to the
`common-trails-heatmap-prod` bucket served at `https://tiles.chemins-communs.fr`:
immutable snapshot → **pointer compare-and-set** on `heatmap-display.json`
(`snapshot_started_at`) → mutable copy → prune. Overlapping builds are safe;
an older build never overwrites a newer pointer. Details:
[`heatmap-pipeline.md`](heatmap-pipeline.md#pmtiles-versioning-convention-phase-3).

There is **no periodic build-pmtiles scheduler**: a missed or debounced trigger
leaves the map stale until the next trigger. Manual run:
[`runbooks/heatmap-rebuild.md`](runbooks/heatmap-rebuild.md).

## 5. What the user sees

- **« Ma heatmap »**: live from `GET /me/activities`, immediately after ingest.
- **Community heatmap**: `/` and `/map` resolve the PMTiles through the pointer
  (`frontend/lib/cdn-cache.ts`), so a new build appears within the pointer's
  short cache window after it publishes.
