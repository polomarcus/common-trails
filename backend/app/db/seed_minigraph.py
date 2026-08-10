"""Seed a minimal road graph for CI/test pgRouting.

Creates a small set of vertices and edges around Lyon, France,
so that /routing works in CI without external data.

Usage:
    python -m app.db.seed_minigraph
    or import and call seed_minigraph(db_url)
"""
import os

SEED_SQL = """
-- Create pgRouting topology tables if they don't exist
CREATE TABLE IF NOT EXISTS minigraph_edges (
    id          BIGSERIAL PRIMARY KEY,
    source      BIGINT,
    target      BIGINT,
    cost        FLOAT DEFAULT 1.0,
    reverse_cost FLOAT DEFAULT 1.0,
    sport       TEXT DEFAULT 'road',
    popularity  FLOAT DEFAULT 0.0,
    highway_type TEXT DEFAULT 'path',
    slope_grade  FLOAT DEFAULT 0.0,
    surface      TEXT DEFAULT 'unknown',
    tracktype    TEXT DEFAULT NULL,
    smoothness   TEXT DEFAULT NULL,
    trail_network BOOLEAN DEFAULT FALSE,
    trail_type  TEXT DEFAULT NULL,
    the_geom    GEOMETRY(LINESTRING, 4326)
);

CREATE TABLE IF NOT EXISTS minigraph_vertices (
    id    BIGSERIAL PRIMARY KEY,
    lon   FLOAT,
    lat   FLOAT,
    the_geom GEOMETRY(POINT, 4326)
);

-- Clear existing seed data
TRUNCATE minigraph_vertices, minigraph_edges RESTART IDENTITY CASCADE;

-- Insert vertices (Lyon area: ~45.75N, 4.83E)
INSERT INTO minigraph_vertices (lon, lat, the_geom) VALUES
    (4.8300, 45.7500, ST_SetSRID(ST_MakePoint(4.8300, 45.7500), 4326)),  -- 1
    (4.8350, 45.7520, ST_SetSRID(ST_MakePoint(4.8350, 45.7520), 4326)),  -- 2
    (4.8400, 45.7540, ST_SetSRID(ST_MakePoint(4.8400, 45.7540), 4326)),  -- 3
    (4.8450, 45.7560, ST_SetSRID(ST_MakePoint(4.8450, 45.7560), 4326)),  -- 4
    (4.8500, 45.7580, ST_SetSRID(ST_MakePoint(4.8500, 45.7580), 4326)),  -- 5
    (4.8350, 45.7480, ST_SetSRID(ST_MakePoint(4.8350, 45.7480), 4326)),  -- 6
    (4.8400, 45.7500, ST_SetSRID(ST_MakePoint(4.8400, 45.7500), 4326)),  -- 7
    (4.8450, 45.7520, ST_SetSRID(ST_MakePoint(4.8450, 45.7520), 4326)),  -- 8
    -- Montpellier area (~43.61N, 3.87E) — near demo activities
    (3.8700, 43.6100, ST_SetSRID(ST_MakePoint(3.8700, 43.6100), 4326)),  -- 9
    (3.8780, 43.6240, ST_SetSRID(ST_MakePoint(3.8780, 43.6240), 4326)),  -- 10
    (3.8910, 43.6450, ST_SetSRID(ST_MakePoint(3.8910, 43.6450), 4326)),  -- 11
    (3.8700, 43.6800, ST_SetSRID(ST_MakePoint(3.8700, 43.6800), 4326)),  -- 12
    (3.8300, 43.6700, ST_SetSRID(ST_MakePoint(3.8300, 43.6700), 4326)),  -- 13
    (3.8060, 43.6890, ST_SetSRID(ST_MakePoint(3.8060, 43.6890), 4326)),  -- 14
    (3.8480, 43.5980, ST_SetSRID(ST_MakePoint(3.8480, 43.5980), 4326)),  -- 15
    (3.7900, 43.5600, ST_SetSRID(ST_MakePoint(3.7900, 43.5600), 4326)),  -- 16
    (3.8200, 43.6400, ST_SetSRID(ST_MakePoint(3.8200, 43.6400), 4326)),  -- 17
    -- Additional vertices along NE diagonal (test coordinate corridor 3.87→3.99, 43.60→43.72)
    (3.8750, 43.6050, ST_SetSRID(ST_MakePoint(3.8750, 43.6050), 4326)),  -- 18
    (3.9000, 43.6300, ST_SetSRID(ST_MakePoint(3.9000, 43.6300), 4326)),  -- 19
    (3.9300, 43.6600, ST_SetSRID(ST_MakePoint(3.9300, 43.6600), 4326)),  -- 20
    (3.9600, 43.6900, ST_SetSRID(ST_MakePoint(3.9600, 43.6900), 4326));  -- 21

-- Insert edges (bidirectional connections)
-- Diversified OSM tags exercise all surface classification rules:
--   1→2: explicit asphalt + good smoothness → asphalt
--   2→3: explicit asphalt, no smoothness → asphalt
--   3→4: secondary, no surface → asphalt (inferred from highway)
--   4→5: residential, no surface → asphalt (inferred from highway)
--   1→6: track + grade2 → gravel (tracktype overrides highway)
--   6→7: track + grade3 → gravel
--   7→8: path + dirt + grade4 + bad → dirt
--   8→5: path + grade5 + horrible → dirt (smoothness downgrades)
--   2→6: cycleway + excellent smoothness → asphalt
--   3→7: track + cobblestone → rock (explicit surface overrides highway)
INSERT INTO minigraph_edges (source, target, cost, reverse_cost, sport, popularity, highway_type, slope_grade, surface, tracktype, smoothness, trail_network, trail_type, the_geom) VALUES
    (1, 2, 1.0, 1.0, 'road', 10.0, 'secondary', 2.0, 'asphalt', NULL,    'good',      FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8300, 45.7500), ST_MakePoint(4.8350, 45.7520))),
    (2, 3, 1.0, 1.0, 'road', 8.0,  'secondary', 3.0, 'asphalt', NULL,    NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8350, 45.7520), ST_MakePoint(4.8400, 45.7540))),
    (3, 4, 1.2, 1.2, 'road', 6.0,  'secondary', 4.0, NULL,      NULL,    NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8400, 45.7540), ST_MakePoint(4.8450, 45.7560))),
    (4, 5, 1.0, 1.0, 'road', 5.0,  'residential', 2.5, NULL,    NULL,    NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8450, 45.7560), ST_MakePoint(4.8500, 45.7580))),
    (1, 6, 0.8, 0.8, 'gravel', 4.0, 'track',   5.0, NULL,      'grade2', NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8300, 45.7500), ST_MakePoint(4.8350, 45.7480))),
    (6, 7, 0.9, 0.9, 'gravel', 3.0, 'track',   6.0, NULL,      'grade3', NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8350, 45.7480), ST_MakePoint(4.8400, 45.7500))),
    (7, 8, 1.1, 1.1, 'mtb',    2.0, 'path',    7.0, 'dirt',    'grade4', 'bad',        TRUE,  'PR', ST_MakeLine(ST_MakePoint(4.8400, 45.7500), ST_MakePoint(4.8450, 45.7520))),
    (8, 5, 1.3, 1.3, 'mtb',    1.0, 'path',    8.0, NULL,      'grade5', 'horrible',   TRUE,  'PR', ST_MakeLine(ST_MakePoint(4.8450, 45.7520), ST_MakePoint(4.8500, 45.7580))),
    (2, 6, 0.5, 0.5, 'road',   7.0, 'cycleway', 3.5, NULL,     NULL,    'excellent',   FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8350, 45.7520), ST_MakePoint(4.8350, 45.7480))),
    (3, 7, 0.5, 0.5, 'road',   6.0, 'track',   4.0, 'cobblestone', NULL, NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(4.8400, 45.7540), ST_MakePoint(4.8400, 45.7500))),
    -- Montpellier-area edges — diverse OSM tags for demo activity enrichment:
    --   9→10: secondary + asphalt → asphalt (road climb from Montpellier)
    --  10→11: secondary + asphalt → asphalt (road toward Pic Saint-Loup)
    --  11→12: track + grade2 → gravel (garrigue tracks)
    --  12→13: path + dirt + grade4 → dirt (Les Matelles MTB trails)
    --  13→14: path + grade5 + horrible → dirt (off-road section)
    --   9→15: tertiary → asphalt (inferred, road south of Montpellier)
    --  15→16: track + compacted + grade3 → gravel (garrigues gravel)
    --  13→17: track + grade2 + good → gravel (link between trails)
    (9, 10, 1.0, 1.0, 'road',   8.0, 'secondary', 3.0, 'asphalt', NULL,    NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8700, 43.6100), ST_MakePoint(3.8780, 43.6240))),
    (10, 11, 1.0, 1.0, 'road',  7.0, 'secondary', 4.0, 'asphalt', NULL,    'good',       FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8780, 43.6240), ST_MakePoint(3.8910, 43.6450))),
    (11, 12, 1.2, 1.2, 'gravel', 5.0, 'track',    5.0, NULL,      'grade2', NULL,         FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8910, 43.6450), ST_MakePoint(3.8700, 43.6800))),
    (12, 13, 1.1, 1.1, 'mtb',   3.0, 'path',      7.0, 'dirt',    'grade4', 'bad',        TRUE,  'PR', ST_MakeLine(ST_MakePoint(3.8700, 43.6800), ST_MakePoint(3.8300, 43.6700))),
    (13, 14, 1.3, 1.3, 'mtb',   2.0, 'path',      8.0, NULL,      'grade5', 'horrible',   TRUE,  'GR', ST_MakeLine(ST_MakePoint(3.8300, 43.6700), ST_MakePoint(3.8060, 43.6890))),
    (9, 15, 0.8, 0.8, 'road',   6.0, 'tertiary',  2.0, NULL,      NULL,    NULL,          FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8700, 43.6100), ST_MakePoint(3.8480, 43.5980))),
    (15, 16, 1.0, 1.0, 'gravel', 4.0, 'track',    4.0, 'compacted', 'grade3', NULL,        FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8480, 43.5980), ST_MakePoint(3.7900, 43.5600))),
    (13, 17, 0.7, 0.7, 'gravel', 5.0, 'track',    3.0, NULL,      'grade2', 'good',        FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8300, 43.6700), ST_MakePoint(3.8200, 43.6400))),
    --  NE diagonal corridor (long edges along test/demo coordinate line):
    --   9→18: secondary + asphalt (Montpellier center → NE)
    --  18→19: tertiary → asphalt (inferred, suburban road)
    --  19→20: track + gravel + grade2 (garrigue tracks NE)
    --  20→21: path + dirt + grade4 (off-road NE continuation)
    (9, 18, 0.9, 0.9, 'road',   7.0, 'secondary', 2.0, 'asphalt', NULL,    'good',        FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8700, 43.6100), ST_MakePoint(3.8750, 43.6050))),
    (18, 19, 0.8, 0.8, 'road',   6.0, 'tertiary',  2.5, NULL,      NULL,    NULL,          FALSE, NULL, ST_MakeLine(ST_MakePoint(3.8750, 43.6050), ST_MakePoint(3.9000, 43.6300))),
    (19, 20, 1.0, 1.0, 'gravel', 4.0, 'track',    4.0, 'gravel',  'grade2', NULL,          FALSE, NULL, ST_MakeLine(ST_MakePoint(3.9000, 43.6300), ST_MakePoint(3.9300, 43.6600))),
    (20, 21, 1.2, 1.2, 'mtb',   2.0, 'path',      6.0, 'dirt',    'grade4', 'bad',         TRUE,  'GR', ST_MakeLine(ST_MakePoint(3.9300, 43.6600), ST_MakePoint(3.9600, 43.6900)));
"""


def seed_minigraph(database_url: str | None = None) -> dict:
    """Seed the mini road graph into the database. Returns row counts."""
    url = database_url or os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5487/common_trails"
    )

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.connect() as conn:
            # Ensure PostGIS is enabled
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            conn.commit()

            for statement in SEED_SQL.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    conn.execute(text(stmt))
            conn.commit()

            vertex_count = conn.execute(text("SELECT COUNT(*) FROM minigraph_vertices")).scalar()
            edge_count = conn.execute(text("SELECT COUNT(*) FROM minigraph_edges")).scalar()

        return {"vertices": vertex_count, "edges": edge_count, "status": "seeded"}

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    result = seed_minigraph()
    print(result)
