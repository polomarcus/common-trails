# OSM PBF Import — Operations Procedure

## Purpose

Pre-populate `osm_road_edges` with routable road segments from OpenStreetMap PBF extracts. This eliminates Overpass API calls during GPX ingestion for covered regions.

**Run once at initial deploy, then re-run quarterly (or when expanding coverage).**

## Prerequisites

- Cloud SQL instance running with migrations applied (`alembic upgrade head`)
- `pyosmium` + `libexpat1` installed in the backend container
- Sufficient disk for PBF download (~200MB per region, temporary)

## Regions

| Region | Geofabrik slug | Size | Segments | Status |
|--------|---------------|------|----------|--------|
| Occitanie | `occitanie` | ~200MB | ~3M | **Phase 1** |
| PACA | `paca` | ~150MB | ~2M | **Phase 1** |
| Auvergne-Rhone-Alpes | `rhone-alpes` | ~250MB | ~4M | Phase 2 |
| France (full) | `france` | ~4GB | ~40M | Phase 3 |
| Spain | `spain` | ~1.5GB | ~15M | Future |
| Italy | `italy` | ~1.8GB | ~18M | Future |

## Procedure

### 1. Connect to the backend container

```bash
# Cloud Run: use cloud-sql-proxy + exec into the job container
# Local: docker compose exec backend bash
```

### 2. Install libexpat (if not in Docker image)

```bash
apt-get update -qq && apt-get install -y -qq libexpat1
```

### 3. Run import for each region

```bash
# Phase 1: South of France
python -m app.cli.import_osm_roads occitanie
python -m app.cli.import_osm_roads paca

# Phase 2: Expand coverage
python -m app.cli.import_osm_roads rhone-alpes

# Phase 3: Full France (replaces regional imports)
python -m app.cli.import_osm_roads france
```

The CLI automatically downloads the PBF from Geofabrik, parses it, and inserts segments in batches. Each region takes 1-5 minutes depending on size.

**To skip download** (if PBF already on disk):
```bash
python -m app.cli.import_osm_roads /data/languedoc-roussillon-latest.osm.pbf --no-download
```

### 4. Verify

```bash
# Check row count and tile coverage
python -c "
from app.db.session import SessionLocal
from sqlalchemy import text
db = SessionLocal()
count = db.execute(text('SELECT COUNT(*) FROM osm_road_edges')).scalar()
tiles = db.execute(text('SELECT COUNT(DISTINCT tile_key) FROM osm_road_edges')).scalar()
size = db.execute(text(\"SELECT pg_size_pretty(pg_total_relation_size('osm_road_edges'))\")).scalar()
print(f'Segments: {count:,} | Tiles: {tiles:,} | Size: {size}')
db.close()
"
```

Expected values (Phase 1 — Occitanie + PACA):
- Segments: ~5M
- Tiles: ~2,000
- Size: ~1.2 GB

### 5. Test routing

The single-route `GET /routing` was removed late May 2026 (broken at scale).
Use `/routing/proposals` to smoke-test the server-side cascade post-import:

```bash
curl -s -X POST "http://localhost:8787/routing/proposals" \
  -H "Content-Type: application/json" \
  -d '{"start_lon":3.876,"start_lat":43.611,"end_lon":3.90,"end_lat":43.63,"profile":"road"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); ps=d['proposals']; print(f'proposals={len(ps)} methods={[p[\"method\"] for p in ps]}')"
```

## When to re-run

- **Initial deploy**: mandatory
- **Quarterly**: refresh OSM data (new roads, surface updates)
- **Coverage expansion**: adding new regions
- **After `docker compose down -v`**: data is in the DB volume, nuking it requires re-import

## Notes

- Regions can overlap — the import is idempotent (clears existing tiles before inserting)
- `france` replaces `occitanie` + `paca` + all other French regions
- For Spain/Italy, add Geofabrik URLs to `REGIONS` dict in `import_osm_roads.py`
- In dev, use the bundled `data/montpellier-anduze.osm.pbf` (Git LFS) for fast local setup
- Production should **not** set `SKIP_OSM_FETCH=true` — Overpass handles gaps outside PBF coverage
