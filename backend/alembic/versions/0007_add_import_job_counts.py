"""Add total_count and skipped_count to import_jobs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-12 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("total_count", sa.Integer, server_default="0"))
    op.add_column("import_jobs", sa.Column("skipped_count", sa.Integer, server_default="0"))


def downgrade() -> None:
    op.drop_column("import_jobs", "skipped_count")
    op.drop_column("import_jobs", "total_count")
