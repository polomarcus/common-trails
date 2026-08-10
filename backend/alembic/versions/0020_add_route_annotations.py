"""Add route_annotations table for POI annotations on routes.

Revision ID: 0020
Revises: 0019
Create Date: 2026-03-19 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_annotations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "route_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("routes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("author_id", sa.String(36), nullable=False, index=True),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("dist_m", sa.Float(), nullable=True),
        sa.Column("icon", sa.String(50), nullable=False, server_default="info"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("route_annotations")
