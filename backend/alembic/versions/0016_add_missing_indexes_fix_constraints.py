"""Add missing FK indexes, fix HeatCell unique constraint, NOT NULL on activity_cells.activity_id.

Addresses data engineering audit findings:
- CRITICAL: Remove column-level unique on heat_cells.cell_key (conflicts with composite unique)
- CRITICAL: Add index on activity_cells.activity_id + NOT NULL
- HIGH: Add indexes on all unindexed FK columns

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-13 00:00:00.000000
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fix HeatCell: column-level unique on cell_key was only in the ORM model,
    # never created in DB (composite unique on cell_key+sport exists instead).
    # Model already fixed — no DDL needed.

    # ── activity_cells.activity_id: NOT NULL + index ──
    op.alter_column("activity_cells", "activity_id", nullable=False)
    op.create_index("ix_activity_cells_activity_id", "activity_cells", ["activity_id"])

    # ── route_versions FK indexes ──
    # ix_route_versions_route_id already exists from initial migration
    op.create_index("ix_route_versions_author_id", "route_versions", ["author_id"])

    # ── suggestions FK indexes ──
    op.create_index("ix_suggestions_route_id", "suggestions", ["route_id"])
    op.create_index("ix_suggestions_author_id", "suggestions", ["author_id"])

    # ── upstream_prs FK indexes ──
    op.create_index("ix_upstream_prs_fork_route_id", "upstream_prs", ["fork_route_id"])
    op.create_index("ix_upstream_prs_parent_route_id", "upstream_prs", ["parent_route_id"])
    op.create_index("ix_upstream_prs_author_id", "upstream_prs", ["author_id"])
    op.create_index("ix_upstream_prs_proposed_version_id", "upstream_prs", ["proposed_version_id"])

    # ── route_tags FK indexes ──
    op.create_index("ix_route_tags_version_id", "route_tags", ["version_id"])

    # ── trip_stages FK indexes ──
    op.create_index("ix_trip_stages_route_id", "trip_stages", ["route_id"])

    # ── trip_pois FK indexes ──
    op.create_index("ix_trip_pois_stage_id", "trip_pois", ["stage_id"])


def downgrade() -> None:
    op.drop_index("ix_trip_pois_stage_id", table_name="trip_pois")
    op.drop_index("ix_trip_stages_route_id", table_name="trip_stages")
    op.drop_index("ix_route_tags_version_id", table_name="route_tags")
    op.drop_index("ix_upstream_prs_proposed_version_id", table_name="upstream_prs")
    op.drop_index("ix_upstream_prs_author_id", table_name="upstream_prs")
    op.drop_index("ix_upstream_prs_parent_route_id", table_name="upstream_prs")
    op.drop_index("ix_upstream_prs_fork_route_id", table_name="upstream_prs")
    op.drop_index("ix_suggestions_author_id", table_name="suggestions")
    op.drop_index("ix_suggestions_route_id", table_name="suggestions")
    op.drop_index("ix_route_versions_author_id", table_name="route_versions")
    op.drop_index("ix_activity_cells_activity_id", table_name="activity_cells")
    op.alter_column("activity_cells", "activity_id", nullable=True)
