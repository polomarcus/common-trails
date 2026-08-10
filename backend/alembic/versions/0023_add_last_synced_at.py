"""Add last_synced_at and sync_failures to integration_accounts.

Revision ID: 0023
Revises: 0022
Create Date: 2026-03-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "integration_accounts",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integration_accounts",
        sa.Column("sync_failures", sa.Integer, server_default="0", nullable=False),
    )
    # Backfill last_synced_at from latest COMPLETED import job, fallback to created_at
    op.execute("""
        UPDATE integration_accounts ia
        SET last_synced_at = COALESCE(
            (SELECT MAX(ij.updated_at)
             FROM import_jobs ij
             WHERE ij.user_id = ia.user_id
               AND ij.provider = 'strava'
               AND ij.status = 'COMPLETED'),
            ia.created_at
        )
        WHERE ia.provider = 'strava'
          AND ia.last_synced_at IS NULL
    """)


def downgrade():
    op.drop_column("integration_accounts", "sync_failures")
    op.drop_column("integration_accounts", "last_synced_at")
