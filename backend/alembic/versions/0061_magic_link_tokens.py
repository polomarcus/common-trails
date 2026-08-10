"""Passwordless magic-link login — single-use token nonce table.

WHY: Phase 1 of email login (docs/email-auth-plan.md). A user enters their
email → receives a login link carrying a short-lived JWT (purpose=magic_link,
signed with JWT_SECRET) → clicking it issues the normal session cookie.

This table is the SINGLE-USE ledger: one row per issued link, keyed by the
JWT's ``jti``. Verification marks ``consumed_at`` — a reused or unknown jti is
rejected. The ``email`` + ``request_ip`` columns feed the request-side
rate-limit (COUNT over ``created_at`` per email / per IP per window).

The ``users.email`` column already exists (unique) since the initial schema —
a magic-link user is a full account, so no user-table change is needed here.

DDL-ONLY.

Revision ID: 0061
Revises: 0060
Create Date: 2026-07-14 00:00:00.000000
"""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS magic_link_tokens (
            jti UUID PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL,
            email VARCHAR(255) NOT NULL,
            request_ip VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_magic_link_tokens_user_id "
        "ON magic_link_tokens (user_id)"
    )
    # Rate-limit lookups: recent requests per email / per IP.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_magic_link_tokens_email_created "
        "ON magic_link_tokens (email, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_magic_link_tokens_ip_created "
        "ON magic_link_tokens (request_ip, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS magic_link_tokens")
