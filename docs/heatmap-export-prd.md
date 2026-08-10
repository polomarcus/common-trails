# PRD — Heatmap Export

**Status:** ✅ APPROVED 2026-06-02 — engineering may start Phase 1
**Author:** Paul (drafted with Claude)
**Date:** 2026-06-01 (drafted) / 2026-06-02 (decisions)
**Estimated reading time:** 25 min

---

## Decisions (2026-06-02)

Paul's calls on the open questions:

### 1. Pivot scope: **Option A — Ship export, no pivot**

> "Most conservative. Export is strictly additive; doesn't subtract from
> anything. Strong recommendation."

The in-app routing UX stays; this feature is purely additive. No
positioning change, no "we're a data layer now" repositioning. The
export gives users another way to consume the community heatmap data,
nothing else. Option B/C/D from §7 are NOT pursued.

### 2. Storage: **GCS bucket (not backend filesystem)**

> "the heatmap should be stored inside a GCS bucket"

The PMTiles binary (and any pre-built MBTiles in Phase 2) lives in a
public GCS bucket served behind Cloud CDN. The backend stops serving
`/heatmap-display.pmtiles` as a static file from `frontend/public/`
once the new pipeline ships; the existing path becomes a 302 redirect
to the GCS URL during transition.

**Implications:**

- The post-rebuild pipeline (currently: `app.jobs.build_pmtiles` writes
  to `/tmp/heatmap-display.pmtiles`, then `docker cp` into
  `frontend/public/`) must be extended to `gsutil cp` into the GCS
  bucket as the final step.
- Local dev keeps the `frontend/public/` copy (no GCS in dev compose).
- Browser fetches `https://heatmap.cdn.cheminscommuns.fr/heatmap-display.pmtiles`
  (or the GCS URL directly, behind the LB).
- Cache-Control: `public, max-age=86400` (matches the heatmap's
  daily-ish refresh cadence).

**Not decided yet** (pre-Phase-1 follow-up): bucket naming, CDN domain,
versioning convention (single mutable file vs `heatmap-display-v{N}.pmtiles`
+ pointer). See §3 below.

---

## TL;DR

Common-Trails owns a unique asset: a community-collected, ODbL-licensed cycling
heatmap derived from GPX uploads + Strava sync. The in-app routing UX is
mediocre and competing with Komoot / Strava / Garmin on that surface is a
multi-year polish problem with no clear ROI for a solo-dev OSS project.

This PRD proposes exposing the heatmap as a **downloadable layer** for use in
mature third-party apps (Alpine Quest, OruxMaps, Locus Map, Gaia GPS, GPX
Studio). The recommendation, ranked by `(user impact) ÷ (engineering cost)`:

| Rank | Format       | Why ship first                                                                                                       |
|------|--------------|----------------------------------------------------------------------------------------------------------------------|
| 1    | **PMTiles**  | Already built every hour. Single static file in `frontend/public/`. Adding a download button is 1-2h of work.        |
| 2    | **MBTiles raster** | Universal — Alpine Quest, OruxMaps, Locus, Gaia all support it. Generated from PMTiles via `pmtiles convert`. Ship behind a backend job. |
| 3    | **GeoJSON / KML overlay** | Loses raster nuance but works in *every* GIS tool. We already have `/heatmap/trails` (GeoJSON LineStrings). Add KML serialization + bbox UI. |

Drop on the floor for now: **MBTiles vector**, **GeoTIFF raster**, **CSV /
Shapefile**. Reasoning in §2.

The pivot question (§7) is open. This PRD argues the export is worth shipping
**even if Paul keeps the in-app routing** — it strengthens the moat (more
heatmap consumers → more contributors → better data), and Phase 1 costs ~1
day. The pivot itself is a separate decision.

---

## Table of contents

