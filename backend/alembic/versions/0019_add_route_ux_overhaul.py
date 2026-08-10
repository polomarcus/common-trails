"""Route UX overhaul: soft delete, last_accessed_at, collections.

Add deleted_at + last_accessed_at to routes.
Create route_collections and route_collection_items tables.

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-17 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("routes", sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_routes_deleted_at", "routes", ["deleted_at"])

    op.create_table(
        "route_collections",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "route_collection_items",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "collection_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("route_collections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "route_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("routes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("collection_id", "route_id"),
    )


def downgrade() -> None:
    op.drop_table("route_collection_items")
    op.drop_table("route_collections")
    op.drop_index("ix_routes_deleted_at", table_name="routes")
    op.drop_column("routes", "last_accessed_at")
    op.drop_column("routes", "deleted_at")
