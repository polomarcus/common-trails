"""Drop the database leftovers of the retired Strava API sync.

The live Strava connection (OAuth, sync jobs, webhooks, photo strip) is gone
from the code; this removes what it left in the database:

* ``integration_accounts`` — OAuth tokens, athlete id, sync state;
* ``import_jobs`` (+ its ``import_job_status_enum`` type) — API import jobs;
* ``activity_photos`` — photos fetched from the Strava API;
* ``activities.total_photo_count`` — photo count reported by the Strava API.

KEPT on purpose: ``activities.provider`` / ``source`` /
``provider_activity_id`` (+ their unique constraint) and
``activities.geometry_source`` — activities imported through the API still
exist and keep displaying.

``total_photo_count`` was one of the columns watched by the "Ma heatmap"
staleness trigger (0068/0069): the trigger function is re-created WITHOUT it
BEFORE the column is dropped, otherwise every UPDATE on ``activities`` would
fail on the missing column. Dropping the column does not bump ``data_rev``:
already-built per-user files stay valid (their summary simply carries one
extra key that nothing reads any more).

Every DROP is ``IF EXISTS`` — safe on a database where a table is already
missing. The downgrade re-creates the tables/column EMPTY, with the schema
they had at 0069 (data is not restorable).

Revision ID: 0070
Revises: 0069
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_DISPLAYED_COLUMNS_0069 = (
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
    "source",
)

# Same gate + body as 0068/0069 (inert until my_heatmap_config.enabled).
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
    # DROP COLUMN takes an ACCESS EXCLUSIVE lock on ``activities``. It is
    # instant once granted, but if it had to WAIT behind a long read, every
    # other query on the table would queue behind it. Give up after 5 s
    # instead: the container entrypoint retries the migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Trigger first: it must stop reading the column before the column goes.
    _replace_update_trigger_function(_DISPLAYED_COLUMNS)

    op.execute("DROP TABLE IF EXISTS activity_photos")
    op.execute("DROP TABLE IF EXISTS import_jobs")
    op.execute("DROP TYPE IF EXISTS import_job_status_enum")
    op.execute("DROP TABLE IF EXISTS integration_accounts")
    op.execute("ALTER TABLE activities DROP COLUMN IF EXISTS total_photo_count")


def downgrade() -> None:
    # Schema as of 0069 (0001 + 0009 + 0023 + 0048), empty.
    op.create_table(
        "integration_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("external_user_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("athlete_name", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contribute_heatmap", sa.Boolean(), server_default="false", nullable=False),
        sa.UniqueConstraint("user_id", "provider"),
    )
    op.create_index("ix_integration_accounts_user_id", "integration_accounts", ["user_id"])

    # Schema as of 0069 (0001 + 0007 + 0008 + 0045 + 0051), empty.
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "RUNNING", "COMPLETED", "FAILED",
                name="import_job_status_enum", create_type=True,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("imported_count", sa.Integer(), server_default="0"),
        sa.Column("failed_count", sa.Integer(), server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("total_count", sa.Integer(), server_default="0"),
        sa.Column("skipped_count", sa.Integer(), server_default="0"),
        sa.Column("gps_upgraded_count", sa.Integer(), server_default="0"),
        sa.Column("gps_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_import_jobs_user_id", "import_jobs", ["user_id"])

    # Schema as of 0013.
    op.add_column(
        "activities",
        sa.Column("total_photo_count", sa.Integer(), server_default="0", nullable=True),
    )
    op.create_table(
        "activity_photos",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("activity_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("strava_photo_id", sa.String(100), nullable=True),
        sa.Column("url_thumb", sa.String(1024), nullable=False),
        sa.Column("url_medium", sa.String(1024), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("activity_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "strava_photo_id"),
    )
    op.create_index("ix_activity_photos_activity_id", "activity_photos", ["activity_id"])
    op.create_index("ix_activity_photos_user_id", "activity_photos", ["user_id"])

    # Column is back: the trigger watches it again (as in 0069).
    _replace_update_trigger_function(_DISPLAYED_COLUMNS_0069)
