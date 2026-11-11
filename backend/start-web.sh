#!/bin/sh
# Container entrypoint of the web/worker services: migrate, then serve.
# Exits non-zero when the migration cannot be applied (see backend/Dockerfile).
# ALEMBIC_CMD / SERVE_CMD / MIGRATE_RETRY_SLEEP exist for the unit test only.
ALEMBIC_CMD="${ALEMBIC_CMD:-alembic upgrade head}"
SERVE_CMD="${SERVE_CMD:-uvicorn app.main:app --host 0.0.0.0 --port 8787}"
SLEEP="${MIGRATE_RETRY_SLEEP:-3}"

i=1
while [ "$i" -le 5 ]; do
  if $ALEMBIC_CMD; then
    # shellcheck disable=SC2086
    exec $SERVE_CMD
  fi
  echo "Alembic attempt $i failed, retrying in ${SLEEP}s..." >&2
  sleep "$SLEEP"
  i=$((i + 1))
done
echo "FATAL: alembic upgrade head failed 5 times — refusing to start the API on an un-migrated schema." >&2
exit 1
