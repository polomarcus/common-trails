"""Add 'running' to sport_enum.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-11 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sport_enum ADD VALUE IF NOT EXISTS 'running'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values; leave as-is.
    pass
