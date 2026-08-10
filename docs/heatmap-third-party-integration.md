# Use the CHEMINS COMMUNS community heatmap in your app

**For:** GPX Studio, OruxMaps, Locus Map, Gaia GPS, Alpine Quest, QGIS,
or any other mapping / route-planning tool whose users would benefit
from a community-cycling heat layer alongside their base map.

**License:** [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Free to use commercially, **attribution required**, derived datasets
must stay under ODbL.

**Attribution string** (copy verbatim, link required):

> © [CHEMINS COMMUNS](https://chemins-communs.fr) contributors — ODbL 1.0

---

## Why this exists

CHEMINS COMMUNS users opt in to share their rides; the shared signal is
aggregated, K-anonymised (K=2 — no edge appears unless ≥ 2 distinct
riders have crossed it), and published under ODbL. The result is a
heat layer of where cyclists actually ride — useful in road, gravel,
MTB and off-road planning workflows.

We **don't** offer a proprietary API, gated tiers, or rate limits. The
data is yours under ODbL. If GPX Studio (or any other tool) wants to
ship this as a toggleable layer in their app, this page is the
integration recipe.

## Raster overlay ("calque") — the easy path (gpx.studio / VisuGPX)

The heatmap is published as a **pre-rendered raster XYZ tile pyramid** on public
GCS, so any map tool can add it as a custom overlay layer via **one URL** — no
download, no GPX import.

**TileJSON (recommended — one URL describes the whole tileset):**

```
https://storage.googleapis.com/common-trails-heatmap-prod/raster/tiles.json
```

**Raw XYZ template** (for tools that want the tile URL directly):

```
https://storage.googleapis.com/common-trails-heatmap-prod/raster/{z}/{x}/{y}.png
```

Transparent 256px PNGs, zoom 6–14 (over-zoomed above 14), `access-control-allow-origin: *`.

- **gpx.studio** → map layer settings → *Add custom layer* → paste the XYZ
  template (or the TileJSON URL) → type **raster**, use as **overlay**, set
  opacity. The heatmap draws on top of your basemap; plan your route over it.
- **VisuGPX** → *Fonds de carte* → add a custom tile layer with the XYZ template.
- **Leaflet:** `L.tileLayer('https://storage.googleapis.com/common-trails-heatmap-prod/raster/{z}/{x}/{y}.png', {maxNativeZoom: 14, opacity: 0.7, attribution: '© CHEMINS COMMUNS — ODbL 1.0'}).addTo(map)`
- **OpenLayers / MapLibre:** add an XYZ raster source with the same template.

Attribution (ODbL) is required — the string above.

## Discovery endpoint — start here

A single HTTPS GET returns the current export inventory:

```bash
curl https://chemins-communs.fr/export/heatmap
```

```json
{
  "license": "ODbL-1.0",
  "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
  "attribution": "© CHEMINS COMMUNS contributors — ODbL 1.0",
  "k_anonymity": 2,
  "generated_at": "2026-06-11T16:35:51Z",
  "formats": [
    {
      "format": "pmtiles",
      "mime": "application/vnd.pmtiles",
      "url": "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles",
      "note": "All sports, full region. Use with MapLibre / pmtiles.js."
    },
    {
      "format": "raster",
      "mime": "image/png",
      "url": "https://storage.googleapis.com/common-trails-heatmap-prod/raster/tiles.json",
      "template": "https://storage.googleapis.com/common-trails-heatmap-prod/raster/{z}/{x}/{y}.png",
      "note": "Pre-rendered raster XYZ overlay ('calque'). Add as a custom raster layer in gpx.studio / VisuGPX / QGIS."
    },
    {
      "format": "geojsonl",
      "mime": "application/geo+json",
      "url": "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.geojsonl.gz",
      "note": "Full-region newline-delimited GeoJSON (gzipped), one LineString per aggregated way. Static pre-built artifact."
    }
  ]
}
```

**Everything is a pre-computed static artifact.** There is no on-demand tile
build, no per-request bbox query, no async job — a rebuild (~daily) is the
single source of truth, so a deleted trace propagates out on the next build.
Every `url` points at a public Google Cloud Storage object (CDN-backed, no
auth); those exact paths could change as we evolve the pipeline, so **fetch the
discovery endpoint at integration time rather than hard-coding the GCS path**
(the snippet in Recipe 1 below does this).

The two file downloads also have stable redirect endpoints that 302 to the
canonical GCS object, convenient for `curl -L`:

- `GET /export/heatmap.pmtiles` → the PMTiles archive.
- `GET /export/heatmap.geojsonl` → the gzipped national GeoJSONL.

## Recipe 1 — MapLibre GL (recommended for browser apps including GPX Studio)

PMTiles is the right choice if your app already uses MapLibre or
MapboxGL. Add this protocol + layer once, and the heatmap renders
on top of your existing base map.

```html
<script src="https://unpkg.com/pmtiles@3/dist/pmtiles.js"></script>
<script type="module">
  import maplibregl from 'https://esm.run/maplibre-gl';

  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);

  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://demotiles.maplibre.org/style.json',   // your base map
    center: [3.876, 43.611],
    zoom: 10,
  });

  // Fetch the canonical PMTiles URL from the discovery endpoint — that
  // way you follow the source of truth even if the underlying storage
  // path changes.
  const discovery = await fetch('https://chemins-communs.fr/export/heatmap').then(r => r.json());
  const pmtilesUrl = discovery.formats.find(f => f.format === 'pmtiles').url;

  map.on('load', () => {
    map.addSource('cc-heatmap', {
      type: 'vector',
      url: `pmtiles://${pmtilesUrl}`,
      attribution: '© <a href="https://chemins-communs.fr">CHEMINS COMMUNS</a> contributors — ODbL 1.0',
    });

    map.addLayer({
      id: 'cc-heat-lines',
      type: 'line',
      source: 'cc-heatmap',
      'source-layer': 'trails',                          // see "Source layers" below
      paint: {
        'line-color': [
          'interpolate', ['linear'], ['get', 'heat_score'],
          0.0, '#3a0f5e',                                // dark plum
          0.5, '#e6398b',                                // hot pink
          1.0, '#ff8a3a',                                // orange
        ],
        'line-width': [
          'interpolate', ['linear'], ['zoom'],
          8,  0.5,
          14, 2.5,
        ],
        'line-opacity': 0.85,
      },
    });
  });
