"""Session generation counter on users.

``users.token_version`` is copied into every session JWT (claim ``tv``);
``app.api.auth.get_current_user`` rejects a token whose ``tv`` differs from
the row. Incrementing it ends every session of the account at once (logout,
confirmed email change, password dropped by a magic-link login).

Existing rows get 0, and a session token minted before this revision carries
no ``tv`` (read as 0): sessions open at deploy time stay valid.

Revision ID: 0073
Revises: 0072
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN with a constant default is not a table rewrite, but it still
    # takes an ACCESS EXCLUSIVE lock: give up after 5 s rather than queue every
    # request behind a long read — the entrypoint retries.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version "
        "integer NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS token_version")
