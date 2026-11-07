#!/usr/bin/env bash
# Full import of a REAL export (Strava / Garmin .zip) in THROWAWAY containers.
# LOCAL ONLY — never in CI, never commit the archive or its output.
#
#   make e2e-import-real ARCHIVE=/path/to/export.zip      (REDROP=1 to re-drop it)
#
# Isolation: a unique docker network created --internal (no internet, no host
# port published) + its own PostGIS + a backend container running the working
# tree's code (mounted read-only). The shared dev stack (8787/3787/5487) is
# never touched. Everything is removed on exit, including the database.
# See docs/archive-import-testing.md.
set -euo pipefail

ARCHIVE="${1:-${ARCHIVE:-}}"
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "usage: $0 /path/to/export.zip  (or make e2e-import-real ARCHIVE=…)" >&2
  exit 2
fi
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
P="ct-import-real-$(date +%s)-$$"
# Image tag = hash of what goes into the deps layer (pyproject + Dockerfile):
# a dependency change (e.g. fitdecode added) builds a fresh image instead of
# silently reusing a stale one. E2E_IMPORT_REBUILD=1 forces a rebuild anyway.
DEPS_HASH="$(cat "$REPO/backend/pyproject.toml" "$REPO/backend/Dockerfile" | shasum -a 256 | cut -c1-12)"
IMAGE="${E2E_IMPORT_IMAGE:-ct-e2e-import-backend:$DEPS_HASH}"
MEM="${E2E_IMPORT_MEM:-8g}"   # the prod importer job's limit

cleanup() {
  docker rm -f "$P-db" "$P-run" >/dev/null 2>&1 || true
  docker network rm "$P-net" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! docker image inspect "$IMAGE" >/dev/null 2>&1 || [[ "${E2E_IMPORT_REBUILD:-0}" == "1" ]]; then
  echo "▸ building $IMAGE (backend deps, no DEM bake)…"
  docker build -q --build-arg SKIP_DEM_BAKE=1 -t "$IMAGE" "$REPO/backend" >/dev/null
fi

echo "▸ throwaway project $P (internal network, no host port)"
docker network create --internal "$P-net" >/dev/null
docker run -d --name "$P-db" --network "$P-net" --network-alias db \
  -e POSTGRES_DB=common_trails -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  postgis/postgis:17-3.6-alpine >/dev/null
for _ in $(seq 1 60); do
  docker exec "$P-db" pg_isready -U postgres -q 2>/dev/null && break
  sleep 1
done
sleep 2  # postgis init restarts the server once

# --memory-swap = --memory: no swap, like Cloud Run — over the limit is an OOM kill.
set +e
docker run --name "$P-run" --network "$P-net" --memory "$MEM" --memory-swap "$MEM" \
  -e DATABASE_URL=postgresql://postgres:postgres@db:5432/common_trails \
  -e TEST_MODE=true -e JWT_SECRET=local-import-real -e SKIP_DEM_ENRICHMENT=true \
  -e DEM_PROVIDER=none -e OVERPASS_ENABLED=false -e HEATMAP_K_ANONYMITY=1 \
  -e ARCHIVE_INTAKE_DIR=/tmp/intake -e UPLOADS_BUCKET= -e PYTHONDONTWRITEBYTECODE=1 \
  -e REDROP="${REDROP:-0}" \
  -v "$REPO/backend:/app:ro" \
  -v "$REPO/scripts/e2e_import_real_driver.py:/driver.py:ro" \
  -v "$ARCHIVE:/input/archive.zip:ro" \
  -w /app "$IMAGE" python -W ignore /driver.py

RC=$?
set -e
OOM_KILLED=$(docker inspect "$P-run" --format '{{.State.OOMKilled}}' 2>/dev/null || echo unknown)
if [[ "$OOM_KILLED" == "true" || "$RC" == "137" ]]; then
  echo "✗ OOM-KILLED: the import exceeded the $MEM memory limit (exit $RC) — the prod job would die the same way." >&2
  exit 137
fi
echo "container OOM-killed: $OOM_KILLED (limit $MEM, no swap), exit $RC"
exit "$RC"