1. [Target users + workflows](#1-target-users--workflows)
2. [Export formats](#2-export-formats-the-meat)
3. [API surface](#3-api-surface)
4. [Frontend UX](#4-frontend-ux)
5. [Implementation milestones](#5-implementation-milestones)
6. [Open questions + risks](#6-open-questions--risks)
7. [Pivot rationale](#7-pivot-rationale-decision-document)
8. [Appendix: existing surface](#8-appendix--what-we-already-have)

---

## 1. Target users + workflows

Three personas. All exist among friends-beta and the wider cycling community
Paul has talked to. None of them are hypothetical.

### Persona A — "Marc, the gravel route-planner" (~45% of identified users)

- **Gear:** Garmin Edge 540 / 840, Wahoo Elemnt Roam, paper maps as backup
- **Apps:** Komoot for planning, GPX Studio for tweaks, RideWithGPS for some
  social rides, Strava for recording
- **Workflow:** Plans 60-150 km gravel loops on Friday night. Wants to know
  "what gravel roads do other people ride in this département?" before
  committing to a 6h ride.
- **Pain today:** Strava heatmap is paywalled, low-res at zoom out, only shows
  *anyone with Strava* (mixed road/MTB/walking). Komoot suggestions are
  surface-tag-based, not popularity-based — they confidently suggest
  unrideable D17 secondary roads.
- **What he wants:** Local heatmap as a *static overlay* in Komoot or GPX
  Studio. Sport-filtered to gravel. Click-and-export his planned route as
  usual.
- **Format he can use today:** GeoJSON (GPX Studio supports it). KML if he
  prefers Google My Maps + Garmin Connect.
- **Frequency:** Re-downloads every ~3 months when planning a new region.

### Persona B — "Julien, the MTB explorer" (~30%)

- **Gear:** Garmin Edge Explore 2, phone-on-handlebar
- **Apps:** Alpine Quest (premium) for actual ride navigation, Strava +
  Trailforks for community data, IGN Cartes for the official 1:25 000 topo
- **Workflow:** Knows the obvious MTB spots; wants to find the *unmarked*
  singletracks that locals use. Spends Saturday morning poring over Alpine
  Quest layered with IGN + community data, then heads out.
- **Pain today:** Trailforks is sparse in France outside the big bike parks.
  Strava heatmap visible-on-mobile is paywall + low-res. He's been mailing
  Paul GPX files for 6 months — he knows the data exists, he just can't see
  the aggregate.
- **What he wants:** MBTiles raster he can drop into Alpine Quest's
  `/AlpineQuestData/maps/` folder, with the heatmap rendered as a
  semi-transparent overlay on top of IGN.
- **Format he can use today:** MBTiles raster (Alpine Quest natively
  supports it, [confirmed](https://alpinequest.net/en/help/v2/maps/file-based-select)).
- **Frequency:** Re-downloads every ~6 weeks. New imports = new spots.

### Persona C — "Sophie, the touring cyclist" (~15-25%)

- **Gear:** Wahoo Elemnt Bolt, phone (iPhone)
- **Apps:** Komoot (paid region pack), Gaia GPS premium for the big trips,
  Mapy.cz for the actual cycling routing
- **Workflow:** Plans 3-7 day cycle-tours. Cares about "what's the *gravel*
  path version of the EuroVelo 8" — knows the main route, wants to know which
  popular alternatives exist.
- **Pain today:** Gaia + Komoot both have proprietary heatmap layers but
  they're underwhelming in France (Strava-derived but paywalled, Komoot is
  popularity-of-*Komoot-routed-rides*, not actual GPS heat).
- **What she wants:** Same as Julien but in Gaia GPS instead of Alpine Quest.
- **Format she can use today:** MBTiles raster (Gaia GPS Premium supports
  importing `.mbtiles` files up to 100 MB synced /
  [confirmed](https://help.gaiagps.com/hc/en-us/articles/360024366293-Import-Maps-Using-MBtiles-or-GeoTiff-into-the-iOS-app)).
- **Frequency:** Once per trip, ~4-6 times per year.

### Persona "non-D" — the GIS pro

Worth mentioning even though we won't optimise for him: someone doing
academic / urban-planning research wanting the raw data. He needs Shapefile
or PostGIS dump. He's well-served by the GitHub repo + a `pg_dump` script
exposed in `docs/`. **Not in scope** for the user-facing export.

### What unites all three personas

- They are *not* asking us to replace their planning app. They are asking
  for the *data layer* their existing app is missing.
- They are happy to download a file and put it in a folder. They are not
  expecting live tiles streamed to mobile.
- They cycle weekly; they re-download the heatmap monthly at most.
- They will share the file with riding buddies → 1 download fans out to
  ~3-5 actual users.

---

## 2. Export formats (the meat)

Each format is scored on `(user impact) × (1 / engineering cost)`. The
scoring is rough — direction of travel, not pretending it's a model.

### Format catalogue

#### 2.1 PMTiles (raw passthrough)

- **What it is:** Single-file vector tile archive ([protomaps.com/spec](https://docs.protomaps.com/pmtiles)). Tiles are stored MVT-compressed inside the archive; clients fetch them via HTTP range requests.
- **What we ship today:** `frontend/public/heatmap-display.pmtiles` (~9.4 MB at K=2, ~168 MB at K=1) is built every hour by `app/jobs/build_pmtiles.py` and uploaded to GCS by the `internal_artefacts` Cloud Run job. This is the *source of truth* for the in-app heatmap.
- **Which apps support it as a *user-import* format:** Limited. MapLibre Android 11.7+ supports `pmtiles://` URIs natively ([MapLibre docs](https://maplibre.org/maplibre-native/android/examples/data/PMTiles/)), and the JS / iOS implementations work too. But **no end-user cycling app today exposes "import a PMTiles file" in its UI.** It's a developer format.
- **Use case:** advanced users self-hosting a viewer (e.g. embedding `pmtiles.js` in their own MapLibre webpage), or downstream OSS projects that want to consume our heatmap as a layer in their own product.
- **Data shape:** Vector tiles. Layer `community-trails`, properties `sport`, `user_count`, `pass_count`, `heat_score`, `forward_count`, `backward_count`. Same schema as the in-app map.
- **Implementation:** *Already built.* Adding a "Download heatmap-display.pmtiles" button + a static link to the file is the cheapest possible export. ~1-2 hours of work.
- **Privacy:** K=2 (production) ensures no single user is identifiable in any tile. Same data the public map already serves. **Already public**, so technically not even a new exposure.
- **Score:** **★★★★★** (effort 1, impact 3, leverage 5 because it's free)

#### 2.2 MBTiles raster

- **What it is:** SQLite database containing pre-rendered raster tiles (PNG or JPEG, one row per `z/x/y`). Spec: [github.com/mapbox/mbtiles-spec](https://github.com/mapbox/mbtiles-spec).
- **Which apps support it:**
  - **Alpine Quest** ✅ ([file-based maps doc](https://alpinequest.net/en/help/v2/maps/file-based-select)). Listed alongside GeoTiff, GeoPackage, SqliteDB, KMZ overlays.
  - **OruxMaps** ✅ ([forum + manual](https://www.oruxmaps.com/oruxmapsmanual_en.pdf)). Drop in `oruxmaps/mapfiles/`. v6.5+ supports semi-transparent overlay.
  - **Locus Map (Pro)** ✅ ([docs.locusmap.app](http://docs.locusmap.app/doku.php?id=manual%3Aadvanced%3Amap_tools%3Amobac)). Listed alongside RMaps and OSMAND-SQLITE.
  - **Gaia GPS (Premium)** ✅ ([help.gaiagps.com](https://help.gaiagps.com/hc/en-us/articles/360024366293-Import-Maps-Using-MBtiles-or-GeoTiff-into-the-iOS-app)) **but** only PNG/JPEG, vector-PBF MBTiles unsupported, **synced files must be ≤ 100 MB**.
  - **QGIS** ✅ via Raster → Add Layer → MBTiles.
  - **GPX Studio** ❌ (web app, can consume vector tiles but no "import MBTiles" UI).
- **Data shape:** A rasterisation of the heatmap — *what users see in the app today*, frozen at the zoom levels we ship. Trade-off: lose interactivity (no click-for-popularity, no sport toggle on the device, no time filter) but gain universal compatibility.
- **Implementation:**
  - **Build:** `pmtiles convert heatmap-display.pmtiles heatmap.mbtiles` will not work directly (it'd produce vector MBTiles, which Gaia rejects). The real path is:
    ```
    # 1. Serve PMTiles locally via `pmtiles serve`
    # 2. Use `gdal_translate` or `mb-util --image_format=png` to rasterise
    #    the MVT → PNG at each zoom 6..14
    ```
    Existing tools: [`mb-util`](https://github.com/mapbox/mbutil), [`tile-stitch`](https://github.com/ericfischer/tile-stitch), or a custom Python that renders MVT → PNG via `mapbox-gl-native` headless or Cairo. Estimate: 1-2 days to wire correctly + size-tune for the 100 MB Gaia limit.
  - **Size:** At z6-z14 with K=2 (current ~9.4 MB vector), rasterised at 256-px PNG-8 is roughly 40-150 MB depending on transparency + palette. Has to fit under 100 MB for Gaia sync; Alpine Quest / Locus / Orux don't care.
  - **Storage:** One file per sport (road / gravel / mtb / offroad / running) — 5× the size for full coverage, or one combined file with a `sport` query parameter at *build* time selecting which edges to render.
- **Privacy:** Inherits PMTiles privacy (K=2). No new surface.
- **Score:** **★★★★☆** (effort 3-5 days, impact 5, leverage 4)

#### 2.3 MBTiles vector

- **What it is:** Same SQLite container, but tiles are MVT (PBF) instead of PNG.
- **Which apps support it:** Mostly developer-oriented tools (MapLibre, Mapbox SDK demos, `tippecanoe`-ecosystem stuff). **Notable end-user app NOT supporting it: Gaia GPS** (vector-PBF explicitly rejected, [docs](https://help.gaiagps.com/hc/en-us/articles/360024366293-Import-Maps-Using-MBtiles-or-GeoTiff-into-the-iOS-app)). OruxMaps + Alpine Quest are raster-oriented in practice.
- **Verdict:** **Skip.** The user audience (people who'd use vector MBTiles over raster) overlaps almost 100% with the "use PMTiles directly" audience. We don't need both.

#### 2.4 GPX overlay (colour-coded LineStrings)

- **What it is:** Standard GPX 1.1 file where each `<trk>` represents a heat-popularity tier, with `<extensions>` carrying styling hints. Garmin's GPX extension lets you set track colour and width per-track.
- **Which apps support GPX:** ⚠️ All of them — but as a *route file*, not as a *map layer*. Loading the heatmap as a GPX means importing 50 000+ tracks into your "tracks" list, which clutters the UI on every device.
- **Verdict:** **Skip for the main flow.** Useful only as a per-route export ("popular gravel loops in this département as 10 GPX tracks") which is a different feature — closer to "trip suggestion" than "heatmap layer". Could be a Phase 4 thing if community asks for it.

#### 2.5 GeoJSON

- **What it is:** Standard FeatureCollection with LineString geometries + properties (`sport`, `user_count`, `pass_count`, `heat_score`).
- **What we already serve:** `GET /heatmap/trails?sport=gravel&days=30` returns gzipped GeoJSON. The endpoint exists, is bbox-filterable, and is gzip-cached. Used by some current frontend code paths.
- **Which apps support it:**
  - **GPX Studio** ✅ (drops `.geojson` files on the map natively).
  - **Gaia GPS** ✅ ([importable on the web](https://help.gaiagps.com/hc/en-us/articles/360052763513-Import-GPX-KML-KMZ-GeoJSON-or-FIT-Files-on-gaiagps-com)).
  - **QGIS** ✅ (Vector → Add Layer).
  - **MapLibre web demos / OSS projects** ✅
  - **Alpine Quest / OruxMaps / Locus Map** ⚠️ (display GeoJSON as overlay routes, but with 50K LineStrings the device chokes).
- **Use case:** Web users, GPX-Studio integration, GIS pros, anyone wanting the raw data without rasterising.
- **Data shape:** LineString per heat_edge, ~11 m segments. Properties make sport / popularity filterable in the consuming app. Bbox-filtered to keep size reasonable (full France at K=2 is ~3 MB gzipped, but full France at K=1 with all sports is ~80 MB ungzipped — needs bbox filter mandatory).
- **Implementation:** Endpoint exists. We need to:
  1. Add a stricter bbox cap (e.g. max 50 km × 50 km) to avoid full-France downloads bringing the API down.
  2. Add a per-sport filter (already there: `?sport=...`).
  3. Surface it in the frontend export UI.
  4. Document it in the README + the export modal.
- **Privacy:** K=2. The endpoint already enforces `HEATMAP_K_ANONYMITY`.
- **Score:** **★★★☆☆** (effort 1 day to tighten + UI-surface, impact 3 — covers a smaller audience than MBTiles, but the audience is high-leverage: GPX Studio users + GIS / OSS community)

#### 2.6 KML / KMZ

- **What it is:** Google Earth's XML format. KMZ = zipped KML. Supports styled `LineString` + `Polygon`, colour + width attributes inline.
- **Which apps support it:** Google Earth, Alpine Quest (KMZ overlays — confirmed), Locus, OruxMaps, Gaia GPS, RideWithGPS (import only), Garmin BaseCamp.
- **Use case:** Users who want the heatmap as a layer in Google Earth / Google My Maps, or in apps where MBTiles is unavailable (e.g. Alpine Quest Free tier).
- **Data shape:** One `<Folder>` per sport + popularity bucket, each containing N `<Placemark><LineString>` features with inline `<Style>` (RGBA hex from our heat ramp).
- **Implementation:** Generate from the same GeoJSON path, with a serialiser that maps `heat_score` → `<color>` and `bucket` → folder. Estimate: ~1 day for a robust serialiser + KMZ zipping.
- **Size:** ~3-5× larger than GeoJSON before zip, ~1.5× after KMZ zipping. Bbox filter mandatory same as GeoJSON.
- **Privacy:** K=2. Same as GeoJSON.
- **Score:** **★★★☆☆** (effort 1 day, impact 3 — strong overlap with MBTiles raster audience, but easier to ship)

#### 2.7 GeoTIFF raster

- **What it is:** Single georeferenced TIFF image (one pixel per ~10 m).
- **Which apps support it:** Gaia GPS ([with a 105 MB cap, iOS](https://help.gaiagps.com/hc/en-us/articles/360024366293-Import-Maps-Using-MBtiles-or-GeoTiff-into-the-iOS-app)), QGIS, Alpine Quest, Locus Pro. Same audience as MBTiles raster, **but only at a single zoom** (no pyramid).
- **Verdict:** **Skip.** MBTiles raster strictly dominates GeoTIFF for our use case (pyramid → can zoom out, smaller file at high zoom).

#### 2.8 Shapefile / GeoPackage

- **What it is:** Industry-standard GIS vector containers.
- **Which apps support them:** QGIS, ArcGIS, ogr2ogr ecosystem. Not the cyclist personas.
- **Verdict:** **Skip for v1**, but document that `pg_dump`-style raw exports are available via the API repo for GIS pros. If demand emerges, a single `ogr2ogr` conversion from the GeoJSON path is one CLI call.

### Ranking summary

| Format         | Audience          | Build effort | Bandwidth        | Score | Ship?                  |
|----------------|-------------------|--------------|-------------------|--------|------------------------|
| PMTiles        | OSS / devs        | 0 (existing) | ~10 MB           | ★★★★★ | **Phase 1**            |
| MBTiles raster | Alpine/Orux/Locus/Gaia | 3-5 days     | 40-100 MB        | ★★★★☆ | **Phase 2**            |
| GeoJSON        | GPX Studio / GIS  | 1 day (tighten existing) | 3-30 MB | ★★★☆☆ | **Phase 2**            |
| KML / KMZ      | Alpine / Google Earth | 1-2 days     | 5-50 MB          | ★★★☆☆ | **Phase 3** if asked   |
| GPX overlay    | none clean        | 1 day        | 5-50 MB          | ★☆☆☆☆ | Skip                   |
| MBTiles vector | Devs (PMTiles already serves) | 1 day | 10-30 MB | ★☆☆☆☆ | Skip                   |
| GeoTIFF        | dominated by MBTiles raster | 1 day | 50-300 MB | ★☆☆☆☆ | Skip                   |
| Shapefile / GPKG | GIS pros        | 1 day        | 5-50 MB          | ★☆☆☆☆ | Skip (offer raw `pg_dump` in repo) |

### Top-2 recommendation

**Phase 1:** PMTiles passthrough. 1-2h work. Already public, already built
hourly. Pure marketing + UI win.

**Phase 2:** MBTiles raster + GeoJSON in parallel. They cover ~95% of the
identified user audience:

- MBTiles raster → Persona B (Julien-MTB-AlpineQuest) and Persona C
  (Sophie-Gaia-touring)
- GeoJSON → Persona A (Marc-GPX-Studio-gravel)

KML/KMZ in Phase 3 if Persona A users ask for Google Earth export.

---

## 3. API surface

### Storage architecture (decided 2026-06-02)

The PMTiles binary lives in a **public GCS bucket**, not on the backend
container filesystem. The browser fetches it from a CDN-fronted URL,
the backend never proxies it.

```
build_pmtiles (Cloud Run Job after weekly heat rebuild)
  ├─ writes to /tmp/heatmap-display.pmtiles inside container
  └─ `gsutil cp /tmp/heatmap-display.pmtiles \
       gs://common-trails-heatmap-prod/heatmap-display.pmtiles`
        ↓
GCS bucket: gs://common-trails-heatmap-prod/
  ├─ heatmap-display.pmtiles         (current — mutable name, see versioning note)
  ├─ heatmap-display-{date}.pmtiles  (archived snapshots, retention TBD)
  └─ /mbtiles/<sport>-<bbox>-<min_uc>.mbtiles  (Phase 2, async-built)
        ↓
Cloud CDN (existing LB) — same domain as the frontend or sub-domain like cdn.cheminscommuns.fr
        ↓
Browser fetches https://cdn.cheminscommuns.fr/heatmap-display.pmtiles
```

### Phase 1 infra delta (this is what gets built first)

- **Terraform:** new `google_storage_bucket "heatmap"`, public-read,
  uniform IAM, lifecycle rule deleting archives > 90 d. Linked to
  existing Cloud CDN LB via a new backend bucket.
- **Cloud Run Job permission:** the `build_pmtiles` job's SA gets
  `roles/storage.objectAdmin` scoped to the heatmap bucket only.
- **Frontend env:** new `NEXT_PUBLIC_HEATMAP_URL` env var. Default to
  the GCS public URL in prod; falls back to `/heatmap-display.pmtiles`
  (relative path = served from frontend container's static export) in
  local dev.
- **Backend:** stop bind-mounting the PMTiles into the frontend
  container. The `make pmtiles` target keeps writing
  `frontend/public/heatmap-display.pmtiles` for dev parity, but prod
  doesn't carry it in the frontend image.

### Versioning (pre-Phase-1 decision needed)

Two options, pick one before writing terraform:

| Strategy | URL the browser fetches | Pros | Cons |
|---|---|---|---|
| **Mutable name** | `heatmap-display.pmtiles` | Single URL, simple frontend code, simple CDN purge | CDN cache staleness for up to `max-age`; partial-write race if `gsutil cp` is interrupted (use atomic-rename trick: `cp` to `tmp` + GCS `mv` to final) |
| **Versioned name + pointer** | `heatmap-display-v18342.pmtiles` (file) + `heatmap-display.json` (`{"latest": "v18342", "size": N}`) | Cache-perfect; previous versions readable; rolling upgrades | Frontend has to fetch the JSON first, then the file (2 round-trips); needs CI pruning logic |

**Recommendation:** Phase 1 ship the **mutable name** (matches the
existing app behavior, minimal change). Move to versioned in Phase 3
if anyone complains about CDN staleness.

### Existing endpoints (keep)

```
GET /heatmap-display.pmtiles                 # static file, CDN-served
GET /heatmap/trails                          # GeoJSON LineStrings, bbox + sport + days filter
GET /heatmap/export                          # GeoJSON cells (legacy, low-resolution)
GET /heatmap/summary                         # aggregate stats per sport
GET /heatmap/stats                           # bucketed cells
GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt   # MVT fallback (days filter only)
GET /heatmap/dfci                            # DFCI tracks
```

### New endpoints (proposed)

```
GET  /export/heatmap                         # discovery: list available formats + sizes
GET  /export/heatmap.pmtiles                 # alias → static GCS file (302 redirect)
GET  /export/heatmap.mbtiles                 # pre-built raster, sport-filtered, optionally bbox-clipped
GET  /export/heatmap.geojson                 # alias → /heatmap/trails with tighter bbox cap
GET  /export/heatmap.kml                     # KML serialisation of the GeoJSON path
POST /export/heatmap/request                 # for async large exports (Phase 3)
GET  /export/heatmap/{request_id}            # poll + download async export (Phase 3)
```

### Query parameters (all formats)

| Param         | Type           | Default        | Notes                                            |
|---------------|----------------|----------------|--------------------------------------------------|
| `bbox`        | `lon,lat,lon,lat` | none (full France) | Max 50 km × 50 km for GeoJSON/KML in Phase 2; full France OK for PMTiles |
| `sport`       | `road \| gravel \| mtb \| offroad \| running \| all` | `all` | Server-side filter, smaller files |
| `min_uc`      | `int >= K_ANONYMITY` | `2` (prod K) | Can only go ≥ K_ANONYMITY, never below |
| `days`        | `int 7..3650`  | none (all-time) | Time-window filter via `heat_edge_contributors` |
| `format`      | hint for `/export/heatmap` discovery | — | Returns matching file via 302 |

### Auth + rate limiting

- **Anonymous OK** for PMTiles + small GeoJSON (matches existing behaviour).
  Data is already public, ODbL-licensed.
- **Authenticated required** for MBTiles raster ≥ 50 MB or any async export
  (we want to prevent a bot from triggering 100 raster-builds in a loop).
  Reuse existing JWT.
- **Rate limit:**
  - Anonymous: 10 small exports / 24 h per IP (GeoJSON ≤ 5 MB, PMTiles direct
    download).
  - Authenticated: 5 raster builds / 24 h per user account.
  - Webhook /alert if any single IP exceeds 100 PMTiles downloads/h —
    indicates either a popular embed (good, surface in admin), or a scrape.
- **Caching:** All static files behind Cloud CDN with `Cache-Control: public,
  max-age=3600, stale-while-revalidate=86400`. ETags from the hourly rebuild
  (`X-Edge-Version` header). Browsers and downstream caches will not
  re-download unchanged files.

### Request/response shapes

#### Discovery endpoint

```
GET /export/heatmap?bbox=3.7,43.5,4.0,43.7&sport=gravel
→ 200
{
  "license": "ODbL-1.0",
  "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
  "attribution": "© CHEMINS COMMUNS contributors — ODbL 1.0",
  "k_anonymity": 2,
  "generated_at": "2026-06-01T08:00:00Z",
  "edge_version": 18342,
  "formats": [
    {
      "format": "pmtiles",
      "url": "https://cdn.chemins-communs.fr/heatmap-display.pmtiles",
      "size_bytes": 9852416,
      "note": "All sports, all zoom levels. Use with MapLibre / pmtiles.js."
    },
    {
      "format": "mbtiles_raster",
      "url": "https://api.chemins-communs.fr/export/heatmap.mbtiles?bbox=3.7,43.5,4.0,43.7&sport=gravel",
      "estimated_size_bytes": 48000000,
      "note": "Raster pyramid z6-z14, gravel only. Compatible with Alpine Quest, OruxMaps, Locus, Gaia GPS."
    },
    {
      "format": "geojson",
      "url": "https://api.chemins-communs.fr/export/heatmap.geojson?bbox=3.7,43.5,4.0,43.7&sport=gravel",
      "estimated_size_bytes": 2300000,
      "note": "LineStrings with sport, user_count, pass_count, heat_score. Compatible with GPX Studio, QGIS, Gaia GPS."
    }
  ]
}
```

#### Static-file download (PMTiles)

```
GET /export/heatmap.pmtiles
→ 302 Location: https://cdn.chemins-communs.fr/heatmap-display.pmtiles
   Cache-Control: public, max-age=3600
   X-License: ODbL-1.0
   X-Edge-Version: 18342
```

The actual file lives on Cloud Storage; the API redirects to keep the public
URL stable even if we re-bucket.

#### MBTiles raster download

```
GET /export/heatmap.mbtiles?bbox=3.7,43.5,4.0,43.7&sport=gravel
→ 200
   Content-Type: application/vnd.mapbox-vector-tile  (or vnd.mbtiles, debate TBD)
   Content-Disposition: attachment; filename="heatmap-gravel-2026-06-01.mbtiles"
   X-License: ODbL-1.0
   X-Edge-Version: 18342
   Cache-Control: public, max-age=3600
   <binary>

→ 413 Payload Too Large if estimated_size > 100 MB → suggest async flow
→ 429 Too Many Requests if rate limit hit
```

Implementation note: keep this endpoint *cache-friendly*. Cache key =
`(sport, bbox-rounded-to-0.1deg, edge_version)`. A 7-day Cloud Storage cache
of pre-built MBTiles for common bbox+sport tuples covers ~80% of requests
free of compute.

#### GeoJSON download

Already exists at `/heatmap/trails` — alias `/export/heatmap.geojson` with
stricter bbox cap.

```
GET /export/heatmap.geojson?bbox=3.7,43.5,4.0,43.7&sport=gravel
→ 200 (existing GeoJSON shape with `metadata.license` + ODbL fields)
→ 413 if bbox > 50 km × 50 km
```

### ODbL attribution

The API response MUST carry:

1. `X-License: ODbL-1.0` header
2. License clause embedded in the file itself:
   - PMTiles: as a `metadata.json` field inside the archive (already there).
   - MBTiles raster: as a row in the `metadata` SQLite table (`name=attribution`, `value=© CHEMINS COMMUNS contributors — ODbL 1.0`).
   - GeoJSON: as a top-level `metadata` object on the FeatureCollection (already there).
   - KML: as a `<atom:author>` + `<description>` block in the document root.
3. A `LICENSE.txt` accompaniment shown in the download UI ("By downloading,
   you agree to share-alike under ODbL 1.0. See [link].").

---

## 4. Frontend UX

### Discovery

A **single "Télécharger le fond de carte" button** in the map page header
group, next to the Profile/Sport selectors. Discreet — not the
most-prominent action. Wording-wise, French-default, mirror existing UI tone.

Wireframe (ASCII, current map page header):

```
┌──────────────────────────────────────────────────────────────────────────┐
│ CHEMINS COMMUNS   🚴 Route│🪨 Gravel│⛰️ MTB│🌿 Off│🏃   📥 Télécharger ▼ │
│                                                                          │
│                                                          ▼ on click:     │
│                                                ┌───────────────────────┐ │
│                                                │ Télécharger le        │ │
│                                                │ fond communautaire    │ │
│                                                │                       │ │
│                                                │ Format:               │ │
│                                                │  ○ PMTiles (avancé)   │ │
│                                                │  ● MBTiles raster     │ │
│                                                │  ○ GeoJSON            │ │
│                                                │  ○ KML                │ │
│                                                │                       │ │
│                                                │ Zone:                 │ │
│                                                │  ● Vue actuelle       │ │
│                                                │  ○ Tracer un rectangle│ │
│                                                │  ○ Toute la France    │ │
│                                                │                       │ │
│                                                │ Filtres:              │ │
│                                                │  Sport: [gravel ▾]    │ │
│                                                │  Popularité min: [2▾] │ │
│                                                │  Période: [Tout ▾]    │ │
│                                                │                       │ │
│                                                │ Taille estimée: 47 MB │ │
│                                                │                       │ │
│                                                │ ☑ J'accepte ODbL 1.0  │ │
│                                                │   (partage à l'identique)│
│                                                │                       │ │
│                                                │  [Télécharger]        │ │
│                                                └───────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

### Modal flow

1. **Format picker** — radio group, 3-4 options, with one-line app-compat
   hint under each ("Compatible avec Alpine Quest, OruxMaps, Locus, Gaia
   GPS" under MBTiles raster).
2. **Bbox picker:**
   - "Vue actuelle" (default) — captures the current map viewport.
   - "Tracer un rectangle" — turns the cursor into a draw-rectangle tool;
     user draws on map, modal stays open and shows the dimensions.
   - "Toute la France" — only available for PMTiles (too big otherwise).
3. **Sport + min_uc + days filters** — same options as the in-app heatmap
   filters. Sport defaults to the currently-selected sport.
4. **Size estimate** — fetched live from `/export/heatmap` discovery, updates
   as user changes filters.
5. **ODbL checkbox** — required. One-line summary + link to the license.
6. **Download button** — triggers either a direct `<a>` download (PMTiles +
   GeoJSON, fast) or an async job (MBTiles raster > 50 MB).
7. **Help link** — "Comment importer dans mon app ?" → links to a
   per-app one-page how-to (see Phase 3 docs in §5).

### Direct vs async

- **Direct** (<50 MB, <30 s server time): plain `<a href download>` —
  browser handles it. Most cases.
- **Async** (≥50 MB or ≥30 s): toast "Préparation de l'export, ~2 min" +
  poll `/export/heatmap/{id}` + show download link in a tray-notification
  when ready. Probably implement only in Phase 3 — Phase 2 caps at 50 MB +
  asks the user to reduce bbox.

### Discoverability beyond the map page

- Add an item in the right-rail Discover/Stats sidebar: "📥 Télécharger le
  fond communautaire (ODbL)".
- README section "Use the heatmap in your own app" with one-paragraph
  recipe per format.
- Long-tail SEO: a public landing page `/heatmap-export` describing the
  data + linking to the modal. Targets "carte chaleur cyclisme libre",
  "free Strava heatmap alternative", "cycling heatmap GeoJSON download".

---

## 5. Implementation milestones

### Phase 1 — Ship PMTiles direct download (1-2 days)

**Goal:** zero-marginal-cost win. Make the existing public file *discoverable*.

**Deliverables:**

- A "Télécharger le fond communautaire" button in the map header.
- Modal with format = PMTiles only, ODbL checkbox, single download button.
- Backend: `GET /export/heatmap` returns one format (pmtiles) with size +
  ODbL metadata.
- `GET /export/heatmap.pmtiles` → 302 to the GCS file.
- README section + `/heatmap-export` landing page (1 page).

**Files touched (estimate):**

- `frontend/app/map/page.tsx` — header button
- `frontend/components/ExportHeatmapModal.tsx` — new modal component
- `frontend/lib/api.ts` — `getExportInfo()` fetch
- `backend/app/api/heatmap.py` or new `backend/app/api/export.py` — new endpoints
- `backend/tests/api/test_export.py` — endpoint tests (license header, redirect target, etag)
- `e2e/tests/heatmap-export.spec.ts` — Playwright: click button, assert
  modal opens, assert download link points to .pmtiles
- `docs/heatmap-pipeline.md` — link to the export PRD + endpoint

**Dependencies:** none beyond existing PMTiles pipeline.

**Effort:** ~1 day backend + 1 day frontend, can ship same week.

**Definition of done:**

- Curl-able `/export/heatmap.pmtiles` returns 302 with ODbL header.
- E2E spec exercises the modal-to-download flow.
- README has the recipe + a screenshot.

### Phase 2 — MBTiles raster + GeoJSON (3-5 days)

**Goal:** cover the actual user personas (Marc / Julien / Sophie).

**Deliverables:**

- **GeoJSON path:**
  - Alias `/export/heatmap.geojson` → `/heatmap/trails` with bbox cap (max
    50 km × 50 km, return 413 above).
  - Modal: GeoJSON format option, bbox picker (viewport / draw rectangle).
- **MBTiles raster path:**
  - New job `backend/app/jobs/build_mbtiles_raster.py` — takes (sport, bbox,
    z_min, z_max) and produces a `.mbtiles` file by:
    1. Querying `heat_edges_display` for the bbox + sport.
    2. Rasterising MVT → PNG-8 per zoom via `mapbox-gl-native` headless or
       a Cairo-based renderer. Investigate: [`tilemaker`](https://github.com/systemed/tilemaker), [`mb-util`](https://github.com/mapbox/mbutil), or roll our own with the
       Python `mapbox-vector-tile` + `pillow` stack.
    3. Packing into MBTiles SQLite with `metadata` table (license, sport,
       generated_at, edge_version).
  - Endpoint `/export/heatmap.mbtiles?bbox=&sport=` calls the job
    synchronously up to ~30 s, returns binary.
  - Cache layer: store pre-built MBTiles for the 10 most-requested
    bbox+sport combos in GCS, served via 302 like PMTiles.
- **Modal:** add MBTiles raster + GeoJSON format options + dynamic size
  estimate.

**Files touched:**

- `backend/app/jobs/build_mbtiles_raster.py` — new
- `backend/app/api/export.py` — new endpoints
- `backend/app/services/heatmap_raster.py` — MVT-to-PNG renderer
- `backend/tests/jobs/test_build_mbtiles_raster.py` — fixture-driven test
- `frontend/components/ExportHeatmapModal.tsx` — format options + bbox picker
- `frontend/components/BboxDrawTool.tsx` — new map interaction
- `e2e/tests/heatmap-export.spec.ts` — extend with bbox draw + MBTiles flow
- `docs/heatmap-pipeline.md` — document the raster pipeline

**Dependencies:**

- Rendering: pick between (a) `mapbox-gl-native` headless (Docker image
  exists, ~500 MB layer, robust) and (b) Python Cairo (lighter, but we have
  to re-implement the heatmap paint properties — gradient, blur, line-sort).
  Recommendation: start with (a), invest in (b) if startup cost matters.
- Storage: a `gs://common-trails-cdn/exports/` bucket with auto-cleanup
  policy (7-day TTL on bbox-specific files, infinite on full-region builds).

**Effort:** ~3-5 days, dominated by the raster-rendering decision.

**Definition of done:**

- A 50 km × 50 km gravel MBTiles file imports cleanly into Alpine Quest +
  Gaia GPS (manual smoke test on Paul's phone).
- A 50 km × 50 km gravel GeoJSON file imports cleanly into GPX Studio.
- E2E spec covers both formats end-to-end.
- Size estimates in the modal match within ±20% of the actual file.

### Phase 3 — Filters, async, KML, versioning (DONE 2026-06-02)

**Status:** ✅ shipped. See `docs/heatmap-pipeline.md` § "Heatmap
Export — public download" for the operational reference. The async
pipeline + KML/KMZ + versioning convention all landed together in one
PR (per CLAUDE.md "one bundled PR over many small"). Per-app docs
(alpine-quest.md, gpx-studio.md, etc.) + SEO landing page are deferred
to a follow-up because they're content not engineering.

**Goal:** cover edge cases + onboarding friction.

**Deliverables:**

- **KML/KMZ** format (Persona A who prefers Google Earth, Garmin BaseCamp).
- **Async export** for files > 50 MB:
  - `POST /export/heatmap/request` → enqueues a Cloud Tasks job
  - `GET /export/heatmap/{request_id}` → returns status (queued, building, done) + signed download URL
  - Frontend tray-notification when ready
- **Date filter** (`?days=N`) for personas wanting "last 30 days popularity"
  rather than all-time.
- **Per-app docs:** one-pagers under `docs/heatmap-export/`:
  - `alpine-quest.md`
  - `oruxmaps.md`
  - `locus-map.md`
  - `gaia-gps.md`
  - `gpx-studio.md`
  - `qgis.md`
- **SEO landing page** `/heatmap-export` with hero, format comparison, 5
  per-app recipes embedded.
- **Telemetry:** add `export.download` Datadog event with `format`, `bbox_km²`, `sport`, `auth_status` tags. Surface in a dashboard
  (which formats are people actually using? which apps drive traffic?).

**Files touched:**

- `backend/app/jobs/build_kml.py` — new
- `backend/app/api/export.py` — async endpoints + worker
- `backend/app/jobs/worker_export.py` — Cloud Tasks worker
- `frontend/components/ExportHeatmapModal.tsx` — async UI + KML option + date filter
- `frontend/app/heatmap-export/page.tsx` — landing page
- `docs/heatmap-export/*.md` — 6 per-app docs

**Effort:** ~1 week, parallelisable (async backend + frontend + docs).

**Definition of done:**

- A 200 MB France-wide gravel MBTiles export completes async + delivers
  download link.
- All 6 per-app docs have screenshots + tested instructions.
- Dashboard shows usage breakdown by format.

### Cumulative effort estimate

Phase 1 + 2 + 3 ≈ **2 weeks of solo-dev work** (interspersed with the usual
maintenance + bug-fix toil — so ~1 month of real calendar time).

---

## 6. Open questions + risks

### 6.1 Cost

- **Bandwidth:** PMTiles (~10 MB at K=2) × N downloads/month. At 1000
  downloads/month: 10 GB egress, ~$0.12 on GCP (Cloud CDN egress to EU is
  ~$0.08-0.12/GB). Negligible.
- **MBTiles raster build:** ~30 s of CPU per build, ~50-100 MB of output.
  Cache aggressively (per-bbox+sport+edge_version key, 7-day TTL). At 1000
  builds/month: ~$0.50 of Cloud Run compute + $5 of Cloud Storage at 100 GB
  retained.
- **Cloud SQL:** No new query patterns. Same `heat_edges_display` matview
  the in-app heatmap already uses. db-f1-micro stays in budget.
- **Total monthly add:** estimated **+$5-10**. Stays under the $10/month
  target *if* we cap async builds + cache aggressively.

**Pricing decision:** the data is ODbL-licensed. We **must not** put it
behind a paywall — that would conflict with the license spirit and the
project mission ("free open heatmap, reclaiming community data from
paywalled platforms" — `project_mission.md`). Possible models:

- **Free, with rate limits.** Recommended. Anonymous: 10 small exports/day;
  authenticated: 5 raster builds/day. No payment.
- **Donation/Tip jar** for users who like the data (Buy Me a Coffee /
  Liberapay). Surfaced in the modal *after* download.
- **No paid tier** for the export itself.

Compare to **Strava** ($5/mo) and **Gaia GPS Premium** ($40/yr) — both are
all-app subscriptions. We can't compete on "all-app" and we shouldn't —
we'd be selling the open data, contra ODbL. The donation path is the only
clean one.

### 6.2 Legal

- **ODbL share-alike:** any derivative dataset (e.g. a GeoTIFF someone
  builds from our PMTiles, then publishes) must also be ODbL. **Attribution
  must be preserved.** Implementation:
  - Every export file embeds the license + attribution in metadata.
  - The download UI requires checkbox-acceptance ("J'accepte le partage à
    l'identique sous ODbL 1.0").
  - The landing page documents downstream-user obligations.
- **What about a private app embedding the heatmap?** ODbL is fine with
  this — they consume the data, they don't redistribute it. They only need
  to attribute. No new license obligations.
- **What if Strava complains?** They can't — we don't import their
  heatmap, we build ours from user-uploaded GPX + Strava OAuth syncs (user
  consents). The audit memory has already gone through this
  (`project_mission.md` + `project_strava_concurrency_2026_05_26.md`).
- **What about Strava-derived activities being re-published?** Strava's API
  ToS allows derivative aggregate data (heatmaps), forbids per-user data
  publishing. Our K=2 + aggregate-only export satisfies that.

### 6.3 Quality gating

- **78% of local edges are `grid_fallback` (jittered 4dp, ~11 m granularity).** Exporting them as-is is honest but ugly. Three options:
  1. **Export everything** (status quo for the in-app heatmap). Honest, but the export looks like garbage in dense urban areas where most edges are unmatched.
  2. **Export Valhalla-matched only.** Cleaner-looking but drops huge swaths of countryside (where map-matching is weak — DFCI tracks, MTB singletracks, off-OSM trails).
  3. **Tag both, let the consumer filter.** Add `match_source` property in vector formats; document the meaning. Raster formats can't do this — pick one (probably "include everything", to be consistent with the in-app map).

  Recommendation: **option 3** for vector (PMTiles, GeoJSON, KML) since the
  consumer can filter in their app; **option 1 with a note** for MBTiles
  raster. The note in the modal: "Le tracé est lissé au mieux selon les
  données OSM disponibles ; certaines zones rurales affichent un quadrillage de ~10 m."

- **What about Layer-1 / Layer-2 / Layer-3 routing-quality issues?** Out of
  scope for this PRD — the export ships *as-is*, mirroring what the in-app
  heatmap shows. Improving the heatmap data is a separate workstream
  (`project_session_2026_05_31.md`).

### 6.4 Discoverability — how do users LEARN about us?

This is the *real* product risk. Building the export is easy; making people
find it is hard.

- **SEO:** target "carte chaleur cyclisme libre", "alternative gratuite
  Strava heatmap", "MBTiles heatmap vélo France". The landing page +
  README is the start. Estimated 6-12 months to see organic traffic for
  long-tail.
- **OSS distribution:** post on
  - r/cycling, r/strava, r/openstreetmap, r/bikepacking
  - HN ("Show HN: open-source community cycling heatmap, ODbL-licensed,
    free downloads")
  - Mastodon / Bluesky #cycling #openstreetmap
  - Le forum OSM-fr, vélobécane forum, gravelhouse Discord
- **App ecosystem outreach:** email Alpine Quest's `psyberia.net` — ask if
  they'd list us in their data-source docs. Same for OruxMaps' forum, Locus
  Map's blog. None of them have a budget or competitive heatmap; they may
  be receptive.
- **Cross-link with OSM:** ask the OSM-fr community to mention us on the
  wiki for "cycling-related data sources". Tag our export files as
  `source=chemins-communs-odbl` for downstream wiki/datasets.
- **Friends-beta multiplier:** each persona-style user told tells 3-5
  buddies (Persona C above). Word-of-mouth in cycling clubs is real but
  slow.

**Honest estimate:** 6-18 months to reach 1k MAU on the export feature
alone. Same as any niche OSS data product. Building it doesn't guarantee
discovery, but *not* building it guarantees no discovery.

### 6.5 Sustainability — who runs the pipeline?

If we pivot to "data-only" (§7), the GitHub-PR/community-routes workflow
gets weaker — fewer reasons for users to *create* an account vs just
download. Possible:

- Lower expected contributions; rely on a smaller, more committed
  contributor base.
- Strava-sync becomes the primary acquisition (one-click contribution),
  while the export is the primary value to *consumers*. The two are
  decoupled.
- The `routes` GitHub-like collaboration becomes a nice-to-have rather than
  the centerpiece. Some friends still use it; not the main pitch.

**Risk:** if contributors stop coming, the heatmap stales. Current beta
(~10 athletes) has 1.7M heat_edges; we'd want to keep growing toward 10M+
before being meaningfully useful in regions outside Hérault. Need a
**contributor flywheel** — "your traces make this map better, get the
export free" — surfaced clearly.

### 6.6 Quality of the PMTiles asset itself

The hourly PMTiles rebuild is *production-tested* for the in-app heatmap
but has a few known gotchas:

- Build is triggered by `internal_artefacts` Cloud Run job. If the job
  silently fails, the export file stale for up to ~24 h. **Add Datadog
  monitor on PMTiles age >= 2 h on the GCS bucket.**
- Cold-build at full France K=1 = 168 MB, K=2 = 9.4 MB. K=2 is what we
  serve. The export should NEVER expose K=1 publicly (privacy).

### 6.7 Other risks (smaller)

- **Bandwidth spike from a popular post (HN front page).** Cap CDN egress
  via Cloud CDN budget alerts. Worst case, the file goes 404 for an hour;
  not a disaster.
- **Format-spec drift** (PMTiles spec v3, MBTiles v1.3). Pin versions in
  the docs + landing page.
- **Per-app compatibility regressions** (Alpine Quest changes their
  MBTiles parser). Mitigation: per-app smoke tests + version pin in the
  per-app docs.

---

## 7. Pivot rationale (decision document)

> **DECIDED 2026-06-02 — Paul chose Option A: ship export, no pivot.**
>
> Quote: *"Most conservative. Export is strictly additive; doesn't subtract
> from anything. Strong recommendation."*
>
> The rest of this section is kept for context — explaining what was on
> the table at the time. The in-app routing UX is NOT downgraded or
> deprioritized; the export is purely additive.

This section was originally drafted for Paul to ponder. Pros + cons
were laid out without a recommendation; the decision lives at the top
of this document.

### The pivot proposition

> **Common-Trails is repositioned as "the open community cycling heatmap
> data source". The in-app routing UI is downgraded from headline feature
> to "demo viewer for the data". Drag-edit-and-route-here UX work is
> deprioritised; export quality + data freshness + per-region coverage
> takes priority.**

### Arguments FOR the pivot

1. **Routing UX is genuinely hard.** Komoot has 200+ engineers, 8+ years,
   $200M+ in funding. Garmin Connect has ~20 years of polish. Strava
   Routes had a 5-person team for 3 years before public launch. Solo-dev
   can't out-polish them. Paul's own assessment ("shitty as F") is
   honest.
2. **Heatmap data IS the unique asset.** Nobody else has community
   GPX-derived, ODbL-licensed, France-focused, sport-segmented heatmap.
   Strava's is paywalled + global + sport-agnostic. Komoot's is
   routes-based not GPS-based. OSM has no heatmap. The data is the moat.
3. **Smaller surface = better quality.** Focusing on data pipeline +
   export means we can really polish the K-anonymity, the Valhalla
   matching, the per-sport classification, the time-window filtering.
   Each is a hard problem in itself.
4. **Distribution leverage.** If we ship the heatmap in formats that
   Alpine Quest / OruxMaps / Locus / Gaia users can consume, we're inside
   their existing UX. We don't have to *acquire* users to a new app — we
   acquire users to a *layer* they add to the app they already use. Lower
   friction.
5. **Sustainability fit.** Data pipelines are stable engineering. Route
   editor UIs are bug-rich, UX-sensitive, regression-prone. Solo-dev is
   better suited to the former.
6. **Mission alignment.** `project_mission.md`: "free open heatmap,
   reclaiming community data from paywalled platforms." Pivoting to a
   pure data project is more on-mission than competing on UX.

### Arguments AGAINST the pivot

1. **Loss of "complete app" positioning.** "Open-source Strava
   alternative" is sticky. "Open-source cycling heatmap data feed" is
   accurate but lower-resonance. Marketing harder.
2. **Routing is a path to monetisation if needed later.** Komoot, RWGPS,
   Strava all monetise on routing/training, not data. Pivoting away
   closes that door (which is theoretical anyway given OSS license).
3. **GitHub-PR / fork routes workflow weakens.** Less reason for users to
   create accounts + collaborate on individual routes. Loses some of the
   project's distinctive personality.
4. **The current in-app heatmap viewer is still useful.** Even if we
   don't add new features, the live map at `chemins-communs.fr/map` is
   the primary demo. We don't have to *remove* it. The pivot is about
   *prioritisation*, not deletion.
5. **Cost asymmetry.** Adding export costs ~2 weeks. Removing routing
   would cost months (it's deeply wired). Pivot doesn't have to mean
   "delete the router" — it means "stop investing in routing UX".
6. **Routing might surprise.** With ongoing Layer-1/2/3 testing work
   (`project_session_2026_05_31.md`) the UX could plateau at "good
   enough" without competing with Komoot. Pivot prematurely = give up
   too early.

### Hybrid options

- **A. Ship export, no pivot.** Most conservative. Export is strictly
  additive; doesn't subtract from anything. Strong recommendation.
- **B. Ship export, pivot positioning (not code).** Reframe the
  marketing — "the open community cycling heatmap" as primary, in-app
  routing as secondary demo. Code stays. Effort: rewrite README + 2-3
  pages. Reversible.
- **C. Ship export, pivot positioning + freeze routing investment.**
  Don't touch the WASM router for 3-6 months. Watch export adoption. If
  export grows fast, deepen the data-only product. If it doesn't,
  re-evaluate.
- **D. Ship export + actively deprecate routing UI.** Most aggressive.
  Don't do this — the in-app heatmap viewer is still our best demo.

### Recommendation

**Option B-C combo.** Ship the export (it's positive-sum regardless),
reframe the positioning to lead with data, freeze routing investment for
3-6 months, watch what users actually do. Reversible at every step.

The killer question: **if export adoption is flat after 6 months, what
have we learned?** Either:
- the data isn't valuable enough (need to invest in data quality),
- the distribution isn't working (need to invest in SEO + outreach),
- or the cyclist persona we imagined doesn't exist at scale (need to
  rethink positioning, possibly back to "complete app").

All three are useful signals. None of them are reachable without shipping
the export first.

---

## 8. Appendix — what we already have

### Existing PMTiles pipeline (no change needed)

- `backend/app/jobs/build_pmtiles.py` — builds `heatmap-display.pmtiles` from `heat_edges_display`
- Cloud Run job `internal_artefacts` — rebuilds + uploads to `gs://common-trails-cdn/heatmap-display.pmtiles` hourly
- `frontend/public/heatmap-display.pmtiles` — bind-mounted in dev
- File is **public**, served via Cloud CDN

### Existing GeoJSON endpoint

- `GET /heatmap/trails?sport=&days=&bbox=` returns LineStrings + ODbL metadata
- Already gzip-cached, version-tagged
- K-anonymity enforced (K=2 in prod)

### Existing license + attribution machinery

- `ODBL_LICENSE = "ODbL-1.0"` in `backend/app/api/heatmap.py:207`
- `X-License` header on responses
- Metadata embedded in GeoJSON FeatureCollection

### What's missing for the export feature

- A user-facing download UI (modal + button)
- The MBTiles raster build pipeline
- The KML serialiser
- The async-export queue (Phase 3)
- The per-app onboarding docs
- The `/heatmap-export` landing page + SEO

Roughly 80% of the *backend* logic already exists in some form. The work is
mostly **assembly + UI + docs**, not core engineering.

---

## Footnotes / sources

- Alpine Quest file-based maps: [alpinequest.net/en/help/v2/maps/file-based-select](https://alpinequest.net/en/help/v2/maps/file-based-select)
- OruxMaps manual (v7): [oruxmaps.com/oruxmapsmanual_en.pdf](https://www.oruxmaps.com/oruxmapsmanual_en.pdf)
- Locus Map custom raster: [docs.locusmap.app — MOBAC](http://docs.locusmap.app/doku.php?id=manual%3Aadvanced%3Amap_tools%3Amobac)
- Gaia GPS MBTiles import: [help.gaiagps.com](https://help.gaiagps.com/hc/en-us/articles/360024366293-Import-Maps-Using-MBtiles-or-GeoTiff-into-the-iOS-app)
- Gaia GPS GeoJSON import: [help.gaiagps.com](https://help.gaiagps.com/hc/en-us/articles/360052763513-Import-GPX-KML-KMZ-GeoJSON-or-FIT-Files-on-gaiagps-com)
- PMTiles spec: [docs.protomaps.com/pmtiles](https://docs.protomaps.com/pmtiles)
- MapLibre native PMTiles: [maplibre.org/maplibre-native/android/examples/data/PMTiles](https://maplibre.org/maplibre-native/android/examples/data/PMTiles/)
- MBTiles spec: [github.com/mapbox/mbtiles-spec](https://github.com/mapbox/mbtiles-spec)
- ODbL 1.0: [opendatacommons.org/licenses/odbl/1-0/](https://opendatacommons.org/licenses/odbl/1-0/)

---

*End of PRD. Open for review + decision.*
