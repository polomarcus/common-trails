"""Whole-archive intake queue for the signed-URL direct-to-GCS upload flow.

WHY: the #451 path read the whole .zip into the web request thread
(``content = await file.read()``) and split it into per-member
``pending_archive_files`` rows THERE. That can't work for a REAL Strava
export (~70 MB zipped, ~400 MB unzipped, 1406 members): Cloud Run caps a
request body at ~32 MB, and even raising the cap would OOM the 512 Mi web
instance that must never hold the archive bytes.

New flow (browser → GCS direct via a V4 signed PUT URL → cold-start 2 Gi
importer):

* ``POST /imports/strava-archive/init``     — records consent, creates ONE
  ``pending_archives`` row (status ``awaiting_upload``), returns a signed
  PUT URL for ``archive-intake/<user_id>/<uuid>.zip``.
* browser PUTs the .zip straight to GCS (bypasses Cloud Run entirely).
* ``POST /imports/strava-archive/complete`` — verifies the object exists +
  belongs to the user, flips the row to ``uploaded``.
* the scheduled 2 Gi Cloud Run job ``app.jobs.ingest_pending_archives``
  STREAMS the .zip from the bucket, walks its members, and ingests each —
  the unzip + per-member parse happens in the JOB, never on the web.

DDL-ONLY. The per-member ``pending_archive_files`` table (migration 0059)
is KEPT for backward compatibility with the small-zip direct endpoint.

Revision ID: 0060
Revises: 0059
Create Date: 2026-07-13 00:00:00.000000
"""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_archives (
            id UUID PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            consent_id UUID NULL,
            source VARCHAR(50) NOT NULL DEFAULT 'manual_upload',
            storage_backend VARCHAR(16) NOT NULL,
            bucket_key TEXT NOT NULL,
            fallback_sport VARCHAR(50) NOT NULL DEFAULT 'road',
            contribute_heatmap BOOLEAN NOT NULL DEFAULT true,
            status VARCHAR(20) NOT NULL DEFAULT 'awaiting_upload',
            attempts INTEGER NOT NULL DEFAULT 0,
            members_total INTEGER NULL,
            imported INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # The scheduled worker drains oldest-uploaded-first; a partial index on
    # the uploaded status keeps that SELECT off a full scan as rows pile up.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_archives_uploaded "
        "ON pending_archives (created_at) WHERE status = 'uploaded'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_archives_user "
        "ON pending_archives (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_archives")
