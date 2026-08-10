"""Partition heat_edges table by sport for faster scans and parallel rebuilds.

With 1000 users, heat_edges grows to 5-10M rows. Partitioning by sport:
- Queries with WHERE sport = X only scan 1 partition (not the full table)
- GIST indexes are smaller per partition
- Rebuilds can run per-sport in parallel
- VACUUM/ANALYZE per partition, not full table

Revision ID: 0029
Revises: 0028
Create Date: 2026-03-29 00:00:00.000000
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

SPORTS = ["road", "gravel", "mtb", "running"]


def upgrade() -> None:
    # Check if already partitioned (idempotent)
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        result = db.execute(sa_text(
            "SELECT partrelid FROM pg_partitioned_table "
            "WHERE partrelid = 'heat_edges'::regclass"
        )).fetchone()
        if result:
            return  # already partitioned
    except Exception:
        pass  # table doesn't exist as partitioned, proceed
    finally:
        db.close()

    # 1. Create partitioned table with same schema
    op.execute("""
        CREATE TABLE heat_edges_partitioned (
            id BIGSERIAL,
            edge_key TEXT NOT NULL,
            sport TEXT NOT NULL,
            user_count INTEGER DEFAULT 0,
            pass_count INTEGER DEFAULT 0,
            forward_count INTEGER DEFAULT 0,
            backward_count INTEGER DEFAULT 0,
            geometry geometry(LineString, 4326) NOT NULL,
            ele_delta_m DOUBLE PRECISION DEFAULT 0,
            slope_grade DOUBLE PRECISION DEFAULT 0,
            surface_type TEXT DEFAULT 'unknown',
            highway_type TEXT DEFAULT 'unknown',
            tracktype TEXT,
            smoothness TEXT,
            trail_network BOOLEAN DEFAULT false,
            trail_type TEXT,
            UNIQUE (edge_key, sport)
        ) PARTITION BY LIST (sport)
    """)

    # 2. Create partitions for known sports + a default partition
    for sport in SPORTS:
        op.execute(f"""
            CREATE TABLE heat_edges_{sport}
            PARTITION OF heat_edges_partitioned
            FOR VALUES IN ('{sport}')
        """)
    op.execute("""
        CREATE TABLE heat_edges_default
        PARTITION OF heat_edges_partitioned
        DEFAULT
    """)

    # 3. Copy data from old table
    op.execute("""
        INSERT INTO heat_edges_partitioned
            (id, edge_key, sport, user_count, pass_count, forward_count, backward_count,
             geometry, ele_delta_m, slope_grade, surface_type, highway_type,
             tracktype, smoothness, trail_network, trail_type)
        SELECT
            id, edge_key, sport, user_count, pass_count, forward_count, backward_count,
            geometry, ele_delta_m, slope_grade, surface_type, highway_type,
            tracktype, smoothness, trail_network, trail_type
        FROM heat_edges
    """)

    # 4. Update the sequence to continue from max id
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('heat_edges_partitioned', 'id'),
            COALESCE((SELECT MAX(id) FROM heat_edges_partitioned), 0) + 1,
            false
        )
    """)

    # 5. Drop old table first (cascades old indexes), then rename new
    op.execute("DROP TABLE heat_edges CASCADE")
    op.execute("ALTER TABLE heat_edges_partitioned RENAME TO heat_edges")

    # 6. Recreate indexes on the partitioned table
    op.execute("""
        CREATE INDEX ix_heat_edges_geometry ON heat_edges USING GIST (geometry)
    """)
    op.execute("""
        CREATE INDEX ix_heat_edges_sport_usercount ON heat_edges (sport, user_count)
        WHERE user_count >= 1
    """)


def downgrade() -> None:
    # Reverse: create non-partitioned table, copy data back
    op.execute("""
        CREATE TABLE heat_edges_unpartitioned (
            id BIGSERIAL PRIMARY KEY,
            edge_key TEXT NOT NULL UNIQUE,
            sport TEXT NOT NULL,
            user_count INTEGER DEFAULT 0,
            pass_count INTEGER DEFAULT 0,
            forward_count INTEGER DEFAULT 0,
            backward_count INTEGER DEFAULT 0,
            geometry geometry(LineString, 4326) NOT NULL,
            ele_delta_m DOUBLE PRECISION DEFAULT 0,
            slope_grade DOUBLE PRECISION DEFAULT 0,
            surface_type TEXT DEFAULT 'unknown',
            highway_type TEXT DEFAULT 'unknown',
            tracktype TEXT,
            smoothness TEXT,
            trail_network BOOLEAN DEFAULT false,
            trail_type TEXT
        )
    """)
    op.execute("""
        INSERT INTO heat_edges_unpartitioned
            (id, edge_key, sport, user_count, pass_count, forward_count, backward_count,
             geometry, ele_delta_m, slope_grade, surface_type, highway_type,
             tracktype, smoothness, trail_network, trail_type)
        SELECT
            id, edge_key, sport, user_count, pass_count, forward_count, backward_count,
            geometry, ele_delta_m, slope_grade, surface_type, highway_type,
            tracktype, smoothness, trail_network, trail_type
        FROM heat_edges
    """)
    op.execute("DROP TABLE heat_edges CASCADE")
    op.execute("ALTER TABLE heat_edges_unpartitioned RENAME TO heat_edges")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_geometry ON heat_edges USING GIST (geometry)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_sport_usercount ON heat_edges (sport, user_count)
        WHERE user_count >= 1
    """)
