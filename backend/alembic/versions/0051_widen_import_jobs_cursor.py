"""Widen `import_jobs.cursor` from VARCHAR(255) to TEXT.

The "cursor" column is a JSON blob holding progress markers across the
4-phase Strava import (contribute_heatmap, phase, current_page,
photos_imported, last_failed_phase, …). It started as a small
pagination token (~10 chars, hence the original `String(255)`) but
accreted more fields over time. On error paths that merge a long
`last_error` into the cursor, observed near the 255-char ceiling. A
single character over the cap rejects the UPDATE at the DB layer →
the outer exception handler catches → tries to merge a slightly
longer cursor → fails again → cascade.

`TEXT` removes the foot-gun. PostgreSQL `TEXT` has no length cap
(documented limit is 1 GB but practical limit is "however much you
want to round-trip in one query"). Storage cost is identical for
strings under ~125 bytes (toast threshold).

Audit 2026-05-29 ST-S2.6.

Revision ID: 0051
Revises: 0050
Create Date: 2026-05-29
"""
import sqlalchemy as sa

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "import_jobs",
        "cursor",
        existing_type=sa.String(255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # NOTE: if any rows have cursor > 255 chars at downgrade time, this
    # ALTER will fail. The migration is forward-safe but not strictly
    # reversible once long cursors land. Acceptable trade-off — the
    # downgrade is only useful in dev, and the operator can clear long
    # cursors via `UPDATE import_jobs SET cursor = NULL WHERE
    # char_length(cursor) > 255` before the downgrade if needed.
    op.alter_column(
        "import_jobs",
        "cursor",
        existing_type=sa.Text(),
        type_=sa.String(255),
        existing_nullable=True,
    )
