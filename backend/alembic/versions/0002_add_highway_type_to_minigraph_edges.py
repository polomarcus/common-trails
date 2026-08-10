"""Add highway_type column to minigraph_edges for road-type cost weighting.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-28 00:00:00.000000
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # minigraph_edges is created at runtime by pgRouting seed, not by migrations.
    # Skip gracefully if the table doesn't exist yet.
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE minigraph_edges ADD COLUMN IF NOT EXISTS highway_type TEXT DEFAULT 'path';
        EXCEPTION
            WHEN undefined_table THEN NULL;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE minigraph_edges DROP COLUMN IF EXISTS highway_type;
        EXCEPTION
            WHEN undefined_table THEN NULL;
        END $$
    """)
