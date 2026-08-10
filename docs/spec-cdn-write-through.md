# Spec: Write-Through CDN for Heatmap Data

## Problem

Cloud Run currently serves heatmap/graph responses by computing from DB, compressing, and returning. Even with in-memory caching, every cold start recomputes everything. With scale-to-zero, cold starts happen often.

## Goal

After an import, Cloud Run computes the heatmap once and writes the result to Cloud Storage. CDN serves the static file. Cloud Run never handles heatmap read requests.

## Architecture

```
┌─────────────┐      ┌─────────────┐      ┌──────────────────┐
│   Browser    │─────→│  Cloud CDN  │─────→│  Cloud Storage   │
│              │←─────│  (edge)     │←─────│  (static files)  │
└─────────────┘      └─────────────┘      └──────────────────┘
                                                    ↑
                                           writes on import
                                                    │
                                          ┌─────────────────┐
                                          │  Cloud Run API   │
                                          │  (compute only)  │
                                          └─────────────────┘
```

## What Gets Written to Cloud Storage

All files are versioned: written under `v{timestamp}/`, then a `manifest.json` is updated to point to the new version. The frontend reads the manifest to discover current file paths. Versioned paths are immutable — CDN caches them indefinitely.

| File | Path | Format | Size | Update frequency |
|------|------|--------|------|-----------------|
| **Manifest** | `/api-cache/manifest.json` | JSON | <1KB | On every publish |
| Heatmap summary | `/api-cache/v{N}/heatmap/summary.json` | JSON | <1KB | On import |
| Trails (per sport) | `/api-cache/v{N}/heatmap/trails/{sport}.json.gz` | gzip JSON | 100KB-5MB | On import |
| Trails (all sports) | `/api-cache/v{N}/heatmap/trails/all.json.gz` | gzip JSON | 500KB-10MB | On import |
| DFCI tracks | `/api-cache/v{N}/heatmap/dfci.json.gz` | gzip JSON | 200KB | On DFCI import (rare) |
| Full graph JSON | `/api-cache/v{N}/routing/graph/{sport}/full.json.gz` | gzip JSON | 1-5MB | On import |
| **Full graph Protobuf** | `/api-cache/v{N}/routing/graph/{sport}/full.pb` | CTGB binary | 500KB-3MB | On import |

The **protobuf format is preferred** by the frontend — 44% smaller than JSON+gzip, faster to parse (typed array view vs JSON.parse). The JSON version is kept as fallback for debugging and backwards compatibility.

**NOT cached in Storage** (dynamic, user-specific):
- `/routing` (waypoint-specific computation)
- `/me/*` (private user data)
- `/imports/*` (file upload)
- `/heatmap/stats?bbox=...` (too many bbox combinations — keep in-memory)
- `area.pb` (viewport-dependent, needs lat/lon/radius params — served by API)

## Implementation

### Step 1: Storage Bucket + CORS

Use the existing frontend bucket with `/api-cache/` prefix. CORS is mandatory — the frontend on `chemins-communs.fr` fetches from Storage.

```hcl
# Terraform: add CORS to existing frontend bucket
resource "google_storage_bucket" "frontend" {
  # ... existing config ...

  cors {
    origin          = ["https://chemins-communs.fr", "http://localhost:3787"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Encoding", "Cache-Control"]
    max_age_seconds = 86400
  }
}
```

### Step 2: Write Function

Add to `backend/app/services/cache_writer.py`:

