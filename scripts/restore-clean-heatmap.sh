#!/usr/bin/env bash
# Restore the "clean continuous heatmap from Paul's rides" local state.
# Single-user (no multi-user demo pollution), realistic pass_counts,
# built pmtiles. ~2 min vs a ~1.5h full rebuild.
# Captured 2026-06-14 from data/strava-export. See memory: reference_clean_local_heatmap_snapshot.
set -euo pipefail
cd "$(dirname "$0")/.."
SNAP=data/clean-snapshot
[ -f "$SNAP/clean-heatmap.sql.gz" ] || { echo "ERROR: $SNAP/clean-heatmap.sql.gz missing"; exit 1; }

echo "[1/5] Truncating + restoring single-user activities + heat_edges..."
docker compose exec -T db psql -U postgres -d common_trails -c \
  "TRUNCATE TABLE heat_edge_contributors, heat_edges, activities RESTART IDENTITY CASCADE;" >/dev/null
gunzip -c "$SNAP/clean-heatmap.sql.gz" | docker compose exec -T db psql -U postgres -d common_trails -q
docker compose exec -T db psql -U postgres -d common_trails -c "ANALYZE heat_edges; ANALYZE heat_edge_contributors;" >/dev/null

echo "[2/5] Rebuilding the display aggregate (heat_edges_agg) from restored heat_edges..."
# heat_edges_agg (migration 0057) is DERIVED and NOT in the snapshot — it's
# rebuilt here so the all-time heatmap (PMTiles + live MVT, which read the agg
# table) is NON-blank after a restore. One fast pass over the single-user
# corpus (~50k ways, seconds). Guarded on table existence so a pre-0057 DB
# (table absent) restores cleanly without erroring.
if docker compose exec -T db psql -U postgres -d common_trails -tAc \
     "SELECT to_regclass('public.heat_edges_agg')" | grep -q heat_edges_agg; then
  docker compose exec -T backend python -m app.jobs.rebuild_heat_agg >/dev/null
  AGG=$(docker compose exec -T db psql -U postgres -d common_trails -tAc "SELECT count(*) FROM heat_edges_agg")
  echo "  heat_edges_agg=$AGG rows"
else
  echo "  heat_edges_agg absent (pre-0057 schema) — skipping; run 'alembic upgrade head' + 'make heatmap-restore'"
fi

echo "[3/5] Restoring built display artifact (pmtiles)..."
# inode-preserving write for pmtiles (frontend bind-mount latched onto it)
cat "$SNAP/heatmap-display.pmtiles" > frontend/public/heatmap-display.pmtiles

echo "[4/5] Rebuilding + serving frontend (snapshots out/ with the artifacts)..."
( cd frontend && npm run build >/tmp/restore-fe-build.log 2>&1 ) || { echo "FE build failed (see /tmp/restore-fe-build.log)"; exit 1; }
docker compose up --build frontend -d >/tmp/restore-fe-serve.log 2>&1

echo "[5/5] Verifying..."
sleep 3
HE=$(docker compose exec -T db psql -U postgres -d common_trails -tAc "SELECT count(*) FROM heat_edges")
MAXPC=$(docker compose exec -T db psql -U postgres -d common_trails -tAc "SELECT max(pass_count) FROM heat_edges")
PM=$(curl -s -o /dev/null -w "%{http_code}" localhost:3787/heatmap-display.pmtiles)
echo "  heat_edges=$HE  max_pass_count=$MAXPC  pmtiles_http=$PM"
# heat_edges is PARTITIONED — a snapshot taken with `-t heat_edges` (no child
# glob) loads 0 rows. Fail loudly rather than leave an empty table behind.
if [ "${HE:-0}" -lt 100000 ]; then
  echo "❌ RESTORE FAILED: heat_edges=$HE (expected ~2.1M). The snapshot dump is"
  echo "   missing partition-child data — re-capture with 'make heatmap-snapshot'"
  echo "   (the 'heat_edges*' glob), then 'make heatmap-restore' again."
  exit 1
fi
echo "✅ Clean heatmap restored — open http://localhost:3787/map"
