"""Add `export_requests` table for async heatmap export pipeline.

Phase 3 of the heatmap-export feature (PRD #391 §5) — third-party
clients can request exports for bboxes too large to build inside the
Cloud Run synchronous request budget (50 km × 50 km is the sync cap;
async lifts this to 250 km × 250 km).

Lifecycle:

- ``POST /export/heatmap/request`` inserts a row with ``status='queued'``
  and enqueues a Cloud Task. Returns the row's UUID + poll URL to the
  caller.
- The Cloud Tasks worker (``POST /internal/export/build/{id}``) updates
  ``status`` → ``running`` → ``ready`` / ``failed`` and writes
  ``gcs_uri`` on success.
- ``GET /export/heatmap/{id}`` polls the row. After ``expires_at``
  the endpoint returns 410 Gone.

Cleanup: ``app.jobs.refresh_matview`` (or a dedicated CLI) deletes rows
where ``expires_at < NOW()`` so the table stays tiny. The GCS lifecycle
rule on the ``archive/`` prefix (terraform ``google_storage_bucket.heatmap``
already declares ``age=90`` on that prefix) handles the binaries.

Revision ID: 0054
Revises: 0053
Create Date: 2026-06-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_requests",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        # user_id is nullable — anonymous export requests are allowed.
        # When set, must reference an existing user; cascade delete so a
        # cleaned-up user doesn't leave dangling requests.
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # 'geojson' | 'mbtiles' | 'pmtiles' | 'kml' | 'kmz'.
        sa.Column("format", sa.String(32), nullable=False),
        # bbox stored as JSONB `[w, s, e, n]` for cheap serialization +
        # easy debugging from psql; we don't query by bbox so an index
        # isn't worth it.
        sa.Column("bbox", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("sport", sa.String(32), nullable=True),
        sa.Column("min_uc", sa.Integer, nullable=True),
        sa.Column("days", sa.Integer, nullable=True),
        # 'queued' | 'running' | 'ready' | 'failed'
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "progress",
            sa.Float,
            nullable=False,
            server_default="0",
        ),
        # GCS URI on success: gs://bucket/archive/{id}/{format}.{ext}.
        # NULL until status='ready'.
        sa.Column("gcs_uri", sa.Text, nullable=True),
        # Error message on failure. NULL until status='failed'.
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # 24 h TTL. Hard-coded in the DDL default so an enqueue path
        # that forgets the column still gets the right cleanup window.
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW() + INTERVAL '24 hours'"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'failed')",
            name="ck_export_requests_status",
        ),
    )
    # Cleanup job scans expired rows — index keeps the DELETE cheap.
    op.create_index(
        "ix_export_requests_expires_at",
        "export_requests",
        ["expires_at"],
    )
    # Per-user list (future "your recent exports" UI). Cheap secondary
    # index; the table stays small (24h TTL + manual cleanup).
    op.create_index(
        "ix_export_requests_user_status",
        "export_requests",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_export_requests_user_status", table_name="export_requests")
    op.drop_index("ix_export_requests_expires_at", table_name="export_requests")
    op.drop_table("export_requests")
