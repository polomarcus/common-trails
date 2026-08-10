# Large-archive upload — signed-URL direct-to-GCS + cold-start importer

> Feature branch `feat/archive-signed-upload-importer`. Makes a REAL Strava
> export contributable (Paul's = 1406 files, ~70 MB zipped / ~400 MB unzipped:
> 366 MB .gpx + 32 MB .fit.gz) WITHOUT routing the bytes through Cloud Run
> (~32 MB request cap) or OOMing the 512 Mi web instance.
>
> Companion to `docs/strava/prod-flip-runbook.md` (the supervised prod flip) —
> that runbook's "missing ingest-pending-archives job" blocker is resolved here.

## Why the old path can't work

`POST /imports/strava-archive` did `content = await file.read()` and split the
zip into `pending_archive_files` rows ON THE WEB REQUEST THREAD, capped at
50 MB. Cloud Run also caps the request body at ~32 MB. So a real export can't be
uploaded, and raising the cap would OOM the 512 Mi web instance. **The web
instance must never hold the archive bytes.**

## The architecture (browser → GCS direct → 2 Gi importer)

1. **`POST /imports/strava-archive/init`** — body (JSON):
   `{sport, consent, consent_version, consent_text, locale, filename?}`.
   Requires consent (422 if absent); records a `ContributionConsent` audit row
   FIRST; creates ONE `pending_archives` row (`status=awaiting_upload`). Returns
   `201`:
   ```json
   {
     "archive_id": "...", "consent_id": "...",
     "bucket_key": "archive-intake/<user_id>/<uuid>.zip",
     "upload_url": "<V4 signed PUT URL | /imports/strava-archive/upload/<archive_id>>",
     "upload_method": "PUT", "content_type": "application/zip",
     "max_bytes": 2147483648, "storage_backend": "gcs" | "local"
   }
   ```
2. **Browser PUTs the `.zip` directly to `upload_url`** — to GCS in prod
   (bypasses Cloud Run entirely; `Content-Type: application/zip` is bound into
   the V4 signature). Upload progress is shown client-side (XHR `upload.onprogress`).
3. **`POST /imports/strava-archive/complete`** — body `{archive_id, key}`.
   Verifies the key sits under `archive-intake/<user_id>/` (403 otherwise), that
   it matches the row (400), and that the object exists in the bucket (409 if the
   PUT hasn't landed). Flips the row to `status=uploaded`. Returns `202`
   `{archive_id, status:"uploaded", enqueue:"scheduled"}`.
4. **Cold-start importer** `python -m app.jobs.ingest_pending_archives` — for
   each `uploaded` `pending_archives` row (claimed `FOR UPDATE SKIP LOCKED`),
   STREAMS the `.zip` from the bucket to a temp file, opens it with `zipfile`
   (reads one member into memory at a time — the ~400 MB decompressed payload is
   never fully materialised), walks GPX/FIT/`.gz` members, and ingests each via
   the existing ingest core tagging `source="manual_upload"`. **The unzip +
   per-member parse happen in the 2 Gi job, never on the web.**

> ⚠️ **Not Cloud Tasks → an `/internal` HTTP handler.** That handler would run on
> the 512 Mi web service and OOM on the ~400 MB unzip. The trigger MUST be the
> 2 Gi Cloud Run JOB (Cloud Scheduler → `jobs:run`). `complete` therefore always
> returns `enqueue="scheduled"`.

### Backward compatibility
- Per-file `POST /imports` (single `.gpx`/`.fit`) — unchanged.
- Old whole-zip `POST /imports/strava-archive` — KEPT for small zips / the dev
  fallback (still capped at `MAX_IMPORT_FILE_SIZE`). The importer drains BOTH the
  new `pending_archives` (whole-zip) queue AND the legacy `pending_archive_files`
  (per-member) queue.
- Local/dev (no `UPLOADS_BUCKET`): `init` returns a backend upload path; the
  browser PUTs to `PUT /imports/strava-archive/upload/<archive_id>` (auth'd)
  which lands the bytes under `ARCHIVE_INTAKE_DIR`. Buffering on the web is
  acceptable ONLY here (dev has no Cloud Run cap / 512 Mi limit).

## Deploy wiring

`common-trails-ingest-pending-archives-${ENV}` is in the `JOBS` array of
`scripts/deploy-prod.sh` — every `deploy-prod.sh` re-syncs it to the api image.

### Create the job (once)
```bash
gcloud run jobs create common-trails-ingest-pending-archives-prod \
  --image="$IMAGE" --region="$REGION" \
  --service-account="$API_SA" \
  --set-cloudsql-instances="$CLOUDSQL_INSTANCE" \
  --memory=2Gi --cpu=1 --max-retries=1 --task-timeout=3600 \
  --set-env-vars=UPLOADS_BUCKET=common-trails-uploads-prod \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest \
  --command=python \
  --args=-m,app.jobs.ingest_pending_archives,--archive-limit,5,--limit,200,--pace,0.5
```
A Cloud Run Job is scale-to-zero by nature (it only runs when executed).

### Trigger — Cloud Scheduler → job execution
```bash
gcloud scheduler jobs create http common-trails-drain-archives-prod \
  --location="$REGION" --schedule="*/10 * * * *" \
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT/jobs/common-trails-ingest-pending-archives-prod:run" \
  --http-method=POST --oauth-service-account-email="$API_SA"
```
Or manually post-upload:
`gcloud run jobs execute common-trails-ingest-pending-archives-prod --region=$REGION`.

### GCS bucket CORS — REQUIRED for the browser PUT (document, do NOT apply here)
The direct browser→GCS PUT is cross-origin; without CORS the browser blocks it.
Apply once on the uploads bucket:
```bash
cat > /tmp/cors.json <<'JSON'
[{"origin": ["https://chemins-communs.fr"],
  "method": ["PUT"],
  "responseHeader": ["Content-Type"],
  "maxAgeSeconds": 3600}]
JSON
gcloud storage buckets update gs://common-trails-uploads-prod --cors-file=/tmp/cors.json
# legacy equivalent: gsutil cors set /tmp/cors.json gs://common-trails-uploads-prod
```

### Signer IAM
`generate_signed_url(version="v4")` needs signing creds. On Cloud Run the runtime
SA signs via the IAM `signBlob` API — grant it
`roles/iam.serviceAccountTokenCreator` **on itself**. Missing → `init` returns
503 (`storage signer misconfigured`), never a 500.

## Env vars
| var | default | purpose |
|---|---|---|
| `UPLOADS_BUCKET` | `""` | GCS bucket for archive objects; unset ⇒ local backend |
| `ARCHIVE_SIGNED_URL_TTL_MIN` | `30` | signed PUT URL TTL (minutes) |
| `MAX_ARCHIVE_UPLOAD_BYTES` (legacy alias `MAX_ARCHIVE_BYTES`) | `512 MiB` | sanity ceiling on one archive — re-checked at `/complete` (413 + row `failed` + delete) AND in the job (skip oversize from metadata, no download). Keep `<=` the importer job's tmpfs. |
| `ARCHIVE_INTAKE_DIR` | tmp | local fallback root (dev/tests) |
