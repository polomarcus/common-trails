"""Add compound index on activities(user_id, provider, provider_activity_id).

Speeds up the idempotency check during Strava import (avoids table scan).

Revision ID: 0014
Revises: 0013
Create Date: 2026-03-13 00:00:00.000000

"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_activities_user_provider_extid",
        "activities",
        ["user_id", "provider", "provider_activity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_activities_user_provider_extid", table_name="activities")
