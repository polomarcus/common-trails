"""Initial schema: GitHub-like routes + integrations + heatmap.

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ── routes ──────────────────────────────────────────────────────────────
    sport_enum = postgresql.ENUM("road", "gravel", "mtb", "offroad", name="sport_enum", create_type=True)
    visibility_enum = postgresql.ENUM("public", "unlisted", "private", name="visibility_enum", create_type=True)
    op.create_table(
        "routes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("sport", sport_enum, nullable=False, server_default="road"),
        sa.Column("visibility", visibility_enum, nullable=False, server_default="public"),
        sa.Column("forked_from_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── route_versions ───────────────────────────────────────────────────────
    op.create_table(
        "route_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("geometry_geojson", sa.Text, nullable=True),
        sa.Column("distance_m", sa.Float, nullable=True),
        sa.Column("elevation_gain_m", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_route_versions_route_id", "route_versions", ["route_id"])

    # ── route_forks ──────────────────────────────────────────────────────────
    op.create_table(
        "route_forks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("parent_route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("fork_route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("forked_by_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("parent_route_id", "fork_route_id"),
    )

    # ── suggestions ──────────────────────────────────────────────────────────
    op.create_table(
        "suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("geometry_geojson", sa.Text, nullable=True),
        sa.Column("status", postgresql.ENUM("open", "merged", "rejected", name="suggestion_status_enum", create_type=True), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── upstream_prs ─────────────────────────────────────────────────────────
    op.create_table(
        "upstream_prs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fork_route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("parent_route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("proposed_version_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("route_versions.id"), nullable=True),
        sa.Column("status", postgresql.ENUM("open", "merged", "rejected", name="upstream_pr_status_enum", create_type=True), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── route_tags ───────────────────────────────────────────────────────────
    op.create_table(
        "route_tags",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("route_versions.id"), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("route_id", "tag"),
    )

    # ── integration_accounts (PRIVATE) ───────────────────────────────────────
    op.create_table(
        "integration_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=True),
        sa.Column("expires_at", sa.BigInteger, nullable=True),
        sa.Column("external_user_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider"),
    )

    # ── import_jobs (PRIVATE) ─────────────────────────────────────────────────
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("status", postgresql.ENUM("PENDING", "RUNNING", "COMPLETED", "FAILED", name="import_job_status_enum", create_type=True), nullable=False, server_default="PENDING"),
        sa.Column("cursor", sa.String(255), nullable=True),
        sa.Column("imported_count", sa.Integer, server_default="0"),
        sa.Column("failed_count", sa.Integer, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── activities (PRIVATE) ──────────────────────────────────────────────────
    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_activity_id", sa.String(100), nullable=True),
        sa.Column("sport", sa.String(50), nullable=False, server_default="road"),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("geometry_geojson", sa.Text, nullable=True),
        sa.Column("distance_m", sa.Float, nullable=True),
        sa.Column("elevation_gain_m", sa.Float, nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("contribute_heatmap", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", "provider_activity_id"),
    )

    # ── activity_cells (PRIVATE) ──────────────────────────────────────────────
    op.create_table(
        "activity_cells",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("activity_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("activities.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("cell_key", sa.String(32), nullable=False, index=True),
        sa.Column("zoom", sa.Integer, server_default="14"),
    )

    # ── heat_cells (COMMON — ODbL) ────────────────────────────────────────────
    op.create_table(
        "heat_cells",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cell_key", sa.String(32), nullable=False, index=True),
        sa.Column("zoom", sa.Integer, nullable=False, server_default="14"),
        sa.Column("sport", sa.String(50), nullable=False, server_default="road"),
        sa.Column("user_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pass_count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("cell_key", "sport"),
    )

    # ── edge_popularity (COMMON — ODbL) ──────────────────────────────────────
    op.create_table(
        "edge_popularity",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("edge_id", sa.BigInteger, nullable=False, unique=True, index=True),
        sa.Column("sport", sa.String(50), nullable=False, server_default="road"),
        sa.Column("popularity", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("edge_popularity")
    op.drop_table("heat_cells")
    op.drop_table("activity_cells")
    op.drop_table("activities")
    op.drop_table("import_jobs")
    op.drop_table("integration_accounts")
    op.drop_table("route_tags")
    op.drop_table("upstream_prs")
    op.drop_table("suggestions")
    op.drop_table("route_forks")
    op.drop_table("route_versions")
    op.drop_table("routes")

    op.execute("DROP TYPE IF EXISTS import_job_status_enum")
    op.execute("DROP TYPE IF EXISTS upstream_pr_status_enum")
    op.execute("DROP TYPE IF EXISTS suggestion_status_enum")
    op.execute("DROP TYPE IF EXISTS visibility_enum")
    op.execute("DROP TYPE IF EXISTS sport_enum")
