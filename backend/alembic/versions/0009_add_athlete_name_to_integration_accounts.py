"""Add athlete_name to integration_accounts.

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-12 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_accounts", sa.Column("athlete_name", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("integration_accounts", "athlete_name")
