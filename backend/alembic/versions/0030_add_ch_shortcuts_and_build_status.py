"""Add Contraction Hierarchies tables.

ch_shortcuts: precomputed shortcut edges for fast long-distance routing.
ch_build_status: tracks build state per sport for lazy rebuild and backoff.

Revision ID: 0030
Revises: 0029
Create Date: 2026-03-31 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ch_shortcuts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("sport", sa.Text, nullable=False, index=True),
        sa.Column("source_lon", sa.Float, nullable=False),
        sa.Column("source_lat", sa.Float, nullable=False),
        sa.Column("target_lon", sa.Float, nullable=False),
        sa.Column("target_lat", sa.Float, nullable=False),
        sa.Column("via_lon", sa.Float, nullable=False),
        sa.Column("via_lat", sa.Float, nullable=False),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("ch_level", sa.Integer, nullable=False),
        sa.Column("profile_hash", sa.Text, nullable=False),
        sa.Column("inline_coords", sa.Text, nullable=True),
        sa.Column("cumulative_ascent_m", sa.Float, server_default="0.0"),
    )
    # Composite indexes for tile bbox queries
    op.create_index(
        "ix_ch_shortcuts_sport_source",
        "ch_shortcuts",
        ["sport", "source_lon", "source_lat"],
    )
    op.create_index(
        "ix_ch_shortcuts_sport_target",
        "ch_shortcuts",
        ["sport", "target_lon", "target_lat"],
    )
    op.create_index(
        "ix_ch_shortcuts_sport_level",
        "ch_shortcuts",
        ["sport", "ch_level"],
    )

    op.create_table(
        "ch_build_status",
        sa.Column("sport", sa.Text, primary_key=True),
        sa.Column("ch_version", sa.Integer, server_default="0"),
        sa.Column("edge_version", sa.Integer, server_default="0"),
        sa.Column("profile_hash", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_count", sa.Integer, server_default="0"),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ch_build_status")
    op.drop_index("ix_ch_shortcuts_sport_level", table_name="ch_shortcuts")
    op.drop_index("ix_ch_shortcuts_sport_target", table_name="ch_shortcuts")
    op.drop_index("ix_ch_shortcuts_sport_source", table_name="ch_shortcuts")
    op.drop_table("ch_shortcuts")
