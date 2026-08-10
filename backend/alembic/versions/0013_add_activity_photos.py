"""Add activity_photos table and total_photo_count on activities.

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-13 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("total_photo_count", sa.Integer(), server_default="0", nullable=True))

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


def downgrade() -> None:
    op.drop_index("ix_activity_photos_user_id", table_name="activity_photos")
    op.drop_index("ix_activity_photos_activity_id", table_name="activity_photos")
    op.drop_table("activity_photos")
    op.drop_column("activities", "total_photo_count")
