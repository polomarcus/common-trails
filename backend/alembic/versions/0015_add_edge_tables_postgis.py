"""Move edge stores from in-memory Python dicts to PostGIS tables.

Creates: heat_edges, heat_edge_contributors, heat_cell_contributors,
         dfci_edges, trail_edges
(heat_cells already exists from 0001; we add the sport column to its unique constraint)

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-13 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── heat_edges ────────────────────────────────────────────────────────
    op.create_table(
        "heat_edges",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("edge_key", sa.Text, nullable=False, unique=True),
        sa.Column("sport", sa.Text, nullable=False),
        sa.Column("user_count", sa.Integer, server_default="0"),
        sa.Column("pass_count", sa.Integer, server_default="0"),
        sa.Column("forward_count", sa.Integer, server_default="0"),
        sa.Column("backward_count", sa.Integer, server_default="0"),
        sa.Column("ele_delta_m", sa.Float, server_default="0.0"),
        sa.Column("slope_grade", sa.Float, server_default="0.0"),
        sa.Column("surface_type", sa.Text, server_default="unknown"),
        sa.Column("highway_type", sa.Text, server_default="unknown"),
        sa.Column("tracktype", sa.Text, nullable=True),
        sa.Column("smoothness", sa.Text, nullable=True),
        sa.Column("trail_network", sa.Boolean, server_default="false"),
        sa.Column("trail_type", sa.Text, nullable=True),
    )
    op.execute(
        "SELECT AddGeometryColumn('heat_edges', 'geometry', 4326, 'LINESTRING', 2)"
    )
    op.execute("ALTER TABLE heat_edges ALTER COLUMN geometry SET NOT NULL")
    op.execute(
        "CREATE INDEX ix_heat_edges_geometry ON heat_edges USING GIST(geometry)"
    )
    op.create_index("ix_heat_edges_sport", "heat_edges", ["sport"])

    # ── heat_edge_contributors (K-anonymity dedup) ────────────────────────
    op.create_table(
        "heat_edge_contributors",
        sa.Column("edge_key", sa.Text, nullable=False),
        sa.Column("user_id_hash", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("edge_key", "user_id_hash"),
    )

    # ── heat_cell_contributors ────────────────────────────────────────────
    op.create_table(
        "heat_cell_contributors",
        sa.Column("cell_key", sa.Text, nullable=False),
        sa.Column("sport", sa.Text, nullable=False),
        sa.Column("user_id_hash", sa.BigInteger, nullable=False),
        sa.PrimaryKeyConstraint("cell_key", "sport", "user_id_hash"),
    )

    # ── dfci_edges ────────────────────────────────────────────────────────
    op.create_table(
        "dfci_edges",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ref", sa.Text, nullable=True),
        sa.Column("surface", sa.Text, server_default="unknown"),
        sa.Column("highway", sa.Text, server_default="track"),
        sa.Column("trail_type", sa.Text, server_default="DFCI"),
    )
    op.execute(
        "SELECT AddGeometryColumn('dfci_edges', 'geometry', 4326, 'LINESTRING', 2)"
    )
    op.execute("ALTER TABLE dfci_edges ALTER COLUMN geometry SET NOT NULL")
    op.execute(
        "CREATE INDEX ix_dfci_edges_geometry ON dfci_edges USING GIST(geometry)"
    )

    # ── trail_edges ───────────────────────────────────────────────────────
    op.create_table(
        "trail_edges",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ref", sa.Text, nullable=True),
        sa.Column("surface", sa.Text, server_default="unknown"),
        sa.Column("highway", sa.Text, server_default="path"),
        sa.Column("trail_type", sa.Text, nullable=False),
    )
    op.execute(
        "SELECT AddGeometryColumn('trail_edges', 'geometry', 4326, 'LINESTRING', 2)"
    )
    op.execute("ALTER TABLE trail_edges ALTER COLUMN geometry SET NOT NULL")
    op.execute(
        "CREATE INDEX ix_trail_edges_geometry ON trail_edges USING GIST(geometry)"
    )
    op.create_index("ix_trail_edges_trail_type", "trail_edges", ["trail_type"])


def downgrade() -> None:
    op.drop_table("trail_edges")
    op.drop_table("dfci_edges")
    op.drop_table("heat_cell_contributors")
    op.drop_table("heat_edge_contributors")
    op.drop_table("heat_edges")