```python
"""Write pre-computed API responses to Cloud Storage for CDN serving."""
import gzip
import json
import logging
import os
import time

log = logging.getLogger(__name__)

BUCKET_NAME = os.environ.get("FRONTEND_BUCKET", "common-trails-frontend")
CACHE_PREFIX = "api-cache"
HEATMAP_K_ANONYMITY = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

# Reusable GCS client (created once, not per-write)
_gcs_client = None


def _get_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage
        _gcs_client = storage.Client()
    return _gcs_client


def _write_gcs(path: str, data: bytes, content_type: str,
               content_encoding: str | None = None,
               max_age: int = 86400) -> bool:
    """Write bytes to Cloud Storage. Returns True on success."""
    try:
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(f"{CACHE_PREFIX}/{path}")
        blob.content_type = content_type
        if content_encoding:
            blob.content_encoding = content_encoding
        blob.cache_control = f"public, max-age={max_age}"
        blob.upload_from_string(data)
        log.info("Written %s (%d bytes)", path, len(data))
        return True
    except Exception as e:
        log.error("Failed to write %s: %s", path, e)
        return False


def publish_heatmap_cache() -> dict:
    """Compute and write all heatmap responses to Cloud Storage.

    Uses versioned paths (v{timestamp}) for atomic updates.
    Writes a manifest.json last — frontend reads manifest to discover files.
    Returns {written: int, failed: int, version: str}.

    This function takes 30-60s on large datasets (11 DB queries + gzip + GCS uploads).
    Should only be called from Cloud Run Jobs, not from the API request path.
    """
    from app.services.ingest import get_heatmap_summary
    from app.services.graph_builder import build_full_graph, build_trails_geojson, build_dfci_geojson

    version = f"v{int(time.time())}"
    written, failed = 0, 0
    t0 = time.time()

    def _write(path, data, ct, ce=None):
        nonlocal written, failed
        # Versioned files are immutable — cache for 7 days
        if _write_gcs(f"{version}/{path}", data, ct, ce, max_age=604800):
            written += 1
        else:
            failed += 1

    # Summary
    summary = get_heatmap_summary()
    _write("heatmap/summary.json", json.dumps(summary).encode(), "application/json")

    # Trails per sport + all
    for sport in ["road", "gravel", "mtb", "offroad", "running", None]:
        sport_key = sport or "all"
        geojson = build_trails_geojson(sport=sport, k=HEATMAP_K_ANONYMITY)
        gz = gzip.compress(geojson.encode(), compresslevel=6)
        _write(f"heatmap/trails/{sport_key}.json.gz", gz, "application/json", "gzip")

    # DFCI tracks
    dfci_geojson = build_dfci_geojson()
    if dfci_geojson:
        gz = gzip.compress(dfci_geojson.encode(), compresslevel=6)
        _write("heatmap/dfci.json.gz", gz, "application/json", "gzip")

    # Full graph per sport
    for sport in ["road", "gravel", "mtb", "offroad", "running"]:
        compressed = build_full_graph(sport)
        _write(f"routing/graph/{sport}/full.json.gz", compressed, "application/json", "gzip")

    elapsed = time.time() - t0

    # Write manifest LAST (atomic switch to new version)
    if failed == 0:
        manifest = {
            "version": version,
            "published_at": time.time(),
            "files": {
                "summary": f"{version}/heatmap/summary.json",
                "dfci": f"{version}/heatmap/dfci.json.gz",
                "trails": {s: f"{version}/heatmap/trails/{s}.json.gz"
                           for s in ["road", "gravel", "mtb", "offroad", "running", "all"]},
                "graph": {s: f"{version}/routing/graph/{s}/full.json.gz"
                          for s in ["road", "gravel", "mtb", "offroad", "running"]},
            },
        }
        # Manifest: short cache (60s) so frontend picks up new versions quickly
        _write_gcs("manifest.json", json.dumps(manifest).encode(),
                    "application/json", max_age=60)
        log.info("Cache published: version=%s, %d files in %.1fs", version, written, elapsed)

        # Cleanup old versions (keep 2 most recent to avoid CDN 404s)
        _cleanup_old_versions(keep_versions=2)
    else:
        log.error("Cache publish incomplete: %d written, %d failed — manifest NOT updated",
                  written, failed)
        # Alert: publish failed — manifest still points to previous version
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"CDN cache publish failed: {failed} files failed, manifest not updated",
                level="error",
            )
        except ImportError:
            pass

    return {"written": written, "failed": failed, "version": version, "elapsed_s": elapsed}


def _cleanup_old_versions(keep_versions: int = 2):
    """Delete old api-cache/v* prefixes, keeping the N most recent.

    Keeps multiple versions to avoid CDN 404s: edge nodes may still serve
    a manifest pointing to a previous version until their cache expires.
    """
    try:
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)

        # List all version prefixes
        versions = set()
        for blob in bucket.list_blobs(prefix=f"{CACHE_PREFIX}/v", delimiter="/"):
            pass  # delimiter listing populates prefixes
        for prefix in bucket.list_blobs(prefix=f"{CACHE_PREFIX}/v").prefixes:
            versions.add(prefix)

        # Sort by timestamp (v{timestamp}/ → extract timestamp)
        sorted_versions = sorted(versions, reverse=True)

        # Delete all but the N most recent
        for old_version in sorted_versions[keep_versions:]:
            for blob in bucket.list_blobs(prefix=old_version):
                blob.delete()
            log.info("Cleaned up old version: %s", old_version)
    except Exception as e:
        log.warning("Old version cleanup failed (non-critical): %s", e)


def get_rollback_versions() -> list[str]:
    """List available cache versions for manual rollback."""
    try:
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)
        versions = set()
        for blob in bucket.list_blobs(prefix=f"{CACHE_PREFIX}/v"):
            # Extract version from path: api-cache/v1234567890/...
            parts = blob.name.split("/")
            if len(parts) >= 2:
                versions.add(parts[1])
        return sorted(versions, reverse=True)
    except Exception:
        return []


def rollback_to_version(version: str) -> bool:
    """Point manifest.json to a previous version (manual recovery)."""
    try:
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)
        # Verify version exists
        test_blob = bucket.blob(f"{CACHE_PREFIX}/{version}/heatmap/summary.json")
        if not test_blob.exists():
            log.error("Rollback failed: version %s not found", version)
            return False
        # Read existing manifest to get file structure, update version
        manifest = {
            "version": version,
            "published_at": time.time(),
            "rollback": True,
            "files": {
                "summary": f"{version}/heatmap/summary.json",
                "dfci": f"{version}/heatmap/dfci.json.gz",
                "trails": {s: f"{version}/heatmap/trails/{s}.json.gz"
                           for s in ["road", "gravel", "mtb", "offroad", "running", "all"]},
                "graph": {s: f"{version}/routing/graph/{s}/full.json.gz"
                          for s in ["road", "gravel", "mtb", "offroad", "running"]},
            },
        }
        _write_gcs("manifest.json", json.dumps(manifest).encode(),
                    "application/json", max_age=60)
        log.info("Rolled back to version %s", version)
        return True
    except Exception as e:
        log.error("Rollback failed: %s", e)
        return False
```

