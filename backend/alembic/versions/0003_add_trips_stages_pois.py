"""Add trips, trip_stages, trip_pois tables for bikepacking trip collections.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-04 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True, unique=True, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("cover_image_url", sa.String(1024), nullable=True),
        sa.Column(
            "sport",
            sa.Enum("road", "gravel", "mtb", "offroad", name="sport_enum", create_type=False),
            server_default="road",
        ),
        sa.Column(
            "visibility",
            sa.Enum("public", "unlisted", "private", name="visibility_enum", create_type=False),
            server_default="private",
        ),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("region", sa.String(255), nullable=True),
        sa.Column("tags_json", sa.Text, nullable=True),
        sa.Column("total_distance_m", sa.Float, nullable=True),
        sa.Column("total_dplus_m", sa.Float, nullable=True),
        sa.Column("forked_from_id", sa.UUID(as_uuid=False), sa.ForeignKey("trips.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "trip_stages",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "trip_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "route_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("routes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("day_index", sa.Integer, server_default="0"),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("estimated_time_min", sa.Integer, nullable=True),
        sa.Column("lodging_type", sa.String(50), nullable=True),
        sa.Column("is_rest_day", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "trip_pois",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "trip_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "stage_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("trip_stages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(50), server_default="custom"),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("trip_pois")
    op.drop_table("trip_stages")
    op.drop_table("trips")
