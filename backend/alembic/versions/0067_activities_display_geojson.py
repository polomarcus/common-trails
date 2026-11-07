"""Add activities.display_geojson — compact geometry for the personal map.

``/map`` loads ``GET /me/activities?limit=5000`` to draw "Ma heatmap". It used
to ship every activity's full-resolution ``geometry_geojson`` (every GPS fix,
full float precision): 1.48 MB raw / 577 KB gzipped for ONE real 38k-point
ride. ``display_geojson`` stores the exact geometry the list endpoint serves:
gap-split on raw coords → Douglas-Peucker 2 m → coords rounded to 5 decimals
(see app.services.display_geometry) — 89 KB raw / 25 KB gzipped for that ride.

Written at ingest; pre-existing rows are filled by the one-shot, idempotent
``python -m app.cli.backfill_display_geojson``. A NULL cache is safe: the
endpoint serves the pre-cache full-precision geometry (heavier, never wrong).

TRACE INTEGRITY (Crouzet): geometry_geojson is untouched — this is a derived,
display-only cache; deleting the row deletes the cache with it (GDPR).
``/me/activities/{id}`` keeps serving full precision.

Revision ID: 0067
Revises: 0066
"""

import sqlalchemy as sa

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "activities",
        sa.Column("display_geojson", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("activities", "display_geojson")