### Step 3: Trigger After Import

Publish is triggered **only from Cloud Run Jobs** (rebuild, import), never from the API request path. This avoids debounce issues across multiple API instances and keeps the publish latency (30-60s) off the request path.

```python
# In app/jobs/rebuild_heatmap.py — after rebuild completes
if os.environ.get("PUBLISH_CACHE", "").lower() == "true":
    from app.services.cache_writer import publish_heatmap_cache
    result = publish_heatmap_cache()
    log.info("Cache publish: %s", result)

# In app/jobs/import_strava.py — after GPS upgrade phase completes
if os.environ.get("PUBLISH_CACHE", "").lower() == "true":
    from app.services.cache_writer import publish_heatmap_cache
    publish_heatmap_cache()
```

For single-activity GPX uploads (via API), trigger a Cloud Run Job execution instead of inline publish:

```python
# In imports.py — after ingest_activity()
if os.environ.get("PUBLISH_CACHE", "").lower() == "true":
    _trigger_cache_publish_job()


def _trigger_cache_publish_job():
    """Trigger the rebuild-heatmap job with TRUNCATE_FIRST=false to just re-publish.

    Uses a DB flag to debounce: only triggers if no publish ran in the last 5 minutes.
    """
    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text

    db = SessionLocal()
    try:
        # Check debounce flag
        row = db.execute(sa_text(
            "SELECT value FROM app_settings WHERE key = 'last_cache_publish'"
        )).fetchone()
        if row and time.time() - float(row[0]) < 300:
            return  # Published recently, skip

        # Update flag
        db.execute(sa_text(
            "INSERT INTO app_settings (key, value) VALUES ('last_cache_publish', :ts) "
            "ON CONFLICT (key) DO UPDATE SET value = :ts"
        ), {"ts": str(time.time())})
        db.commit()

        # Trigger Cloud Run Job (lightweight — just publish, no rebuild)
        import httpx
        # ... trigger common-trails-rebuild-heatmap-prod with TRUNCATE_FIRST=false
    finally:
        db.close()
```

