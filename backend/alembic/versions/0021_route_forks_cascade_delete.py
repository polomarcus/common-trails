"""Add CASCADE on delete to route_forks foreign keys.

Revision ID: 0021
Revises: 0020
Create Date: 2026-03-20 00:00:00.000000
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop existing FK constraints and recreate with ON DELETE CASCADE
    op.drop_constraint("route_forks_parent_route_id_fkey", "route_forks", type_="foreignkey")
    op.drop_constraint("route_forks_fork_route_id_fkey", "route_forks", type_="foreignkey")
    op.create_foreign_key(
        "route_forks_parent_route_id_fkey",
        "route_forks", "routes",
        ["parent_route_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "route_forks_fork_route_id_fkey",
        "route_forks", "routes",
        ["fork_route_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("route_forks_parent_route_id_fkey", "route_forks", type_="foreignkey")
    op.drop_constraint("route_forks_fork_route_id_fkey", "route_forks", type_="foreignkey")
    op.create_foreign_key(
        "route_forks_parent_route_id_fkey",
        "route_forks", "routes",
        ["parent_route_id"], ["id"],
    )
    op.create_foreign_key(
        "route_forks_fork_route_id_fkey",
        "route_forks", "routes",
        ["fork_route_id"], ["id"],
    )