</script>
```

**Source layers** in our PMTiles archive:

- `trails` — the main heat lines (LineString) with attributes:
  - `heat_score` (0.0–1.0) — normalized popularity (LN-scaled `user_count`, boosted on primary/secondary roads); use this for colour
  - `user_count` — distinct rider count (K ≥ 2 guaranteed)
  - `pass_count` — busiest sub-segment crossings (MAX across an OSM way's sub-edges, no artificial cap; observed up to ~300)
  - `sport` — `road` / `gravel` / `mtb` / `offroad`
  - `highway_type` — OSM tag (`primary`, `secondary`, `tertiary`, `residential`, `track`, `path`, `cycleway`, …)
- `heat_points` — clustered dots at low zooms (z 6–10), for the "where are people riding" overview before lines become legible. Same attributes minus geometry shape.

Note: there is **no `surface`** attribute in the PMTiles. Paved/unpaved
classification lives elsewhere (the on-demand `/routes/surface_stats`
endpoint, computed per route). For a coarse proxy at PMTiles grain,
`highway_type` works — `track`/`path`/`bridleway` are typically
unpaved.

Filter client-side via MapLibre expressions, e.g.
`['==', ['get', 'sport'], 'gravel']` or
`['in', ['get', 'highway_type'], ['literal', ['track', 'path', 'bridleway']]]`.

## Recipe 2 — static GeoJSONL for offline apps + analysis tools (QGIS, Python, R)

The full-national aggregation is published as a **single gzipped
newline-delimited GeoJSON** file (`.geojsonl.gz`) — one `LineString` Feature
per aggregated way, per line. Download the whole thing once; no bbox, no query
params, no per-request build.

```bash
curl -L -o cc-heatmap.geojsonl.gz https://chemins-communs.fr/export/heatmap.geojsonl
gunzip cc-heatmap.geojsonl.gz          # → cc-heatmap.geojsonl
```

Each line is a standalone GeoJSON Feature:

```json
{"type":"Feature","geometry":{"type":"LineString","coordinates":[[3.87,43.61],…]},"properties":{"sport":"gravel","user_count":4,"pass_count":11,"heat_score":0.62}}
```

Use cases:
- **QGIS / GDAL** — `ogr2ogr cc-heatmap.gpkg cc-heatmap.geojsonl` to convert to
  any vector format, then filter by `sport` / `user_count` and run
  `ST_Length` aggregations or joins against your own network.
- **Python** — read line-by-line (bounded memory) and filter to your area of
  interest client-side.
- **Offline / mobile** — most apps that consume MBTiles can ingest a GeoJSON /
  GeoJSONL source, or convert it locally with `tippecanoe` /
  `ogr2ogr -f MBTILES`.

Same attribute schema as the PMTiles `trails` layer (see Recipe 1). K-anonymity
(K ≥ 2) is already applied — the file contains no below-K edge.

## Versioning — pin a specific snapshot

The mutable `heatmap-display.pmtiles` URL always serves the latest
snapshot, refreshed on every rebuild (~daily). For reproducible
downstream pipelines, the discovery payload also exposes an **immutable**
versioned path:

```bash
GET /export/heatmap            # discovery
# response includes:
{
  "pinned_url": "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display-v{N}.pmtiles",
  "version": "v18493-a7c2e1f0",
  "captured_at": "2026-06-11T03:00:00Z",
  "edge_count": 1923847
}
```

The pinned URL is served with `Cache-Control: public, max-age=31536000, immutable` —
safe to CDN-cache for a year. Use the latest mutable URL for the user-facing
"current heatmap" toggle; use a pinned URL when you want byte-for-byte
reproducibility (e.g. a fixed snapshot bundled with a release of your app).

## K-anonymity, privacy, ODbL

- **K = 2** in production: no edge appears in the export unless at least
  2 distinct riders have ridden it. This is a privacy floor, not a
  popularity threshold — it ensures no individual ride can be reverse-engineered.
- **Strava's proprietary global heatmap is NOT a source.** Scraping or
  importing the public Strava heatmap is forbidden by Strava's TOS and
  we don't do it. What CAN feed our heatmap: a CHEMINS COMMUNS user who
  has connected their Strava account via the official OAuth flow has
  their own rides synced and aggregated into our community signal,
  subject to the same K ≥ 2 + opt-in privacy floor as GPX uploads.
  Translated: the data is from real riders who opted in, not lifted
  from someone else's heatmap.
- **ODbL "share-alike"**: if you build a derived dataset (e.g. filter the
  heatmap by intersection with your own track database, then publish
  the result), the derivative must also be ODbL. Using the heatmap as a
  *display layer* in your app does NOT trigger share-alike — that's
  "produced work", which only needs attribution.
- **Attribution placement**: a visible attribution in the map UI's
  attribution control is sufficient. The string above is the canonical form.

## Stability + change policy

- The export surface is **pre-computed static artifacts only** — the PMTiles,
  the raster calque (`raster/tiles.json` + `{z}/{x}/{y}.png`), and the national
  GeoJSONL. There is no on-demand / async build endpoint.
- The discovery JSON schema is additive-only — we add fields, never
  remove or rename.
- The PMTiles URL is stable: `heatmap-display.pmtiles` (mutable, latest)
  and `heatmap-display-v{N}.pmtiles` (immutable, pinned snapshot).
- The `trails` source-layer / GeoJSONL attribute schema is stable. New
  attribute fields may be added; existing ones won't be renamed or repurposed.
- The redirect endpoints `/export/heatmap.pmtiles` and
  `/export/heatmap.geojsonl` are stable.

If you're shipping this in a real product and want a heads-up before
breaking changes (we don't plan any, but…), open an issue at
[github.com/polomarcus/common-trails](https://github.com/polomarcus/common-trails)
or email Paul directly (see the repo's `README.md`).

## Partnership / outreach

If you maintain a route-planning app and want to add CHEMINS COMMUNS as
a toggleable layer, we'd love to hear from you — open an issue or a PR
on your side referencing this doc, and we'll help with whatever
integration questions come up (palette tuning, attribution wording,
specific MapLibre style snippets, etc.).

**The community heatmap exists because riders share their data. The
more apps integrate it, the more value the community gets back from
that share.** That's the whole point.

---

*Last updated: 2026-08-07 (pre-computed-only export surface). For implementation details (PMTiles build
pipeline, GCS bucket setup, ODbL boundary cases), see
[`docs/heatmap-export-prd.md`](heatmap-export-prd.md) and
[`docs/heatmap-pipeline.md`](heatmap-pipeline.md).*
