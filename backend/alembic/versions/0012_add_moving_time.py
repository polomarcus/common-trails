"""Add moving_time column to activities.

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-13 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("moving_time", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "moving_time")
