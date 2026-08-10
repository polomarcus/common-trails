"""Drop deprecated upstream_prs table.

The upstream-PR endpoints (POST /routes/{id}/upstream-prs, GET ...,
POST /upstream-prs/{pr_id}/{merge,reject}) were marked DEPRECATED for
several releases. They were never wired into the frontend (the
RoutePRsSection component existed but was never imported — see comment
in frontend/app/map/page.tsx).

The variant UX is now the only "GitHub-like" surface: fork = own route
with forked_from_id pointer; no merge/PR layer on top.

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-16
"""
import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS upstream_prs CASCADE")
    op.execute("DROP TYPE IF EXISTS upstream_pr_status_enum")


def downgrade() -> None:
    # Placeholder: re-create an empty table with the original shape so
    # the migration is reversible. Indexes from 0016 are NOT restored —
    # they only mattered when the table had rows.
    op.execute(
        "CREATE TYPE upstream_pr_status_enum AS ENUM ('open', 'merged', 'rejected')"
    )
    op.create_table(
        "upstream_prs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("fork_route_id", sa.dialects.postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("parent_route_id", sa.dialects.postgresql.UUID(as_uuid=False), sa.ForeignKey("routes.id"), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("proposed_version_id", sa.dialects.postgresql.UUID(as_uuid=False), sa.ForeignKey("route_versions.id"), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "merged", "rejected", name="upstream_pr_status_enum", create_type=False),
            nullable=False,
            server_default="open",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
