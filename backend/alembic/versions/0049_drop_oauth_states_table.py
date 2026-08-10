"""Drop the dead `oauth_states` table.

PR #349 (audit S3.3) replaced the DB-backed OAuth state with a signed
JWT. No code path has read or written `oauth_states` since #349
landed. The table was left in place for one release cycle as rollback
safety; #349 has been stable in prod and no rollback is needed.

The table was created at runtime by an idempotent
`CREATE TABLE IF NOT EXISTS oauth_states (...)` (no Alembic migration
ever declared it), so this migration is the FIRST Alembic-tracked
event touching the table. `DROP TABLE IF EXISTS` keeps the migration
idempotent — running it on a dev DB that never had the table is a
no-op, not an error.

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-27
"""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oauth_states")


def downgrade() -> None:
    # Recreate with the original runtime-created schema. We don't
    # restore data (it was always ephemeral OAuth state, max 30 min
    # TTL) — the next /connect or /login would have written fresh
    # rows anyway. Matches the original `_ensure_oauth_table` shape.
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
