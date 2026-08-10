"""Add collection sharing: visibility, position, collection-level annotations.

Revision ID: 0026
Revises: 0025
Create Date: 2026-03-25 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # RouteCollection: add visibility column (reuse existing visibility_enum)
    op.add_column(
        "route_collections",
        sa.Column(
            "visibility",
            sa.Enum("public", "unlisted", "private", name="visibility_enum", create_type=False),
            server_default="private",
            nullable=False,
        ),
    )

    # RouteCollectionItem: add position column
    op.add_column(
        "route_collection_items",
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
    )

    # RouteAnnotation: add collection_id column
    op.add_column(
        "route_annotations",
        sa.Column(
            "collection_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("route_collections.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_route_annotations_collection_id", "route_annotations", ["collection_id"])

    # RouteAnnotation: make route_id nullable
    op.alter_column("route_annotations", "route_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=False), nullable=True)

    # RouteAnnotation: add CHECK constraint (at least one parent)
    op.create_check_constraint(
        "ck_annotation_has_parent",
        "route_annotations",
        "route_id IS NOT NULL OR collection_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_annotation_has_parent", "route_annotations", type_="check")
    # Delete collection-only annotations (route_id IS NULL) before making route_id NOT NULL
    op.execute("DELETE FROM route_annotations WHERE route_id IS NULL")
    op.alter_column("route_annotations", "route_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=False), nullable=False)
    op.drop_index("ix_route_annotations_collection_id", "route_annotations")
    op.drop_column("route_annotations", "collection_id")
    op.drop_column("route_collection_items", "position")
    op.drop_column("route_collections", "visibility")
