"""Make hashed_password nullable for Strava-only users.

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-12 00:00:00.000000

"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "hashed_password", nullable=True)


def downgrade() -> None:
    op.alter_column("users", "hashed_password", nullable=False)
