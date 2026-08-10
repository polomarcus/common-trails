"""Add ``purpose`` to magic_link_tokens — types the single-use JWT ledger.

WHY: account unification (Strava ⇄ email). A Strava-OAuth user has a SYNTHETIC
``strava_<id>@strava.local`` email and can't be reached by the #476 magic-link
login. ``POST /auth/me/email`` lets such a user set their REAL email, confirmed
by a magic-link sent to the NEW address (purpose=``email_change``) so we never
bind an unverified address. That confirmation token reuses this SAME single-use
ledger as the login token (purpose=``magic_link``); the new ``purpose`` column
keeps the two flows disjoint:

  * the login rate-limit COUNT only counts ``magic_link`` rows, and
  * the confirm endpoint only consumes ``email_change`` rows (atomic UPDATE
    ... WHERE purpose='email_change').

Existing rows are login tokens → backfilled to ``magic_link`` via the server
default. DDL-ONLY.

Revision ID: 0062
Revises: 0061
Create Date: 2026-07-14 00:00:00.000000
"""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE magic_link_tokens "
        "ADD COLUMN IF NOT EXISTS purpose VARCHAR(32) NOT NULL DEFAULT 'magic_link'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE magic_link_tokens DROP COLUMN IF EXISTS purpose")