This avoids the Cloud Tasks dependency and uses a simple DB-level debounce that works across all API instances.

### Step 4: Frontend Reads from CDN

Frontend fetches the manifest (tiny, cached 60s by CDN, refreshed with TTL in-memory), then reads versioned files (cached 7 days since paths are immutable).

```typescript
// frontend/lib/cdn-cache.ts
const CDN_BASE = process.env.NEXT_PUBLIC_CDN_URL || '';
const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

let _manifest: { version: string; files: Record<string, any> } | null = null;
let _manifestFetchedAt = 0;
const MANIFEST_TTL_MS = 60_000; // Re-fetch manifest every 60s

async function getManifest() {
    // Return cached if fresh
    if (_manifest && Date.now() - _manifestFetchedAt < MANIFEST_TTL_MS) {
        return _manifest;
    }
    if (!CDN_BASE) return null;
    try {
        const res = await fetch(`${CDN_BASE}/api-cache/manifest.json?t=${Math.floor(Date.now() / 60000)}`);
        if (res.ok) {
            _manifest = await res.json();
            _manifestFetchedAt = Date.now();
        }
    } catch (e) {
        console.warn('CDN manifest fetch failed, falling back to API:', e);
    }
    return _manifest;
}

export async function fetchTrails(sport: string): Promise<any> {
    const manifest = await getManifest();
    if (manifest?.files?.trails?.[sport]) {
        const url = `${CDN_BASE}/api-cache/${manifest.files.trails[sport]}`;
        try {
            const res = await fetch(url);
            if (res.ok) return res.json(); // browser auto-decompresses gzip
        } catch (e) {
            console.warn(`CDN fetch failed for ${sport}, falling back to API:`, e);
        }
    }
    // Fallback to API (same response shape — build_trails_geojson matches API output)
    return fetch(`${API_URL}/heatmap/trails?sport=${sport}`).then(r => r.json());
}

export async function fetchGraph(sport: string): Promise<any> {
    const manifest = await getManifest();
    if (manifest?.files?.graph?.[sport]) {
        const url = `${CDN_BASE}/api-cache/${manifest.files.graph[sport]}`;
        try {
            const res = await fetch(url);
            if (res.ok) return res.json();
        } catch (e) {
            console.warn(`CDN graph fetch failed for ${sport}, falling back to API:`, e);
        }
    }
    return fetch(`${API_URL}/routing/graph/${sport}/full.json`).then(r => r.json());
}
```

### Step 5: Cleanup Old Versions

Handled in `_cleanup_old_versions()` above. Keeps 2 most recent versions to avoid CDN edge 404s (edge nodes may still serve a manifest pointing to the previous version until their 60s cache expires).

### Step 6: Rollback

If a publish produces corrupt data, rollback to a previous version:

```bash
# List available versions
docker compose exec backend python3 -c "
from app.services.cache_writer import get_rollback_versions
print(get_rollback_versions())
"

# Rollback to a specific version
docker compose exec backend python3 -c "
from app.services.cache_writer import rollback_to_version
rollback_to_version('v1711700000')
"
```

Or via environment override (skip CDN, use API directly):
```bash
# Emergency: force all frontends to use API instead of CDN
# Set NEXT_PUBLIC_CDN_URL="" and redeploy frontend
```

## Service Layer Extraction

Graph and trails computation must be extracted from API modules to avoid circular imports. This is the most complex part of the implementation.

