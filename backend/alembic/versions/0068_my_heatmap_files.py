"""Per-user private "Ma heatmap" file (PMTiles in a private GCS bucket).

Adds ``my_heatmap_files`` — one row per user: the published file version (the
DB-side pointer, compare-and-set on ``built_rev``) and a ``data_rev`` counter
that statement-level triggers on ``activities`` bump whenever a column the
file displays changes. ``built_rev = data_rev`` ⇔ the file is fresh. The
triggers make staleness exact for EVERY write path (ORM, raw SQL, bulk
updates, account merge) — see docs/ma-heatmap-fichier-prive.md §6.

No FK to ``users``: activities themselves carry none and many tests insert
rows for ad-hoc user ids; account deletion removes the row + objects
explicitly (``app.cli.my_heatmap_files delete``).

Revision ID: 0068
Revises: 0067
"""

import sqlalchemy as sa

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

# Columns whose change alters what the file (tiles + summary) shows.
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
)

_BUMP_SQL = """
    INSERT INTO my_heatmap_files (user_id, data_rev, dirty_at)
    SELECT u, 1, now() FROM ({users}) AS t(u) WHERE u IS NOT NULL
    ON CONFLICT (user_id) DO UPDATE
       SET data_rev = my_heatmap_files.data_rev + 1, dirty_at = now()
"""


def upgrade() -> None:
    op.create_table(
        "my_heatmap_files",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("data_rev", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("built_rev", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("previous_version", sa.String(64), nullable=True),
        sa.Column("activity_count", sa.Integer(), nullable=True),
        sa.Column("pmtiles_bytes", sa.BigInteger(), nullable=True),
        sa.Column("summary_bytes", sa.BigInteger(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dirty_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    # geometry_geojson only matters while the display cache is NULL (the file
    # then derives from it); comparing two large TOASTed texts on EVERY update
    # would double the geometry I/O — every geometry writer also rewrites
    # display_geojson, which is compared above.
    changed = " OR ".join(f"o.{c} IS DISTINCT FROM n.{c}" for c in _DISPLAYED_COLUMNS) + (
        " OR (n.display_geojson IS NULL AND o.geometry_geojson IS DISTINCT FROM n.geometry_geojson)"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION my_heatmap_bump_ins() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          {_BUMP_SQL.format(users="SELECT DISTINCT user_id FROM new_rows")};
          RETURN NULL;
        END $$;

        CREATE OR REPLACE FUNCTION my_heatmap_bump_del() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          {_BUMP_SQL.format(users="SELECT DISTINCT user_id FROM old_rows")};
          RETURN NULL;
        END $$;

        CREATE OR REPLACE FUNCTION my_heatmap_bump_upd() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          {_BUMP_SQL.format(users=f'''
            SELECT o.user_id FROM old_rows o JOIN new_rows n ON n.id = o.id WHERE {changed}
            UNION
            SELECT n.user_id FROM old_rows o JOIN new_rows n ON n.id = o.id WHERE {changed}
          ''')};
          RETURN NULL;
        END $$;

        CREATE TRIGGER activities_my_heatmap_ins AFTER INSERT ON activities
          REFERENCING NEW TABLE AS new_rows
          FOR EACH STATEMENT EXECUTE FUNCTION my_heatmap_bump_ins();
        CREATE TRIGGER activities_my_heatmap_del AFTER DELETE ON activities
          REFERENCING OLD TABLE AS old_rows
          FOR EACH STATEMENT EXECUTE FUNCTION my_heatmap_bump_del();
        CREATE TRIGGER activities_my_heatmap_upd AFTER UPDATE ON activities
          REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
          FOR EACH STATEMENT EXECUTE FUNCTION my_heatmap_bump_upd();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS activities_my_heatmap_ins ON activities;
        DROP TRIGGER IF EXISTS activities_my_heatmap_del ON activities;
        DROP TRIGGER IF EXISTS activities_my_heatmap_upd ON activities;
        DROP FUNCTION IF EXISTS my_heatmap_bump_ins();
        DROP FUNCTION IF EXISTS my_heatmap_bump_del();
        DROP FUNCTION IF EXISTS my_heatmap_bump_upd();
        """
    )
    op.drop_table("my_heatmap_files")
