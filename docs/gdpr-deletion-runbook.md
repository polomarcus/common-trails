# GDPR deletion runbook — full account erasure (operator manual)

Audience: the operator (Paul) handling a "supprimez mon compte et toutes mes
données" request received at the privacy-policy contact address.

Scope: **full account deletion**. Single-activity deletion exists as an API
endpoint (`DELETE /me/activities/{id}`, `backend/app/api/me_activities.py`) but
there is **currently no button for it in the UI** (the old `/stats#mes-traces`
list was removed), so an operator may have to run it on the user's behalf.

**Raw-trace era note.** The community map is now built from `activities`
directly (`HEATMAP_DISPLAY_SOURCE=raw`); deleting the user's `activities` rows
(step 4) and rebuilding the PMTiles (step 7) is what removes their traces from
the map. The legacy `heat_*` tables of steps 2, 3, 6 and 9 may no longer exist
in prod — **skip any statement whose table is absent** (check with
`\dt heat_*`); `activity_deletion.py` does the same (`_relation_exists`).

Time budget: ~15 min. Everything below is copy-paste-able; replace the two
placeholders:

- `:USER_ID` — the user's UUID (string form)
- `:HASH` — the user's heat-contributor hash (computed in step 1)

Connect to prod:

```bash
gcloud sql connect common-trails-prod --user=postgres --database=common_trails
```

---

## 1. Identify the user + compute the contributor hash

```sql
SELECT id, email, username, created_at FROM users WHERE email = 'requester@example.com';
```

The community heat layer keys contributions by a SHA-256-derived hash of the
user id (see `ingest.py`), NOT the user id itself. Compute it locally:

```bash
python3 -c "import hashlib; print(int(hashlib.sha256('USER_ID_UUID_HERE'.encode()).hexdigest()[:8], 16))"
```

That integer is `:HASH` below.

## 2. Remove the user's community heat-edge contributions (legacy — skip if `heat_edges` is absent)

Same recompute-from-source mechanics as the self-service endpoint
(`app/services/activity_deletion.py`), but scoped by `user_id_hash` instead
of one `activity_id`. Run as ONE transaction:

```sql
BEGIN;

-- 2a. Capture the touched edges + OSM ways BEFORE anything is deleted
--     (needed for the heat_edges_agg recompute in step 6).
CREATE TEMP TABLE _touched_edges AS
  SELECT DISTINCT edge_key FROM heat_edge_contributors WHERE user_id_hash = :HASH;
CREATE TEMP TABLE _touched_ways AS
  SELECT DISTINCT osm_way_id FROM heat_edges
  WHERE edge_key IN (SELECT edge_key FROM _touched_edges)
    AND osm_way_id IS NOT NULL;

-- 2b. Delete the user's contributor rows.
DELETE FROM heat_edge_contributors WHERE user_id_hash = :HASH;

-- 2c. Recount the touched edges from the REMAINING contributors.
--     pass_count == contributor-row count by construction (migration 0052);
--     forward/backward have no per-contributor attribution → clamp.
UPDATE heat_edges he
SET pass_count = r.pc,
    user_count = r.uc,
    forward_count = LEAST(he.forward_count, r.pc),
    backward_count = LEAST(he.backward_count, r.pc)
FROM (
    SELECT edge_key, COUNT(*) AS pc, COUNT(DISTINCT user_id_hash) AS uc
    FROM heat_edge_contributors
    WHERE edge_key IN (SELECT edge_key FROM _touched_edges)
    GROUP BY edge_key
) r
WHERE he.edge_key = r.edge_key;

-- 2d. Drop edges the user was the only contributor to.
DELETE FROM heat_edges
WHERE edge_key IN (SELECT edge_key FROM _touched_edges)
  AND NOT EXISTS (
      SELECT 1 FROM heat_edge_contributors c WHERE c.edge_key = heat_edges.edge_key
  );

-- Keep the way list for step 6 before COMMIT drops the temp tables:
SELECT array_agg(osm_way_id) FROM _touched_ways;   -- ← copy this output

COMMIT;
```

## 3. Remove the legacy heat-cell contributions (skip if `heat_cells` is absent)

`heat_cells` is a legacy layer (not part of the current PMTiles display
pipeline) but it is still written at ingest and must be cleaned. There is no
per-activity attribution here — only per-user:

```sql
BEGIN;
CREATE TEMP TABLE _touched_cells AS
  SELECT DISTINCT cell_key, sport FROM heat_cell_contributors WHERE user_id_hash = :HASH;
DELETE FROM heat_cell_contributors WHERE user_id_hash = :HASH;
UPDATE heat_cells hc
SET user_count = r.cnt
FROM (
    SELECT cell_key, sport, COUNT(*) AS cnt
    FROM heat_cell_contributors
    WHERE (cell_key, sport) IN (SELECT cell_key, sport FROM _touched_cells)
    GROUP BY cell_key, sport
) r
WHERE hc.cell_key = r.cell_key AND hc.sport = r.sport;
DELETE FROM heat_cells hc
WHERE (hc.cell_key, hc.sport) IN (SELECT cell_key, sport FROM _touched_cells)
  AND NOT EXISTS (
      SELECT 1 FROM heat_cell_contributors c
      WHERE c.cell_key = hc.cell_key AND c.sport = hc.sport
  );
COMMIT;
```