```
backend/app/services/graph_builder.py  (NEW)
├── build_full_graph(sport) → bytes (gzip JSON)
├── build_trails_geojson(sport, k) → str (GeoJSON)
├── build_dfci_geojson() → str (GeoJSON)
└── build_area_graph(sport, lon, lat, radius) → bytes (CTGB)
```

**What needs to be extracted from `graph_tiles.py`:**
- `_full_graph_cache` dict and version check logic
- `_related_sports` expansion (road→[road,gravel], etc.)
- DFCI/trail edge merging into the graph
- Gzip compression with configurable level
- Surface/highway/trail index encoding

**What needs to be extracted from `heatmap.py`:**
- `get_heat_edges_public()` call with K-anonymity filter
- GeoJSON FeatureCollection construction
- Sport expansion (offroad→[mtb,offroad,gravel])

API endpoints become thin wrappers: `return Response(content=build_full_graph(sport))`.

**Estimated effort for extraction alone: 3-4 hours** (not 2, given the tight coupling to caches, version counters, and response formatting).

## Monitoring

### Publish Health Check

Add to `/healthz` or a dedicated endpoint:

```python
@router.get("/cache-status")
async def cache_status():
    """Check CDN cache freshness. Returns manifest version + age."""
    try:
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(f"{CACHE_PREFIX}/manifest.json")
        if not blob.exists():
            return {"status": "no_cache", "manifest": None}
        manifest = json.loads(blob.download_as_string())
        age_s = time.time() - manifest.get("published_at", 0)
        return {
            "status": "stale" if age_s > 86400 else "ok",
            "version": manifest.get("version"),
            "age_seconds": int(age_s),
            "rollback": manifest.get("rollback", False),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

### Sentry Alerts

- Publish failure → Sentry error (implemented in `publish_heatmap_cache`)
- Manifest older than 48h → Sentry warning (via Cloud Monitoring scheduled check)

## Cost Impact (Realistic)

| Item | Before | After (10 users) | After (1000 users) |
|------|--------|-------------------|---------------------|
| Cloud Run (heatmap reads) | $2-10/mo | $0 | $0 |
| Cloud Storage writes | $0 | ~$0.01/mo | ~$0.10/mo |
| Cloud CDN egress | $0 | ~$0.50/mo | ~$5-15/mo |
| **Total CDN cost** | — | **~$0.51/mo** | **~$5-15/mo** |

CDN egress calculation at 1000 users:
- Average cached file size: ~2MB (gzip)
- Files fetched per session: ~3 (manifest + trails + graph)
- Sessions per day: ~500 (not all 1000 users daily)
- Daily egress: 500 × 3 × 2MB = 3GB/day = 90GB/mo
- CDN egress at $0.08/GB = **~$7.20/mo**

The real win is **latency**: CDN edge <50ms globally vs Cloud Run cold start 3-5s.

## Dependencies

- `google-cloud-storage` pip package (add to `pyproject.toml`)
- `FRONTEND_BUCKET` env var (already set in Cloud Run)
- `PUBLISH_CACHE=true` env var (opt-in, off by default)
- Service account needs `roles/storage.objectCreator` on the bucket (add to Terraform)
- Bucket CORS configured for `chemins-communs.fr`
- `app_settings` DB table for debounce flag (simple key-value, create via Alembic migration)

## Effort (Revised)

- Service layer extraction (graph_builder.py): 3-4 hours
- Backend (cache_writer + trigger + rollback): 2-3 hours
- Frontend (manifest fetch + CDN fallback + logging): 2 hours
- Terraform (bucket CORS + IAM): 1 hour
- Monitoring (cache-status endpoint + Sentry): 1 hour
- Testing: 1-2 hours

**Total: ~1.5-2 days.**

## When to Implement

**Phase: 10+ users.** Not needed now (1 user, in-memory cache is instant). But when Cloud Run cold starts become noticeable (multiple users hitting uncached instance), this eliminates heatmap latency entirely.

The version-based in-memory caching we just shipped is the prerequisite — it ensures we only compute once per import. The CDN write-through is just "also write the result to Storage" on top of that.
