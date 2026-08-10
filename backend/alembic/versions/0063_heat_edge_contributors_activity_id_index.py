"""Index heat_edge_contributors.activity_id for GDPR per-activity deletion.

DELETE /me/activities/{id} removes the activity's contributor rows by
``activity_id`` (per-activity attribution exists since migration 0052 —
the PK is (edge_key, user_id_hash, activity_id)). Without this index the
DELETE seq-scans the whole contributors table (~5 M rows in prod) per
deletion; with it, it's an index range scan over the activity's own rows.

Revision ID: 0063
Revises: 0062
Create Date: 2026-07-20 00:00:00.000000
"""
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_heat_edge_contributors_activity_id "
        "ON heat_edge_contributors (activity_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_heat_edge_contributors_activity_id")