Note: `pass_count` on surviving heat_cells is not per-user attributable
(known approximation on a legacy layer; disappears entirely on the next full
`rebuild_heatmap`).

## 4. Delete the user's private rows

```sql
BEGIN;
-- Activities: cascades activity_cells + activity_photos via FK ON DELETE CASCADE.
DELETE FROM activities             WHERE user_id  = ':USER_ID';
DELETE FROM integration_accounts   WHERE user_id  = ':USER_ID';   -- Strava OAuth tokens
DELETE FROM import_jobs            WHERE user_id  = ':USER_ID';
DELETE FROM magic_link_tokens      WHERE user_id  = ':USER_ID';
DELETE FROM pending_archive_files  WHERE user_id  = ':USER_ID';
DELETE FROM pending_archives       WHERE user_id  = ':USER_ID';
DELETE FROM export_requests        WHERE user_id  = ':USER_ID';
-- Frozen social features — usually empty, but check:
DELETE FROM routes                 WHERE owner_id = ':USER_ID';   -- cascades versions/forks/suggestions/tags/annotations/collection items
DELETE FROM route_collections      WHERE owner_id = ':USER_ID';   -- cascades items/annotations
DELETE FROM trips                  WHERE owner_id = ':USER_ID';   -- cascades stages/POIs
-- Consent audit trail: see the note below before deleting.
DELETE FROM contribution_consents  WHERE user_id  = ':USER_ID';
-- Finally the account itself (notifications cascade via FK).
DELETE FROM users                  WHERE id       = ':USER_ID';
COMMIT;
```

**Consent-record note.** `contribution_consents` is the legal proof that the
user's uploads were consented. Retaining proof of past consent after an
erasure request is defensible under GDPR (legal-obligation/defence basis),
but the row contains only `user_id` + consent wording — once `users` is
gone the id no longer maps to a person we can identify. Default: delete it
with everything else (data minimization). If a dispute is anticipated,
export the rows to a dated file first.

## 5. Delete GCS objects under the user's prefix

Uploaded archives (whole zips + extracted members) live under one per-user
prefix in the uploads bucket (`UPLOADS_BUCKET` env on the prod service;
prefix logic in `app/services/archive_intake.py`):

```bash
# Verify first
gsutil ls -r "gs://<UPLOADS_BUCKET>/archive-intake/USER_ID/" | head

# Then delete
gsutil -m rm -r "gs://<UPLOADS_BUCKET>/archive-intake/USER_ID/"
```

If the prefix doesn't exist, `gsutil` errors with "matched no objects" —
that's fine (user never uploaded an archive).

## 6. (Legacy) heat_edges_agg recompute — skip in raw mode

Only relevant if the matched-era `heat_edges_agg` table still exists. The
`rebuild-heat-agg` Cloud Run Job was removed at the raw-trace pivot; in raw
mode the build-pmtiles job alone removes the user's traces from the map
(step 7). If the table exists and you captured the way list in step 2, the
scoped recompute is still available:

```python
from app.db.session import SessionLocal
from app.jobs.rebuild_heat_agg import recompute_heat_agg_for_ways
db = SessionLocal()
deleted, upserted = recompute_heat_agg_for_ways(db, [<way ids from step 2>])
db.commit(); db.close()
```

## 7. Rebuild the published PMTiles

The static `heatmap-display.pmtiles` on GCS still renders the pre-deletion
state until rebuilt (runs on db-f1-micro, no tier bump). The build also prunes
the older pinned snapshots, so the deleted traces do not survive in an old
snapshot on our bucket. Details: [`runbooks/heatmap-rebuild.md`](runbooks/heatmap-rebuild.md).

```bash
gcloud run jobs execute common-trails-build-pmtiles-prod --region=europe-west1 --wait
```

## 8. Distinct-user floor side-effect

Prod runs `HEATMAP_MIN_USERS = 1` (K = 1 during the beta), so removing one
contributor only removes that contributor's traces. Once the floor is raised to
2, removing one contributor can also hide cells that drop below 2 distinct
users — that is the gate working as designed, not data loss for the remaining
contributors.

## 9. Confirm to the requester

Verification queries (all must return 0; drop the `heat_*` lines if those
tables no longer exist):

```sql
SELECT
  (SELECT count(*) FROM users                 WHERE id = ':USER_ID')       AS users,
  (SELECT count(*) FROM activities            WHERE user_id = ':USER_ID')  AS activities,
  (SELECT count(*) FROM integration_accounts  WHERE user_id = ':USER_ID')  AS integrations,
  (SELECT count(*) FROM heat_edge_contributors WHERE user_id_hash = :HASH) AS heat_contribs,
  (SELECT count(*) FROM heat_cell_contributors WHERE user_id_hash = :HASH) AS cell_contribs;
```

Reply within the 1-month GDPR window; mention that ODbL exports produced
BEFORE the deletion may persist in third-party copies. Those exports contain
the user's **raw traces with masked endpoints** (not anonymous aggregates), so
say so plainly (see [`privacy-model.md`](privacy-model.md) §5).
