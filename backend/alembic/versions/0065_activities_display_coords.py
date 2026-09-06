"""Add activities.display_coords — precomputed cleaned coords for the raw build.

Every heatmap rebuild re-streams the WHOLE community corpus as geometry_geojson
TEXT from the db-f1-micro TWICE (pass 1 lattice + pass 2 emit) and re-runs the
json.loads → bbox-clip → outlier-reject stages per activity. At 17.4k activities
/ 727 MB of JSON that is ~1.4 GB streamed off the micro instance per rebuild —
the dominant rebuild cost (O(corpus) per upload).

``display_coords`` caches the DISPLAY-ONLY derived polyline (post bbox-clip +
outlier-reject, PRE densify + mask, so the remaining read-time stages run in the
exact same order → byte-identical output): zlib'd float64 (lon, lat) pairs,
~3.6× smaller than the JSON and ~10× cheaper to decode. ``display_coords_params``
is the parameter fingerprint (version | max-span | raw-bbox) — the reader uses
the blob ONLY when it matches the current env, else falls back to the live
pipeline (self-healing on param/algorithm changes; see
raw_trace_display.display_coords_fingerprint).

TRACE INTEGRITY (Crouzet): geometry_geojson is untouched — this is a derived,
display-only cache; deleting the row deletes the cache with it (GDPR).

Revision ID: 0065
Revises: 0064
"""
import sqlalchemy as sa

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "activities",
        sa.Column("display_coords", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "activities",
        sa.Column("display_coords_params", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("activities", "display_coords_params")
    op.drop_column("activities", "display_coords")
