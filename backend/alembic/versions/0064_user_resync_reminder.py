"""Add users.last_resync_reminder_at for the 6-monthly re-sync reminder email.

The community heatmap is fed by MANUAL uploads only (Strava-API = personal), so
contributors go stale — rides after their last export never reach the map. A
periodic job (app.jobs.resync_reminder) emails each contributor at most every
~6 months to re-export + re-upload. This column tracks the last time we
reminded a given user so a monthly run never double-sends.

Revision ID: 0064
Revises: 0063
"""
import sqlalchemy as sa

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_resync_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_resync_reminder_at")
