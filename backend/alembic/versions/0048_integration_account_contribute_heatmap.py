"""Add contribute_heatmap to integration_accounts (webhook hot-path).

Before this migration, the webhook worker
(`app/api/internal_strava_webhook.py:380-384`) looked up the user's
heatmap-contribution preference by querying their most-recent Strava
`Activity` row for every incoming event. At friends-beta scale that's
~500 redundant `SELECT … ORDER BY DESC LIMIT 1` calls per week on a
db-f1-micro instance whose pool is already tight (audit 2026-05-27
S2.7).

Moves the preference to a column on `integration_accounts` — read
once when the worker enriches the event, written when the user
toggles their setting or completes OAuth.

Backfill: copy the most-recent Strava activity's `contribute_heatmap`
per account when present; default to FALSE otherwise. This matches
the previous code's "no last_strava → contribute_heatmap=False"
branch exactly — opting in must be an active user choice (via the
consent screen on the import flow), never a silent default.

The column is NOT NULL with `server_default='false'` so any future
insert that forgets the field gets the safe default. NULL is
disallowed because the worker's hot path reads it without a None
fallback.

Revision ID: 0048
Revises: 0047
Create Date: 2026-05-27
"""
import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column with a server-side default so existing rows get
    # `true` immediately and the NOT NULL constraint is satisfiable.
    op.add_column(
        "integration_accounts",
        sa.Column(
            "contribute_heatmap",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Backfill from the user's most-recent Strava Activity row when
    # one exists — preserves the user's actual setting (could be
    # `false` if they opted out). Without this, an opted-out user
    # would have their preference silently flipped back to default.
    #
    # COALESCE guard: `Activity.contribute_heatmap` was added in 0001
    # WITHOUT `nullable=False` (ORM also omits it). A pre-existing NULL
    # row would crash this UPDATE on the new NOT NULL column —
    # half-applied migration state, hard to recover. PR #348 review S2
    # caught this. The COALESCE degrades NULL → false (safe default).
    op.execute(
        """
        UPDATE integration_accounts ia
        SET contribute_heatmap = COALESCE(a.contribute_heatmap, false)
        FROM (
            SELECT DISTINCT ON (user_id) user_id, contribute_heatmap
            FROM activities
            WHERE provider = 'strava'
            ORDER BY user_id, created_at DESC
        ) a
        WHERE ia.user_id = a.user_id
          AND ia.provider = 'strava'
        """
    )


def downgrade() -> None:
    op.drop_column("integration_accounts", "contribute_heatmap")
