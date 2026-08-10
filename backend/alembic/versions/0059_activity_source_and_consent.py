"""Activity provenance ``source`` + consent + paced archive-intake queue.

WHY: Strava's 2026 API Policy prohibits aggregating/redistributing
Strava-API-extracted data into a public heatmap. A user's OWN archive —
downloaded via Strava's official "request your data" flow and uploaded
by the user with explicit consent — is a DIFFERENT, legally-defensible
basis for contributing to the open (ODbL) community heatmap.

To keep the two sources distinguishable (so the eventual "public heatmap
draws ONLY from user-contributed archives" separation is a pure query
change), every activity now carries a provenance ``source``:

* ``NULL``            — legacy / pre-migration rows (unknown provenance).
* ``manual_upload``   — the user's own uploaded archive / GPX, consented.
* ``strava_api``      — pulled via the Strava OAuth API (NOT community-eligible).

``contribution_consents`` is the append-only audit trail proving the
ODbL-contribution basis: one row per consented archive submission with
the user, the exact consent version + wording shown, and a timestamp.

``pending_archive_files`` is the DURABLE QUEUE that makes archive ingest
PROGRESSIVE. A big archive (thousands of GPX) is NOT ingested on the
request thread; the upload stores each raw member to a per-user bucket
prefix and enqueues one row here, then a paced worker
(``app.jobs.ingest_pending_archives``) drains the queue in small batches
so ingest is absorbed over time (no f1-micro OOM / ingest spike).

DDL-ONLY. ``activities.source`` is nullable (no server-side default) so
adding it does NOT rewrite the existing table; legacy rows stay NULL.

Revision ID: 0059
Revises: 0058
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no server_default → metadata-only ALTER (no rewrite of the
    # large activities table). Legacy rows remain NULL = "unknown".
    op.execute("ALTER TABLE activities ADD COLUMN IF NOT EXISTS source VARCHAR(50)")
    # Partial index: only rows with a known provenance. Serves the eventual
    # "public heatmap = user-contributed only" filter without bloating the
    # index with the NULL legacy majority.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activities_source "
        "ON activities (source) WHERE source IS NOT NULL"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS contribution_consents (
            id UUID PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            source VARCHAR(50) NOT NULL,
            consent_version VARCHAR(100) NOT NULL,
            consent_text TEXT NOT NULL,
            locale VARCHAR(8) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contribution_consents_user_id "
        "ON contribution_consents (user_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_archive_files (
            id UUID PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            consent_id UUID NULL,
            source VARCHAR(50) NOT NULL DEFAULT 'manual_upload',
            storage_backend VARCHAR(16) NOT NULL,
            storage_key TEXT NOT NULL,
            original_filename VARCHAR(512) NULL,
            sport VARCHAR(50) NULL,
            contribute_heatmap BOOLEAN NOT NULL DEFAULT true,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            activity_id UUID NULL,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # The paced worker drains oldest-pending-first; a partial index on the
    # pending status keeps that SELECT off a full scan as the table grows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_archive_files_pending "
        "ON pending_archive_files (created_at) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_archive_files_user "
        "ON pending_archive_files (user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_archive_files")
    op.execute("DROP TABLE IF EXISTS contribution_consents")
    op.execute("DROP INDEX IF EXISTS ix_activities_source")
    op.execute("ALTER TABLE activities DROP COLUMN IF EXISTS source")
