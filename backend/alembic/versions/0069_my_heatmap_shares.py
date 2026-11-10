"""Sharing "Ma heatmap" through an unguessable token link.

Adds ``my_heatmap_shares`` — at most one active share per user. Only the
SHA-256 of the token is stored (a database leak does not give the links); the
clear token is shown once, at creation. Revoking = deleting the row (every
``/s/{token}/…`` URL answers 404 on the next request: lookup per request, no
server-side cache).

Adds to ``my_heatmap_files`` the pointer of the SHARED artefact — an
endpoint-masked, id-less PMTiles built in the same pass as the private file
(same snapshot, same compare-and-set): ``share_version`` (+ its size and
feature count). NULL = no shared artefact built for the current share.

Adds ``source`` to the columns whose change marks the file stale (the
UPDATE trigger of 0068 is re-created): the shared artefact keeps only
non-Strava-API traces, and duplicate promotion rewrites ``source``.

FK to ``users`` ON DELETE CASCADE (unlike ``my_heatmap_files``): shares are
only ever created by an authenticated user, and deleting the account (GDPR,
account merge) must kill the link even if every other step is forgotten.

Design: docs/ma-heatmap-fichier-prive.md §10.

Revision ID: 0069
Revises: 0068
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

_DISPLAYED_COLUMNS_0068 = (
    "user_id",
    "sport",
    "name",
    "display_geojson",
    "distance_m",
    "elevation_gain_m",
    "moving_time",
    "activity_date",
    "created_at",
    "provider",
    "provider_activity_id",
    "total_photo_count",
)
# MUST match my_heatmap_files.DISPLAYED_COLUMNS (pinned by
# test_my_heatmap_files::test_hook_columns_match_trigger).
_DISPLAYED_COLUMNS = (
    "user_id",
    "sport",
    "name",
    "display_geojson",
    "distance_m",
    "elevation_gain_m",
    "moving_time",
    "activity_date",
    "created_at",
    "provider",
    "provider_activity_id",
    "total_photo_count",
    "source",
)

# Same gate + body as 0068 (inert until my_heatmap_config.enabled).
_BUMP_SQL = """
    IF NOT COALESCE((SELECT enabled FROM my_heatmap_config WHERE id = 1), false) THEN
      RETURN NULL;
    END IF;
    INSERT INTO my_heatmap_files (user_id, data_rev, dirty_at)
    SELECT u, 1, now() FROM ({users}) AS t(u) WHERE u IS NOT NULL ORDER BY u
    ON CONFLICT (user_id) DO UPDATE
       SET data_rev = my_heatmap_files.data_rev + 1, dirty_at = now()
"""


def _replace_update_trigger_function(columns: tuple[str, ...]) -> None:
    changed = " OR ".join(f"o.{c} IS DISTINCT FROM n.{c}" for c in columns) + (
        " OR (n.display_geojson IS NULL AND o.geometry_geojson IS DISTINCT FROM n.geometry_geojson)"
    )
    users = f"""
            SELECT o.user_id FROM old_rows o JOIN new_rows n ON n.id = o.id WHERE {changed}
            UNION
            SELECT n.user_id FROM old_rows o JOIN new_rows n ON n.id = o.id WHERE {changed}
    """
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION my_heatmap_bump_upd() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          {_BUMP_SQL.format(users=users)};
          RETURN NULL;
        END $$;
        """
    )


def upgrade() -> None:
    op.create_table(
        "my_heatmap_shares",
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_my_heatmap_shares_token_sha256", "my_heatmap_shares", ["token_sha256"], unique=True
    )
    op.add_column("my_heatmap_files", sa.Column("share_version", sa.String(64), nullable=True))
    op.add_column("my_heatmap_files", sa.Column("share_pmtiles_bytes", sa.BigInteger(), nullable=True))
    op.add_column("my_heatmap_files", sa.Column("share_activity_count", sa.Integer(), nullable=True))
    _replace_update_trigger_function(_DISPLAYED_COLUMNS)


def downgrade() -> None:
    _replace_update_trigger_function(_DISPLAYED_COLUMNS_0068)
    op.drop_column("my_heatmap_files", "share_activity_count")
    op.drop_column("my_heatmap_files", "share_pmtiles_bytes")
    op.drop_column("my_heatmap_files", "share_version")
    op.drop_index("ix_my_heatmap_shares_token_sha256", table_name="my_heatmap_shares")
    op.drop_table("my_heatmap_shares")
