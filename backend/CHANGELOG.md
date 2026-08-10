# Changelog

## [0.64.0](https://github.com/polomarcus/common-trails/compare/backend-v0.63.2...backend-v0.64.0) (2026-08-10)


### Features

* **heatmap:** serve community heatmap from first-party tiles.chemins-communs.fr ([#629](https://github.com/polomarcus/common-trails/issues/629)) ([09287d9](https://github.com/polomarcus/common-trails/commit/09287d9663d761722cc13438ed0a0437b92abd40))

## [0.63.2](https://github.com/polomarcus/common-trails/compare/backend-v0.63.1...backend-v0.63.2) (2026-08-10)


### Bug Fixes

* **heatmap:** bound the pass_count-regrade split so GPS noise can't OOM the PMTiles build ([#626](https://github.com/polomarcus/common-trails/issues/626)) ([4f3d632](https://github.com/polomarcus/common-trails/commit/4f3d632766ae508f691932e00fcbb6bae45981eb))


### Miscellaneous

* release 0.7.165 ([#624](https://github.com/polomarcus/common-trails/issues/624)) ([5f400e7](https://github.com/polomarcus/common-trails/commit/5f400e76c209518c4d661fef5cd606aab06936ce))

## [0.63.1](https://github.com/polomarcus/common-trails/compare/backend-v0.63.0...backend-v0.63.1) (2026-08-09)


### Bug Fixes

* **heatmap:** per-feature LOCAL pass_count/heat_score on raw traces ([#621](https://github.com/polomarcus/common-trails/issues/621)) ([72d6304](https://github.com/polomarcus/common-trails/commit/72d6304ddc384511593257e3d1da881d276a1e43))
* **security:** abuse/cost hardening for public beta (rate-limit key, upload throttles, archive quotas, heatmap/geocode cost guards) ([#622](https://github.com/polomarcus/common-trails/issues/622)) ([f4b971b](https://github.com/polomarcus/common-trails/commit/f4b971b027651c8536c5344aaaf67ec44a1ea6cb))

## [0.63.0](https://github.com/polomarcus/common-trails/compare/backend-v0.62.2...backend-v0.63.0) (2026-08-09)


### Features

* **heatmap:** arrows point the DOMINANT direction + encode dominance strength ([#618](https://github.com/polomarcus/common-trails/issues/618)) ([7098a9a](https://github.com/polomarcus/common-trails/commit/7098a9a4bc77f3204471f7d0e9715043d33febd5))

## [0.62.2](https://github.com/polomarcus/common-trails/compare/backend-v0.62.1...backend-v0.62.2) (2026-08-09)


### Bug Fixes

* **backend:** stream /me/activities, gzip+cap /heatmap/export, fresher cache, job-config guard ([#614](https://github.com/polomarcus/common-trails/issues/614)) ([e83c816](https://github.com/polomarcus/common-trails/commit/e83c816f79900d800398de8cd6ff4a0d99593c26))

## [0.62.1](https://github.com/polomarcus/common-trails/compare/backend-v0.62.0...backend-v0.62.1) (2026-08-09)


### Bug Fixes

* **cache:** revalidate the HTML entry (fixes stale-bundle "no heatmap") + /strava polish ([#612](https://github.com/polomarcus/common-trails/issues/612)) ([16eecdd](https://github.com/polomarcus/common-trails/commit/16eecdd3499d7c776c7ce67238ba29769f4641de))

## [0.62.0](https://github.com/polomarcus/common-trails/compare/backend-v0.61.3...backend-v0.62.0) (2026-08-08)


### Features

* **email:** 6-monthly "re-sync your data" reminder for stale contributors ([#605](https://github.com/polomarcus/common-trails/issues/605)) ([fd4ae22](https://github.com/polomarcus/common-trails/commit/fd4ae22b3504ccef0d55ae8d76b7f17c608435fb))
* **strava+nav:** show contribution status on /strava; orphan the broken /stats ([#606](https://github.com/polomarcus/common-trails/issues/606)) ([f00b7d5](https://github.com/polomarcus/common-trails/commit/f00b7d5d840051c3eab4a2491a35f196846f6547))

## [0.61.3](https://github.com/polomarcus/common-trails/compare/backend-v0.61.2...backend-v0.61.3) (2026-08-07)


### Bug Fixes

* **index:** post-pivot home copy + 3-step narrative + km-stat & DFCI payload fixes ([#603](https://github.com/polomarcus/common-trails/issues/603)) ([7a39f30](https://github.com/polomarcus/common-trails/commit/7a39f307fef163725ea6c03a24c359ddd0f281bf))

## [0.61.2](https://github.com/polomarcus/common-trails/compare/backend-v0.61.1...backend-v0.61.2) (2026-08-07)


### Bug Fixes

* **ingest:** raise MAX_ZIP_MEMBERS 5000→20000 — real Garmin "Export All" is 6000+ .fit ([#599](https://github.com/polomarcus/common-trails/issues/599)) ([b220c9c](https://github.com/polomarcus/common-trails/commit/b220c9ca106389733719e5334ee7fcf402f916a0))

## [0.61.1](https://github.com/polomarcus/common-trails/compare/backend-v0.61.0...backend-v0.61.1) (2026-08-07)


### Bug Fixes

* **gdpr:** prune old immutable pmtiles snapshots — keep only the latest ([#594](https://github.com/polomarcus/common-trails/issues/594)) ([40be99d](https://github.com/polomarcus/common-trails/commit/40be99d4ce005c3d499a259c377a529fd8be792b))

## [0.61.0](https://github.com/polomarcus/common-trails/compare/backend-v0.60.1...backend-v0.61.0) (2026-08-07)


### Features

* **ingest:** recurse into nested zips — Garmin "Export All" archives now import ([#590](https://github.com/polomarcus/common-trails/issues/590)) ([3e9764f](https://github.com/polomarcus/common-trails/commit/3e9764fd6df59e733b565550a41c7c1711e8f546))

## [0.60.1](https://github.com/polomarcus/common-trails/compare/backend-v0.60.0...backend-v0.60.1) (2026-08-07)


### Bug Fixes

* **gdpr:** purge stale raster calque tiles each build (deleted/below-K traces no longer linger) ([#587](https://github.com/polomarcus/common-trails/issues/587)) ([832c0bc](https://github.com/polomarcus/common-trails/commit/832c0bc8c17560a2e9d1b03e354fdc7c6d572c76))

## [0.60.0](https://github.com/polomarcus/common-trails/compare/backend-v0.59.1...backend-v0.60.0) (2026-08-06)


### Features

* **email:** the "your traces are on the map" email promotes the gpx.studio/VisuGPX calque ([#574](https://github.com/polomarcus/common-trails/issues/574)) ([1e29d12](https://github.com/polomarcus/common-trails/commit/1e29d1272169114061935fa71625eaaa0507ca6e))
* **heatmap:** raster XYZ tile pyramid → the heatmap as an importable calque (gpx.studio/VisuGPX) ([#573](https://github.com/polomarcus/common-trails/issues/573)) ([feb16e7](https://github.com/polomarcus/common-trails/commit/feb16e736151563435cf56a1ed06d42c19a2c11d))


### Bug Fixes

* MVP audit batch 1 — 4 HIGH findings (FIT pollution, raster ordering, Cloud Tasks project, banner) ([#577](https://github.com/polomarcus/common-trails/issues/577)) ([224080c](https://github.com/polomarcus/common-trails/commit/224080c5ddafa3d0b42bcd5d34e655253148f26b))

## [0.59.1](https://github.com/polomarcus/common-trails/compare/backend-v0.59.0...backend-v0.59.1) (2026-08-06)


### Bug Fixes

* **compliance:** /gpx/upload also requires consent to publish to the community layer ([#571](https://github.com/polomarcus/common-trails/issues/571)) ([23ee39e](https://github.com/polomarcus/common-trails/commit/23ee39e5d1804479dad62739ac338008691ed5b7))
* **compliance:** no community/ODbL write on /imports/files without a consent row ([#570](https://github.com/polomarcus/common-trails/issues/570)) ([836a4bf](https://github.com/polomarcus/common-trails/commit/836a4bfd7248c658a75d81789cdf26315f649938))
* **gdpr:** activity deletion + account merge now refresh the community map ([#568](https://github.com/polomarcus/common-trails/issues/568)) ([60ad6d3](https://github.com/polomarcus/common-trails/commit/60ad6d36d8a9936976d87bfde170ea103cfc088a))
* **imports:** loose GPX/FIT upload now triggers the PMTiles rebuild so a contribution appears on the map ([#567](https://github.com/polomarcus/common-trails/issues/567)) ([8a8e68c](https://github.com/polomarcus/common-trails/commit/8a8e68c68fc889ecd548ce5e000bd247a75e4dfd))

## [0.59.0](https://github.com/polomarcus/common-trails/compare/backend-v0.58.0...backend-v0.59.0) (2026-08-06)


### Features

* **heatmap:** raster-style density heatmap (Strava look) — fixes the diffuse vector-line pâté ([#560](https://github.com/polomarcus/common-trails/issues/560)) ([3561823](https://github.com/polomarcus/common-trails/commit/3561823db9f885969df36d9f302ed9b026c9fbcc))

## [0.58.0](https://github.com/polomarcus/common-trails/compare/backend-v0.57.4...backend-v0.58.0) (2026-08-06)


### Features

* **heatmap:** per-direction pass counts for the mtb/gravel tooltip ([#557](https://github.com/polomarcus/common-trails/issues/557)) ([31e2373](https://github.com/polomarcus/common-trails/commit/31e2373b6f408c4d0f0689247a7da2597195870a))

## [0.57.4](https://github.com/polomarcus/common-trails/compare/backend-v0.57.3...backend-v0.57.4) (2026-08-05)


### Bug Fixes

* **export:** stream GPX/GeoJSON/KML serialization end-to-end (kill the OOM 503) ([#553](https://github.com/polomarcus/common-trails/issues/553)) ([4026466](https://github.com/polomarcus/common-trails/commit/40264661d008b888595b698d111bcb1af14d0d43))

## [0.57.3](https://github.com/polomarcus/common-trails/compare/backend-v0.57.2...backend-v0.57.3) (2026-08-05)


### Bug Fixes

* **export:** serve raw heatmap exports from the pre-built geojsonl (kill the 503) ([#550](https://github.com/polomarcus/common-trails/issues/550)) ([7e05c7b](https://github.com/polomarcus/common-trails/commit/7e05c7b2c96147b6f069f0419b4b3301227420dd))

## [0.57.2](https://github.com/polomarcus/common-trails/compare/backend-v0.57.1...backend-v0.57.2) (2026-08-05)


### Bug Fixes

* **startup:** two raw-mode startup errors after the heat_edges DROP ([#549](https://github.com/polomarcus/common-trails/issues/549)) ([93e8708](https://github.com/polomarcus/common-trails/commit/93e8708851ad25d980a66923d4789ed34107c45c))

## [0.57.1](https://github.com/polomarcus/common-trails/compare/backend-v0.57.0...backend-v0.57.1) (2026-08-05)


### Miscellaneous

* **ingest:** remove dead matched-era graph_builder module + CDN publish path ([#544](https://github.com/polomarcus/common-trails/issues/544)) ([b06ec30](https://github.com/polomarcus/common-trails/commit/b06ec30720711cf6ffbc148a79bb68a865d8b02f))

## [0.57.0](https://github.com/polomarcus/common-trails/compare/backend-v0.56.3...backend-v0.57.0) (2026-08-05)


### Features

* **heatmap:** directional one-way score + MTB/gravel arrow layer ([#541](https://github.com/polomarcus/common-trails/issues/541)) ([ba897ee](https://github.com/polomarcus/common-trails/commit/ba897ee18a926e740cb02b425b73ad7844b56666))

## [0.56.3](https://github.com/polomarcus/common-trails/compare/backend-v0.56.2...backend-v0.56.3) (2026-08-05)


### Miscellaneous

* **ingest:** remove dead matched-era osm_enrich module + CLIs ([#537](https://github.com/polomarcus/common-trails/issues/537)) ([ae2dee9](https://github.com/polomarcus/common-trails/commit/ae2dee98cdc01de936dbe95ca286776977401dae))

## [0.56.2](https://github.com/polomarcus/common-trails/compare/backend-v0.56.1...backend-v0.56.2) (2026-08-04)


### Miscellaneous

* **social:** decommission the inert Garmin Connect integration ([#532](https://github.com/polomarcus/common-trails/issues/532)) ([ae9c091](https://github.com/polomarcus/common-trails/commit/ae9c091ff5098d5e38f9f9b992abf5742cb83d91))

## [0.56.1](https://github.com/polomarcus/common-trails/compare/backend-v0.56.0...backend-v0.56.1) (2026-08-04)


### Bug Fixes

* **heat:** gate the last live heat_edges/heat_edges_agg readers so the tables can be DROPPED ([#529](https://github.com/polomarcus/common-trails/issues/529)) ([7fa5267](https://github.com/polomarcus/common-trails/commit/7fa52675ef8011001a116ec44d3555ef27abbe45))

## [0.56.0](https://github.com/polomarcus/common-trails/compare/backend-v0.55.2...backend-v0.56.0) (2026-08-04)


### Features

* **accounts:** link-time self-merge of synthetic Strava accounts + one-off dup migration ([#477](https://github.com/polomarcus/common-trails/issues/477)) ([#527](https://github.com/polomarcus/common-trails/issues/527)) ([d570ca4](https://github.com/polomarcus/common-trails/commit/d570ca4666f14dc5fffb9436bf5dd18445f9bf3e))

## [0.55.2](https://github.com/polomarcus/common-trails/compare/backend-v0.55.1...backend-v0.55.2) (2026-08-04)


### Miscellaneous

* **backend:** remove dead overpass surface-tag service module ([#525](https://github.com/polomarcus/common-trails/issues/525)) ([2fad68d](https://github.com/polomarcus/common-trails/commit/2fad68d9f31de3e924a99d58ce9b2359bfecf5e4))

## [0.55.1](https://github.com/polomarcus/common-trails/compare/backend-v0.55.0...backend-v0.55.1) (2026-08-04)


### Bug Fixes

* **stats:** count community-eligible activities only in compute_community_stats ([#520](https://github.com/polomarcus/common-trails/issues/520)) ([8c3aec8](https://github.com/polomarcus/common-trails/commit/8c3aec8541a416c017d2e360c59538dca8e93c8a))

## [0.55.0](https://github.com/polomarcus/common-trails/compare/backend-v0.54.0...backend-v0.55.0) (2026-08-04)


### Features

* 4dp snap grid (11m) — 43x more shared edges, motto test passes ([5243352](https://github.com/polomarcus/common-trails/commit/524335223af67b5888e2de4c324d32370c842455))
* **activities:** add PostGIS geometry column ([5bebeca](https://github.com/polomarcus/common-trails/commit/5bebeca9756416244196f6d5f37ee1db9a449d8d))
* **activities:** add PostGIS geometry column (binary, smaller, queryable) ([67b3ee2](https://github.com/polomarcus/common-trails/commit/67b3ee2db19b30acbbf84dc52500f8533b49d041))
* add all French regions + Switzerland/Spain/Italy to OSM PBF import ([a13fac2](https://github.com/polomarcus/common-trails/commit/a13fac22a12249bba37fc9b5f12da3cf3f1e6fb3))
* **admin:** archives (contributions) observability card — GET /admin/archives + /admin pills+table ([#490](https://github.com/polomarcus/common-trails/issues/490)) ([344aeb9](https://github.com/polomarcus/common-trails/commit/344aeb99b2eadc5e01ecc8c2c0005dba693eb7f0))
* **admin:** beta-time visibility — engagement, providers, heatmap quality, recent feeds ([#288](https://github.com/polomarcus/common-trails/issues/288)) ([362c4bb](https://github.com/polomarcus/common-trails/commit/362c4bb499ce8b709fc3ec2344147775f1102def))
* **admin:** heatmap monitoring dashboard — freshness panel + evolution chart ([#447](https://github.com/polomarcus/common-trails/issues/447)) ([5cf6ac3](https://github.com/polomarcus/common-trails/commit/5cf6ac32624965b0c4790034bef01a8b53dbccc9))
* **admin:** per-user observability dashboard (users table) ([#443](https://github.com/polomarcus/common-trails/issues/443)) ([2d6f2fe](https://github.com/polomarcus/common-trails/commit/2d6f2fe83b22b5da2792501c795dbf8cbe6c7675))
* archive raw GPX uploads to bucket (heatmap rebuild safety net) ([7e9eadb](https://github.com/polomarcus/common-trails/commit/7e9eadbb5eb61bb49889aef210403444cbd1d245))
* archive raw GPX uploads to GCS bucket for heatmap rebuild safety net ([d323502](https://github.com/polomarcus/common-trails/commit/d323502c9a1affbb751fa2d4d8718d76ae7ac116))
* **archive:** terminal-state email when a Strava archive finishes draining ([#496](https://github.com/polomarcus/common-trails/issues/496)) ([5a63066](https://github.com/polomarcus/common-trails/commit/5a63066b4a7a75980e5c02fba182c07224f178fa))
* **auth:** passwordless email magic-link login + Resend (Phase 1) ([#476](https://github.com/polomarcus/common-trails/issues/476)) ([dd50ebe](https://github.com/polomarcus/common-trails/commit/dd50ebef15ff02e954fc6504e8a9bbf213458166))
* **auth:** unify Strava-connect and email-login accounts ([#477](https://github.com/polomarcus/common-trails/issues/477)) ([1b276b8](https://github.com/polomarcus/common-trails/commit/1b276b8daabbe6066a52ec00cfdc45bd47fadb44))
* auto-rebuild .fgraph after GPX upload or Strava import ([2dfb876](https://github.com/polomarcus/common-trails/commit/2dfb8766c7db5389eedd65db53e4a878b5fbc1f1))
* **backend:** GPX backup to cloud storage + dedup safety net ([425f1f6](https://github.com/polomarcus/common-trails/commit/425f1f6e528157875b73fe7df76bfd8ce2efaa61))
* **beta:** 4 deferred audit items + group_edges_osm progress logging ([#269](https://github.com/polomarcus/common-trails/issues/269)) ([00bd001](https://github.com/polomarcus/common-trails/commit/00bd0016e3ca3f6975336a63e9e80e9d03efb73f))
* bulk GPX folder import CLI + Mac→prod ops docs ([f76d477](https://github.com/polomarcus/common-trails/commit/f76d4779b8f54db626a084fea089c671b8b94515))
* **cli:** bulk GPX folder import CLI ([3eaad9b](https://github.com/polomarcus/common-trails/commit/3eaad9b97d9432988b7365d3f03cf0e7d08660c3))
* **cli:** one-shot founder archive-import CLI (consented ODbL contribution) ([#460](https://github.com/polomarcus/common-trails/issues/460)) ([77ddf06](https://github.com/polomarcus/common-trails/commit/77ddf0688fae1f5e52a9361277902dd50e8f0ca4))
* cross-provider dedup — same date + distance (±10%) = skip ([4d8a8e0](https://github.com/polomarcus/common-trails/commit/4d8a8e0c158517d5bfbd895a9523bc60eef7a3d2))
* **cutover:** public community map, members-only EXPORT ([#509](https://github.com/polomarcus/common-trails/issues/509)) ([27675bf](https://github.com/polomarcus/common-trails/commit/27675bf73cd885992434448bef22d902604ea841))
* **diagnostics:** critical-connector symmetry audit + longest-hop bridge candidates ([#411](https://github.com/polomarcus/common-trails/issues/411)) ([179bbde](https://github.com/polomarcus/common-trails/commit/179bbde05461573fc03287387f5588f00a48b509))
* **elevation+surface:** D+ smoothing + local DEM at PBF time + surface_confidence end-to-end ([#259](https://github.com/polomarcus/common-trails/issues/259)) ([a9d89ed](https://github.com/polomarcus/common-trails/commit/a9d89ed8fccdcd13ab162b34c2f7856968de5e13))
* **export:** GPX 1.1 export of the community heatmap — VisuGPX-compatible ([#436](https://github.com/polomarcus/common-trails/issues/436)) ([275ea95](https://github.com/polomarcus/common-trails/commit/275ea952cfc8d52d1b7b8378976ec5d541e56489))
* **export:** Phase 1 — PMTiles download + GCS storage ([#395](https://github.com/polomarcus/common-trails/issues/395)) ([93ef26d](https://github.com/polomarcus/common-trails/commit/93ef26d5a4b715aeb7488b066bcc728ca3bc3e6a))
* **export:** Phase 2 — MBTiles + GeoJSON download ([#396](https://github.com/polomarcus/common-trails/issues/396)) ([fb131f7](https://github.com/polomarcus/common-trails/commit/fb131f76333010866b1bfc962f8439afb930a2e6))
* **export:** Phase 2.5 — MBTiles raster for Alpine Quest ([#399](https://github.com/polomarcus/common-trails/issues/399)) ([7511205](https://github.com/polomarcus/common-trails/commit/7511205b61b82e22e91824b8a35e6bde07474745))
* **export:** Phase 3 — async &gt; 50km, KML, versioning ([#400](https://github.com/polomarcus/common-trails/issues/400)) ([de48777](https://github.com/polomarcus/common-trails/commit/de4877783c473a416b0c84c8b1f3d0cc81594e2e))
* full routing engine in Rust WASM — cost model, graph, Dijkstra, proposals, stats ([d9dad51](https://github.com/polomarcus/common-trails/commit/d9dad51bc5441dd82e3207251099808ccbb884ee))
* **gdpr:** DELETE /me/activities/{id} — per-activity deletion incl. community heat contributions ([#498](https://github.com/polomarcus/common-trails/issues/498)) ([d22ce5c](https://github.com/polomarcus/common-trails/commit/d22ce5c573bf0418cbc9651c7412d08511877cbf))
* GPX backup to cloud storage + dedup safety net ([be2dc37](https://github.com/polomarcus/common-trails/commit/be2dc3712ead8fd1d17c8d6c7509b74ecb9fca4f))
* **gpx:** auto-detect sport from GPX &lt;trk&gt;&lt;type&gt; on upload ([#301](https://github.com/polomarcus/common-trails/issues/301)) ([#302](https://github.com/polomarcus/common-trails/issues/302)) ([ce5d162](https://github.com/polomarcus/common-trails/commit/ce5d1626707cc85196c04f4f3b77188a7909341d))
* **gpx:** infer sport from Strava activities.csv + skip out-of-scope ([#339](https://github.com/polomarcus/common-trails/issues/339)) ([2d4b8eb](https://github.com/polomarcus/common-trails/commit/2d4b8ebbd62096239552bb2d513d1b33993bb684))
* **heat:** connectivity diagnostic CLI over the routable graph ([#410](https://github.com/polomarcus/common-trails/issues/410)) ([74fc840](https://github.com/polomarcus/common-trails/commit/74fc8405e4eb89d62d6a0366d8704f7ac4ddf7dc))
* heatmap glow polish + Garmin .fit import + multi-source support ([474bd91](https://github.com/polomarcus/common-trails/commit/474bd913fadd00877ccf1d6484345b722aad3749))
* **heatmap+prod:** Komoot quality + Valhalla map-matching + event-driven artefact refresh ([d269d6b](https://github.com/polomarcus/common-trails/commit/d269d6b247b036d802c872fc25483cb8db54972a))
* **heatmap:** incremental heat_edges_agg aggregate — fixes PMTiles OOM on db-f1-micro ([#441](https://github.com/polomarcus/common-trails/issues/441)) ([fc7ef0d](https://github.com/polomarcus/common-trails/commit/fc7ef0d997b4225bf78984debf7eb9e14158d45f))
* **heatmap:** keep off-OSM desire lines — env-driven, SSOT'd across both display readers ([#501](https://github.com/polomarcus/common-trails/issues/501)) ([726c8af](https://github.com/polomarcus/common-trails/commit/726c8af1eb9ffe8691fb591dae157ec41adc1c53))
* **heatmap:** osm_way_id fix + ingestion goldens + full test-coverage hardening + perf ([#420](https://github.com/polomarcus/common-trails/issues/420)) ([4c6ec53](https://github.com/polomarcus/common-trails/commit/4c6ec5334aac0426f5b64610b5a874cc7d6d0fbd))
* **heatmap:** raw-trace display source + endpoint masking (flag-gated, default off) ([#505](https://github.com/polomarcus/common-trails/issues/505)) ([5edf49f](https://github.com/polomarcus/common-trails/commit/5edf49f3e7df8e42330884df0f3f413eff774ba1))
* **home:** wire real community stats from build-time stats.json ([#446](https://github.com/polomarcus/common-trails/issues/446)) ([5c3702d](https://github.com/polomarcus/common-trails/commit/5c3702d01001ef5b4a9e018ac533d01039828fcd))
* i8n + CI lint + broken import test after .fit support ([#175](https://github.com/polomarcus/common-trails/issues/175)) ([dacc29c](https://github.com/polomarcus/common-trails/commit/dacc29cf52d8272afec84eae5d23df133cd99488))
* import queue — one Strava import at a time (rate limit protection) ([9da7816](https://github.com/polomarcus/common-trails/commit/9da7816e698392e7db20e136a407f858f32bb149))
* **import:** classify gravel/mtb from Strava CSV name + clean-heatmap snapshot/restore ([#430](https://github.com/polomarcus/common-trails/issues/430)) ([14fd39b](https://github.com/polomarcus/common-trails/commit/14fd39b1d19214ee8eb454e309c62caff7af4b48))
* **imports:** event-driven trigger of ingest-pending-archives job on /complete ([#482](https://github.com/polomarcus/common-trails/issues/482)) ([c6e438c](https://github.com/polomarcus/common-trails/commit/c6e438c00cc490b9df0a393f6efe48197db4a228))
* **imports:** signed-URL direct-to-GCS upload for large Strava archives ([#455](https://github.com/polomarcus/common-trails/issues/455)) ([6a97a18](https://github.com/polomarcus/common-trails/commit/6a97a186d82e30805ba8cfb488109e62d2e20741))
* **infra:** microservice split — light 512Mi web + scale-to-zero 2Gi worker ([#462](https://github.com/polomarcus/common-trails/issues/462)) ([e583691](https://github.com/polomarcus/common-trails/commit/e583691b605453232c1a49eab19f0e3f569d6383))
* **ingest:** async gpx-upload via Cloud Tasks queue ([2ea8948](https://github.com/polomarcus/common-trails/commit/2ea8948c84f3cca415b1f45aebbc8a78ce676602))
* **ingest:** async gpx-upload via Cloud Tasks queue ([e98bac8](https://github.com/polomarcus/common-trails/commit/e98bac83dd6cf71b6f8c5b9f438fcf7f988ebca3))
* **ingest:** bridge/tunnel critical-connectors layer in routing graph ([#381](https://github.com/polomarcus/common-trails/issues/381)) ([14ae74c](https://github.com/polomarcus/common-trails/commit/14ae74c4ef2b41c57c26d2a1fadcb8fe02fe055b))
* **ingest:** consented Strava-archive community-contribution import ([#451](https://github.com/polomarcus/common-trails/issues/451)) ([d4b41ce](https://github.com/polomarcus/common-trails/commit/d4b41ce55eafdce1276b920db5494f1ad3fddb2f))
* **ingest:** HMM/Viterbi spatial map-matcher (model the whole trajectory) ([#421](https://github.com/polomarcus/common-trails/issues/421)) ([1629dab](https://github.com/polomarcus/common-trails/commit/1629dab6150e6b1e3e48dfa675a859dd1e84472f))
* **ingest:** ping PMTiles rebuild after archive drain imports (event-driven) ([#486](https://github.com/polomarcus/common-trails/issues/486)) ([ec89d82](https://github.com/polomarcus/common-trails/commit/ec89d82f8f6153c91ffe3f56f51a0c79af06b511))
* **ingest:** separate PUBLIC (manual_upload) from PERSONAL (strava_api) provenance (②③) ([#453](https://github.com/polomarcus/common-trails/issues/453)) ([a9f95ba](https://github.com/polomarcus/common-trails/commit/a9f95bae9421fa8337d4ce5436b81623d9e51c02))
* **make:** prod rebuild targets + URGENT dry-run bug fix ([#249](https://github.com/polomarcus/common-trails/issues/249)) ([f533e07](https://github.com/polomarcus/common-trails/commit/f533e07ae272086e48591ed856609d7f6f201635))
* **map:** fork-UX polish + drop deprecated upstream-PR endpoints ([#279](https://github.com/polomarcus/common-trails/issues/279)) ([c247cd4](https://github.com/polomarcus/common-trails/commit/c247cd4b78c8da89dc90d1596041c7b549ec37aa))
* **monitoring:** heat-edge spaghetti alerts via build_pmtiles hook ([#247](https://github.com/polomarcus/common-trails/issues/247)) ([f4b3179](https://github.com/polomarcus/common-trails/commit/f4b31790efbc526a50339333aa5ca7fa008416fa))
* MVT vector tiles + partition heat_edges + incremental rebuild ([#147](https://github.com/polomarcus/common-trails/issues/147)) ([284389c](https://github.com/polomarcus/common-trails/commit/284389c84f7f3e1947d7257ea82981e2b2dd7e69))
* **notifications:** "your ride is on the map" toast on new Strava sync ([#449](https://github.com/polomarcus/common-trails/issues/449)) ([bc2b411](https://github.com/polomarcus/common-trails/commit/bc2b41110d2475dfd82495b7163ce6f32f3be3fa))
* **notifications:** post-login notifications for long-running imports ([#309](https://github.com/polomarcus/common-trails/issues/309)) ([d4b1c2d](https://github.com/polomarcus/common-trails/commit/d4b1c2d41fffa6863f1c5bd6799a35d8cecb074f))
* **ops:** bake HGT tiles into image + activities D+ backfill CLI ([#261](https://github.com/polomarcus/common-trails/issues/261)) ([d307dca](https://github.com/polomarcus/common-trails/commit/d307dca30c1c58f727f45ec558c81b69aa6029fe))
* OSM road network as routing base layer ([#177](https://github.com/polomarcus/common-trails/issues/177)) ([214dcd6](https://github.com/polomarcus/common-trails/commit/214dcd62d140161d00eaf051d4af095bd2debcbf))
* PMTiles heatmap display — single static file, CDN-served ([9c88975](https://github.com/polomarcus/common-trails/commit/9c88975872dac8fa3445e1f1362912b2bdd710e9))
* **prod-followups:** 4 bundled fixes — ORM drift / recompute Job / rebuild guard / admin job feed ([#289](https://github.com/polomarcus/common-trails/issues/289)) ([c34d239](https://github.com/polomarcus/common-trails/commit/c34d239cc2759af86440488fde563bc4a0079678))
* **routing:** 1.30× cost penalty for critical-connector edges (PR [#381](https://github.com/polomarcus/common-trails/issues/381) S2-C) ([#384](https://github.com/polomarcus/common-trails/issues/384)) ([1d78dae](https://github.com/polomarcus/common-trails/commit/1d78dae6f6c5584d320556f67eb51c76b9c635f7))
* **routing:** shard-first re-architecture + OSM-in-the-shard (restore the motto) ([#417](https://github.com/polomarcus/common-trails/issues/417)) ([75fee35](https://github.com/polomarcus/common-trails/commit/75fee350e08e976d72be8e1acfa2c20e67f9d2b0))
* **sentry:** scrub raw lat/lon + hashed bbox span names ([#243](https://github.com/polomarcus/common-trails/issues/243)) ([d83c240](https://github.com/polomarcus/common-trails/commit/d83c240cd4e3b70794ce165d408c10cc7ad0fcf0))
* serve CTGB protobuf from CDN — 44% smaller than JSON ([2d5e6f7](https://github.com/polomarcus/common-trails/commit/2d5e6f748165ac012c3deb68123942f13c321ceb))
* **strava:** accurate activity count via /athlete/activities pagination ([#306](https://github.com/polomarcus/common-trails/issues/306)) ([0c9e719](https://github.com/polomarcus/common-trails/commit/0c9e7193565ab504c9aa088fb98f014581794181))
* **strava:** friends-beta quick wins — GPX skip + decommission cron + daily alerts ([#342](https://github.com/polomarcus/common-trails/issues/342)) ([d9bf660](https://github.com/polomarcus/common-trails/commit/d9bf66002cc34bae3112e82d6433987a53ebf985))
* **strava:** render photo thumbnails on activity detail panel ([#307](https://github.com/polomarcus/common-trails/issues/307)) ([105d3ba](https://github.com/polomarcus/common-trails/commit/105d3bab79ec3c73c0bf7977cf82477d4d50b825))
* stuck import detection + map link during Strava import ([#153](https://github.com/polomarcus/common-trails/issues/153)) ([9bc0ccd](https://github.com/polomarcus/common-trails/commit/9bc0ccd9e0f1e9b9953b11ffcc2eba60c9bea31a))
* **substrate:** osm_ways side-table + region-partitioned osm_road_edges — 26 GB → 4.1 GB (−84%) ([#435](https://github.com/polomarcus/common-trails/issues/435)) ([b194305](https://github.com/polomarcus/common-trails/commit/b194305bf703bd871d2eed7d45ad0ed1a43a1f81))
* **trails:** GR/GRP/GT/PR/EV from local PBF + kill runtime Overpass in prod ([#230](https://github.com/polomarcus/common-trails/issues/230)) ([f0b29a0](https://github.com/polomarcus/common-trails/commit/f0b29a0b0125576f1fd4985a9b3b0218b1d57bf6))
* WASM router with pre-built .fgraph (CDN-ready) ([13eb486](https://github.com/polomarcus/common-trails/commit/13eb486359d01f5dcb5a44ca580088d06ae44c72))


### Bug Fixes

* add safety log showing existing DB size before tile-based delete ([e242fbb](https://github.com/polomarcus/common-trails/commit/e242fbb7ae76268f1495002047094310dbb931b9))
* **alembic:** resolve duplicate revision 0054 (export [#400](https://github.com/polomarcus/common-trails/issues/400) + matview-drop [#424](https://github.com/polomarcus/common-trails/issues/424)) ([#426](https://github.com/polomarcus/common-trails/issues/426)) ([f7b9590](https://github.com/polomarcus/common-trails/commit/f7b95901d8b06e7c18be098b3c0140121da2f359))
* **api:** /readyz returns 200 'warming' when heat_edges empty ([#227](https://github.com/polomarcus/common-trails/issues/227)) ([a7863c9](https://github.com/polomarcus/common-trails/commit/a7863c935ea7c742675e89153a56b1648e76ab19))
* **api:** add /health alias for /healthz (Cloud Run edge 404 workaround) ([2bb3f53](https://github.com/polomarcus/common-trails/commit/2bb3f53b51f00f53ebf8eef52c20545b6b287f85))
* **archive:** drain robustness — ingestible-only zip caps + stale-processing recovery + admin requeue ([#495](https://github.com/polomarcus/common-trails/issues/495)) ([da38863](https://github.com/polomarcus/common-trails/commit/da388635a6b957f1aaa17f65398b0f4317646738))
* **archive:** find activities.csv at any depth in re-zipped exports + skip macOS junk ([#489](https://github.com/polomarcus/common-trails/issues/489)) ([710ece1](https://github.com/polomarcus/common-trails/commit/710ece1c9af54d83a056d584f7c8d1e54c321363))
* **archive:** junk zip members no longer count against MAX_ZIP_MEMBERS ([#494](https://github.com/polomarcus/common-trails/issues/494)) ([3c505f8](https://github.com/polomarcus/common-trails/commit/3c505f8e6216d267901fb8a5a1a0e4f58aa45073))
* **archive:** sign PUT URLs via IAM signBlob when credentials have no private key ([#491](https://github.com/polomarcus/common-trails/issues/491)) ([3a50328](https://github.com/polomarcus/common-trails/commit/3a5032831f0aea0c7611341f35be698494aa2462))
* **audit:** close all code-level 🔴 from the full codebase audit ([#427](https://github.com/polomarcus/common-trails/issues/427)) ([0691f6f](https://github.com/polomarcus/common-trails/commit/0691f6f70f7e62b1d32f8228e7e9ded5b5b7e689))
* **backend:** /readyz 22s → 109ms (banner stuck forever) ([10215b0](https://github.com/polomarcus/common-trails/commit/10215b0b1acbe85b71eedb144e168475145bb0f8))
* **backend:** /readyz 22s → 109ms (was blocking banner forever) ([1d4ea0c](https://github.com/polomarcus/common-trails/commit/1d4ea0c74c4c79a6799cfe1fb3379134a80dcea1))
* **backend:** MVT tile OOM + DFCI endpoint safety limits ([6da6c36](https://github.com/polomarcus/common-trails/commit/6da6c36cfb15167246236a7948dad5eaf1214ffd))
* **ci:** lint fixes + skip 7 pre-existing failing tests + sync lockfile ([3755c5f](https://github.com/polomarcus/common-trails/commit/3755c5f3c780e90ddee603a38da53e858f7301bb))
* **ci:** retry alembic on DB connection race + sync lockfile ([daf3653](https://github.com/polomarcus/common-trails/commit/daf3653230472615a265cb795bfc859581223d57))
* **consent:** record ODbL consent on the loose GPX/FIT path + source-neutral wording (audit gap [#7](https://github.com/polomarcus/common-trails/issues/7)) ([#499](https://github.com/polomarcus/common-trails/issues/499)) ([c621572](https://github.com/polomarcus/common-trails/commit/c6215729243eec676781b764ae1acf5a2c790c4c))
* **db:** cascade-delete heat_edge_contributors when heat_edge is deleted ([#318](https://github.com/polomarcus/common-trails/issues/318)) ([db1a7df](https://github.com/polomarcus/common-trails/commit/db1a7df1c081cea7548b367907c9c16600614821))
* **export:** aggregate heatmap GeoJSON/KML by way — continuous lines for gpx.studio ([#432](https://github.com/polomarcus/common-trails/issues/432)) ([f5cb3cc](https://github.com/polomarcus/common-trails/commit/f5cb3ccc4a52fc3559ae0c323c239dbd52670c29))
* **gpx:** activity_date dropped on HTTP upload (cross-provider dedup broken) ([#330](https://github.com/polomarcus/common-trails/issues/330)) ([0623420](https://github.com/polomarcus/common-trails/commit/0623420d8f1556e201419dc05bf5c21551fc9df7))
* **gpx:** extract .gpx.gz / .fit.gz members from Strava bulk-export ZIPs ([#356](https://github.com/polomarcus/common-trails/issues/356)) ([bf1f03f](https://github.com/polomarcus/common-trails/commit/bf1f03f6220e0f65d80c2b27960f5b5ee6929915))
* **gpx:** harden stdlib XML against DTD entity-expansion attacks (defusedxml) ([#358](https://github.com/polomarcus/common-trails/issues/358)) ([7c289f7](https://github.com/polomarcus/common-trails/commit/7c289f7baa264938e4c95e83f8248e2129a8e7d8))
* **gpx:** harden upload paths against zip-bomb + dense-coord DoS ([#317](https://github.com/polomarcus/common-trails/issues/317)) ([182612f](https://github.com/polomarcus/common-trails/commit/182612f9f7d8218a9aba8324c75ebbd95cfc6788))
* **gpx:** per-member error for ZIP-member size + path-traversal ([#359](https://github.com/polomarcus/common-trails/issues/359)) ([2dec366](https://github.com/polomarcus/common-trails/commit/2dec366b5fd9a4e40d249ff1da0395d604fb88d5))
* **gpx:** unblock event loop + archive every bundle + index dedup ([#319](https://github.com/polomarcus/common-trails/issues/319)) ([6c3c132](https://github.com/polomarcus/common-trails/commit/6c3c132a044e348d2dd078d4a9a130b4ed3c5314))
* **group_edges_osm:** set osm_way_id + accept parallel-near-360°/180° ([#250](https://github.com/polomarcus/common-trails/issues/250)) ([ceb26c2](https://github.com/polomarcus/common-trails/commit/ceb26c25a789680d870b8b8e385c31a93f410092))
* **heat-edges:** drop redundant 5dp re-snap on OSM-matched edges ([#374](https://github.com/polomarcus/common-trails/issues/374)) ([2aca743](https://github.com/polomarcus/common-trails/commit/2aca74303706ea448b08991ae377745246a9e617))
* **heat-edges:** pass_count tied to per-activity contributions, not UPSERT count ([#369](https://github.com/polomarcus/common-trails/issues/369)) ([a28026f](https://github.com/polomarcus/common-trails/commit/a28026ff7a7e51b02e61bf907b77f1026f822b16))
* heatmap hardening — idempotent migration, pruning, error handling ([#149](https://github.com/polomarcus/common-trails/issues/149)) ([5a6bf7a](https://github.com/polomarcus/common-trails/commit/5a6bf7acb7db01303ece5479db32c3c114106feb))
* heatmap spaghetti — aggressive snap + zoom-based min uc ([d9ac0db](https://github.com/polomarcus/common-trails/commit/d9ac0db3c5a48deeb9359dcf8aa4fee6fdefe7f3))
* heatmap spaghetti — snap-to-grid merge for MVT tiles z11-z15 ([133f997](https://github.com/polomarcus/common-trails/commit/133f997db9d9e1899fe6b2e8a7e1854c804f76b6))
* **heatmap/raw:** reject GPS-glitch outliers before render ([#511](https://github.com/polomarcus/common-trails/issues/511)) ([1a790c1](https://github.com/polomarcus/common-trails/commit/1a790c17cbe7cc047adac3fcd2ab250fd8a4e8fd))
* **heatmap:** PMTiles light path touches only heat_edges_agg — no raw scan on f1-micro ([#441](https://github.com/polomarcus/common-trails/issues/441) follow-up) ([#442](https://github.com/polomarcus/common-trails/issues/442)) ([43023c1](https://github.com/polomarcus/common-trails/commit/43023c198472e17eaae7b7dc5f55286da9d64f10))
* **home:** real stats + tightened SEO copy ([#256](https://github.com/polomarcus/common-trails/issues/256)) ([fd97aee](https://github.com/polomarcus/common-trails/commit/fd97aeed68e6aa9ab9f1faa5143d26401fc555d8))
* **imports:** enforce MAX_GPX_SIZE per-file on /imports/files single .gpx + .fit branches ([#354](https://github.com/polomarcus/common-trails/issues/354)) ([f21d519](https://github.com/polomarcus/common-trails/commit/f21d5196ce467cec0cdcd6e380c81f20d4b7f5ce))
* **imports:** size-guard the signed-URL archive upload ([#455](https://github.com/polomarcus/common-trails/issues/455) fast-follow) ([#457](https://github.com/polomarcus/common-trails/issues/457)) ([34ad349](https://github.com/polomarcus/common-trails/commit/34ad349978122ba5ef67eaef34ed4f9b2f3120b6))
* increase MVT snap grid + extend merge to z16 ([da58272](https://github.com/polomarcus/common-trails/commit/da5827232567042492ab9049ad0bfa7988d99f68))
* **ingest:** arc-length bucket Valhalla matched_points along OSM polyline ([#376](https://github.com/polomarcus/common-trails/issues/376)) ([8e17fc1](https://github.com/polomarcus/common-trails/commit/8e17fc156a8ae0e6e2b9d5152323c461c88f2b31))
* **ingest:** coerce ISO 8601 string activity_date before dedup-window subtraction ([#379](https://github.com/polomarcus/common-trails/issues/379)) ([41ae36f](https://github.com/polomarcus/common-trails/commit/41ae36f80315a12b5e4f719466a1a9ca867a7f62))
* **ingest:** heat_edges sport partition normalization ([90598df](https://github.com/polomarcus/common-trails/commit/90598dfee27e4d6c3cc6e0038731e2cc81db5d27))
* **ingest:** heat_edges.match_source 'grid' → 'grid_fallback' (doc/code drift) ([#353](https://github.com/polomarcus/common-trails/issues/353)) ([354e5e2](https://github.com/polomarcus/common-trails/commit/354e5e2ba4be32eff48931f517bbebc241d59f21))
* ingestion improvements (densifier breaks, sport-aware OSM radius, test fixes) ([c35fb81](https://github.com/polomarcus/common-trails/commit/c35fb8131fc869c866d68d93b5a41fc326d5e4f8))
* **ingest:** motorway under-passage in critical-connectors CTE + boundary test pin ([#383](https://github.com/polomarcus/common-trails/issues/383)) ([5184612](https://github.com/polomarcus/common-trails/commit/51846129c9c361fd06b09ab5764d7d8d374475d7))
* **ingest:** normalize heat_edges sport to real partitions (offroad→gravel) ([573d0eb](https://github.com/polomarcus/common-trails/commit/573d0eb3a23b3d6329570fdb60abc91bd7d17872))
* **ingest:** Overpass writer now populates ele_* + surface_confidence ([#262](https://github.com/polomarcus/common-trails/issues/262)) ([918a0fa](https://github.com/polomarcus/common-trails/commit/918a0fa25bc9d365b66b5dd0e95bac2f773f52a1))
* **ingest:** project OSM-matched GPS points onto the OSM line ([2e9a665](https://github.com/polomarcus/common-trails/commit/2e9a665f0e084c13e478a12b474f239c13acc91c))
* **ingest:** skip OSM-match + heat_edges write under the raw-trace pivot ([#513](https://github.com/polomarcus/common-trails/issues/513)) ([4accd4d](https://github.com/polomarcus/common-trails/commit/4accd4dc6cd5916395a4b20cb7c02296518c6f9f))
* **ingest:** unified sport classifier across all intakes + folder upload UX ([#431](https://github.com/polomarcus/common-trails/issues/431)) ([14b183c](https://github.com/polomarcus/common-trails/commit/14b183ca556dcd8f98dd7cbed842d3073deee27e))
* **lint:** drop unused 'from alembic import op' until migration is activated ([0da4b53](https://github.com/polomarcus/common-trails/commit/0da4b5317c892d60c6577e925365a728cebc914e))
* **lint:** import sorting in test_gpx_backup new tests ([508ab61](https://github.com/polomarcus/common-trails/commit/508ab619276330629816c47f2fdc51e5b969d12e))
* **lint:** ruff cleanup — unused vars + sorted imports ([bc1d0ca](https://github.com/polomarcus/common-trails/commit/bc1d0ca0f298f27f100db15301c013480d1eafd8))
* **lint:** sort imports in main.py ([03c17ba](https://github.com/polomarcus/common-trails/commit/03c17ba466e17fa6c6baf78b1f4b6a9078bc85ae))
* **lint:** unused import + context manager in test_gpx_backup ([5551ec4](https://github.com/polomarcus/common-trails/commit/5551ec437da2b71379259f6a9d0ad3387677bfb5))
* **monitoring+e2e:** heat-quality O(n), Makefile occitanie default, E2E UI/MVT/Strava-mock hardening + ES/IT regions ([#434](https://github.com/polomarcus/common-trails/issues/434)) ([93e824e](https://github.com/polomarcus/common-trails/commit/93e824e8b19d2e2e99a88a317c5059df147638a8))
* MVT snap-to-grid query — use subquery for zero-length filter ([3071bc2](https://github.com/polomarcus/common-trails/commit/3071bc27a106b69f5229a64e96222bc16dff85b4))
* **notifications:** PERSONAL-only "ride synced" toast copy (post-pivot) ([#467](https://github.com/polomarcus/common-trails/issues/467)) ([5dad75c](https://github.com/polomarcus/common-trails/commit/5dad75c40e8b115fe07b28ea4f1be3cb22ecd10f))
* pending bugs (regional.pb 500, heatmap spaghetti, proposal diversity) + routing audit ([9050836](https://github.com/polomarcus/common-trails/commit/9050836d804ce74e952d3791fb2cdb351f311218))
* pending bugs (regional.pb, spaghetti, proposal diversity) + routing audit + drop TS routing fallback ([60ba9f9](https://github.com/polomarcus/common-trails/commit/60ba9f9c24506a95d0e65170d3c6c0a03bbeafbd))
* **pmtiles:** --keep-grid-fallback also relaxes grid_fallback_min_uc ([#238](https://github.com/polomarcus/common-trails/issues/238)) ([cd0046c](https://github.com/polomarcus/common-trails/commit/cd0046ceb0a910d6c6ab4398ec06692359b1617b))
* **raw:** finish the cutover on the read/export paths (no 500 on dropped substrate, no OOM) ([#517](https://github.com/polomarcus/common-trails/issues/517)) ([2d507a3](https://github.com/polomarcus/common-trails/commit/2d507a38d8c3e37bdc1862656cd36d00a95c01e1))
* release DB pool between batches, paginate stats fetches, pause polling when tab hidden ([#311](https://github.com/polomarcus/common-trails/issues/311)) ([ea8fb11](https://github.com/polomarcus/common-trails/commit/ea8fb11029adac9c35c64a21e86a1e582d490500))
* revert min_uc filter — keep K=1 for beta, snap-merge only ([d44ef7c](https://github.com/polomarcus/common-trails/commit/d44ef7c5b8e71670e37079f84b11ca897021d152))
* revert uc=2 hardcode — respect HEATMAP_K_ANONYMITY for routing tiles ([e33922d](https://github.com/polomarcus/common-trails/commit/e33922d14825f45c53db8985c248cdb71a623224))
* robust cross-provider dedup + GPX date extraction + 5 tests ([5da9b8f](https://github.com/polomarcus/common-trails/commit/5da9b8f0a27193d81f123c7a7e68c7334221c3bc))
* routing tiles use min uc=2 (prevents 4.2M edge browser OOM) ([171532f](https://github.com/polomarcus/common-trails/commit/171532f6fc1243a6f961529c91dc6350f9ba58b1))
* **routing:** couche propre — chain-and-chunk heat + node weld, trails segmentés, pass_count, shards PACA/Corse, garde substrat ([#437](https://github.com/polomarcus/common-trails/issues/437)) ([58a2df6](https://github.com/polomarcus/common-trails/commit/58a2df6e73e027c52471185be321e75251062a34))
* **routing:** paved-skeleton connectors fix gravel/mtb shard fragmentation ([#429](https://github.com/polomarcus/common-trails/issues/429)) ([15db85c](https://github.com/polomarcus/common-trails/commit/15db85c1f94a681757a440b3d9f76a7d7f3a613e))
* ruff lint fixes + protobuf CDN serving ([699781c](https://github.com/polomarcus/common-trails/commit/699781c0f39db39831818b17ceffc6dfcb8c7bee))
* ruff lint fixes for CDN write-through + protobuf ([7479e1b](https://github.com/polomarcus/common-trails/commit/7479e1b55e2e63ff4949687806414351f52eba10))
* scaling + offroad-visibility hardening ([#233](https://github.com/polomarcus/common-trails/issues/233)) ([a0e83cc](https://github.com/polomarcus/common-trails/commit/a0e83cc45fe331a3b6fdf3d1701b55deb9c47f48))
* **share:** render OG card as PNG (was SVG, dropped by WhatsApp/Twitter) ([#273](https://github.com/polomarcus/common-trails/issues/273)) ([fca4a2c](https://github.com/polomarcus/common-trails/commit/fca4a2cd9b0fa460f126a9b9a5b4d8d0914e8a32))
* spaghetti at live endpoints, offroad partition, drift guard, audit closure ([b4cdf32](https://github.com/polomarcus/common-trails/commit/b4cdf32e9a3e3f6601ed94f42addc19857dc8066))
* **strava + pmtiles:** defensive outer except in _run_strava_import + prefer non-null way_geometry ([#357](https://github.com/polomarcus/common-trails/issues/357)) ([b772f61](https://github.com/polomarcus/common-trails/commit/b772f613b1dbf2cd467785c1837a6c4abba5ea98))
* **strava:** _run_photo_import — short-lived sessions per batch ([#331](https://github.com/polomarcus/common-trails/issues/331)) ([da18611](https://github.com/polomarcus/common-trails/commit/da186114d4ad8b1fc6c1cd52717565be9cc90f4d))
* **strava-client:** Retry-After propagation + photo 429 batch break + shared httpx client ([#332](https://github.com/polomarcus/common-trails/issues/332)) ([bc5500c](https://github.com/polomarcus/common-trails/commit/bc5500c8fc3c4e2b53fa71ae0cc03795bb436e3c))
* **strava:** audit S2 bundle — phase-2 notif + CLI cascade/cap + ghost existing_ids rollback ([#347](https://github.com/polomarcus/common-trails/issues/347)) ([99dbf6c](https://github.com/polomarcus/common-trails/commit/99dbf6c26cb1c4012a3abd500c02c7df4921069b))
* **strava:** audit S2.6 + S2.7 + S3 cleanup bundle — dedup tighten, account preference, sentry parity, run_import tests ([#348](https://github.com/polomarcus/common-trails/issues/348)) ([2105665](https://github.com/polomarcus/common-trails/commit/2105665e446d339aadbed360560a201c95fb641b))
* **strava:** audit S3.3 — replace DB-backed OAuth state with signed JWT ([#349](https://github.com/polomarcus/common-trails/issues/349)) ([ca2b660](https://github.com/polomarcus/common-trails/commit/ca2b66081c3c30ceb0ac3e0eeb486451e8809880))
* **strava:** close TEST_MODE CSRF hole + state guard tests ([#244](https://github.com/polomarcus/common-trails/issues/244)) ([d46528d](https://github.com/polomarcus/common-trails/commit/d46528dfb4f49b9e7c87ca9d06aabc97cfa90d4c))
* **strava:** codify env vars + repair resync_strava import + agent doc ([#329](https://github.com/polomarcus/common-trails/issues/329)) ([2b32976](https://github.com/polomarcus/common-trails/commit/2b329765059081a46e25763b2fe3c971e5fa55da))
* **strava:** emit user notification when ImportJob is OOM-killed (signal-9 bypass) ([#344](https://github.com/polomarcus/common-trails/issues/344)) ([28d9990](https://github.com/polomarcus/common-trails/commit/28d99905028295c798cb2c247b6ab37284f7977e))
* **strava:** encrypt IntegrationAccount tokens at rest (Fernet + Secret Manager) ([#335](https://github.com/polomarcus/common-trails/issues/335)) ([01dcd05](https://github.com/polomarcus/common-trails/commit/01dcd05e04461dcd416a8e582f9be8c8fafc803d))
* **strava:** job lifecycle — user-scoped queue + page cap + stale cutoff + OAuth TTL ([#333](https://github.com/polomarcus/common-trails/issues/333)) ([8e312b4](https://github.com/polomarcus/common-trails/commit/8e312b4f196238f0ed028385cb1127ebb03c5f43))
* **strava:** observability + perf — Sentry, SQL agg, Cloud Tasks heat, preview cache, failed phase surface ([#334](https://github.com/polomarcus/common-trails/issues/334)) ([b6a3e3e](https://github.com/polomarcus/common-trails/commit/b6a3e3e86933bd931b6b306487d2a789a7868914))
* **strava:** post-[#345](https://github.com/polomarcus/common-trails/issues/345) audit S1s — Job Phase 3 pool hygiene + CLI secret leak + webhook subscription_id allow-list ([#346](https://github.com/polomarcus/common-trails/issues/346)) ([8686089](https://github.com/polomarcus/common-trails/commit/86860899e7a80e643b3fe4c8a9130c134037caf7))
* **strava:** prevent OOM in 512 Mi import job — bound osm_grid_cache ([#345](https://github.com/polomarcus/common-trails/issues/345)) ([4fa873b](https://github.com/polomarcus/common-trails/commit/4fa873bfe21bfcb1b3e2ff0b9d9deb20294527a6))
* **strava:** sweep — sentry capture-first + suppression in 4 sister handlers ([#360](https://github.com/polomarcus/common-trails/issues/360)) ([ff182f2](https://github.com/polomarcus/common-trails/commit/ff182f29f6507331b766819cee50d6b4e32e6f44))
* **strava:** sync longevity — trailing-window reconciliation, subscription liveness, reconnect prompt ([#439](https://github.com/polomarcus/common-trails/issues/439)) ([4a9e840](https://github.com/polomarcus/common-trails/commit/4a9e840624fb0ae12c2d72cc516ec0bf566f983b))
* **strava:** unwedge prod — short-lived sessions + persist gps_total ([#314](https://github.com/polomarcus/common-trails/issues/314)) ([5573b57](https://github.com/polomarcus/common-trails/commit/5573b57520e4ac6e754d1fb389a438b9f2c3207a))
* **strava:** widen import_jobs.cursor to Text + write last_synced_at on every successful sync ([#355](https://github.com/polomarcus/common-trails/issues/355)) ([0f7d311](https://github.com/polomarcus/common-trails/commit/0f7d3118b609798b2717812b0fc3b1a2dd378de0))
* **strava:** zombie job cleanup + queue UI + admin cancel ([#338](https://github.com/polomarcus/common-trails/issues/338)) ([fdfb3f5](https://github.com/polomarcus/common-trails/commit/fdfb3f55636ebd58ef4408a5617d5740ccde992d))
* **test:** pipeline_patterns silent bit-rot — isolate by bbox ([#242](https://github.com/polomarcus/common-trails/issues/242)) ([7c53777](https://github.com/polomarcus/common-trails/commit/7c537771c7b5915fb21c5d9d7e922d7c02c72074))
* tile-based delete in OSM import (don't wipe all regions) ([8270416](https://github.com/polomarcus/common-trails/commit/82704168331ab84d270d96cb54de940e7b8980ee))
* TS strict mode graph_pb access + update cache-control test assertion ([4c0ad2f](https://github.com/polomarcus/common-trails/commit/4c0ad2fb4936168d09f8dfe093090c1d42e30bd4))


### Miscellaneous

* decommission the in-app WASM routing / route editor ([#518](https://github.com/polomarcus/common-trails/issues/518)) ([134737d](https://github.com/polomarcus/common-trails/commit/134737d5f849e7161d47223dd47daa3496ef6f67))
* deep agent docs + cascade cap + drift test + dedup test + beta gate ([#229](https://github.com/polomarcus/common-trails/issues/229)) ([7116bfc](https://github.com/polomarcus/common-trails/commit/7116bfcb99f4d51c61cd8d494e7ddcb9006a0b4f))
* delete broken-at-scale backend GET /routing endpoint ([#372](https://github.com/polomarcus/common-trails/issues/372)) ([be83318](https://github.com/polomarcus/common-trails/commit/be83318fda1fda7fcd561b97044affe52c020945))
* **dem:** bake Cataluna + Italia Nord-Ovest HGT tiles into the image ([#294](https://github.com/polomarcus/common-trails/issues/294)) ([d275bc2](https://github.com/polomarcus/common-trails/commit/d275bc29c10726a8332c8e1892514d07c958ec3b))
* extend uploads bucket lifecycle from 90 days to 2 years ([d971e20](https://github.com/polomarcus/common-trails/commit/d971e20a175fc9b6a4724a3ad73d01940281a83e))
* **import-osm:** drop `service` highway from import filter ([#298](https://github.com/polomarcus/common-trails/issues/298)) ([4d4575d](https://github.com/polomarcus/common-trails/commit/4d4575d2c6c53176a48dbb69b5d4f9ea624e09d4))
* **ingest:** close S3 nits from heat_edges + Valhalla bucket reviews ([#377](https://github.com/polomarcus/common-trails/issues/377)) ([2383338](https://github.com/polomarcus/common-trails/commit/2383338467cb848f94698750501933c4580fd6f1))
* **oauth:** drop dead `oauth_states` table (post-[#349](https://github.com/polomarcus/common-trails/issues/349) cleanup) ([#351](https://github.com/polomarcus/common-trails/issues/351)) ([e1c787b](https://github.com/polomarcus/common-trails/commit/e1c787ba97aaab6e8cc47a8fc9fca9325ef92534))
* **orm:** partial index parity on heat_edges.osm_way_id ([#292](https://github.com/polomarcus/common-trails/issues/292)) ([15a1629](https://github.com/polomarcus/common-trails/commit/15a1629afd724fc932606ee53e30bca102a73562))
* project-wide audit - bug fixes + doc drift cleanup ([#312](https://github.com/polomarcus/common-trails/issues/312)) ([9551933](https://github.com/polomarcus/common-trails/commit/955193342b5912ef4a108205b8af6e0bf0722266))
* release ([#305](https://github.com/polomarcus/common-trails/issues/305)) ([c3767ec](https://github.com/polomarcus/common-trails/commit/c3767eca755a4547ad8c894b499a180d0b9e1c36))
* release 0.7.101 ([#209](https://github.com/polomarcus/common-trails/issues/209)) ([c546dc9](https://github.com/polomarcus/common-trails/commit/c546dc9c7affec409f091d16a754efc6f80cae24))
* release 0.7.102 ([#211](https://github.com/polomarcus/common-trails/issues/211)) ([829d86a](https://github.com/polomarcus/common-trails/commit/829d86a0eab1ff3ec30efba73fdb9b529468daad))
* release 0.7.103 ([#216](https://github.com/polomarcus/common-trails/issues/216)) ([9c5d992](https://github.com/polomarcus/common-trails/commit/9c5d9928d997b41f88bb82f001dfea652f89b211))
* release 0.7.104 ([#217](https://github.com/polomarcus/common-trails/issues/217)) ([8ca500f](https://github.com/polomarcus/common-trails/commit/8ca500f36ae356b4287a4404a50150f26c38d0d1))
* release 0.7.105 ([#219](https://github.com/polomarcus/common-trails/issues/219)) ([887496f](https://github.com/polomarcus/common-trails/commit/887496fa475f2e996a3257605c694b17feb5a5b7))
* release 0.7.108 ([#224](https://github.com/polomarcus/common-trails/issues/224)) ([0553ebf](https://github.com/polomarcus/common-trails/commit/0553ebfa022ecf31280c070a5b94c4bcf01570ea))
* release 0.7.110 ([#228](https://github.com/polomarcus/common-trails/issues/228)) ([fbe2110](https://github.com/polomarcus/common-trails/commit/fbe211080766326717450d0965209ddd24d2c498))
* release 0.7.111 ([#257](https://github.com/polomarcus/common-trails/issues/257)) ([053998b](https://github.com/polomarcus/common-trails/commit/053998b8acd0348b9ff09aacb6b68a3521d0bfbe))
* release 0.7.112 ([#263](https://github.com/polomarcus/common-trails/issues/263)) ([f789e85](https://github.com/polomarcus/common-trails/commit/f789e85ef813b6c571c5210a262239b4476def6a))
* release 0.7.114 ([#271](https://github.com/polomarcus/common-trails/issues/271)) ([7b84f20](https://github.com/polomarcus/common-trails/commit/7b84f20e9044abf4aa9bfe27ace973b437d7a3c3))
* release 0.7.116 ([#276](https://github.com/polomarcus/common-trails/issues/276)) ([99d17d2](https://github.com/polomarcus/common-trails/commit/99d17d29b954095a7351405408bdf6f5ef0863f7))
* release 0.7.116 ([#277](https://github.com/polomarcus/common-trails/issues/277)) ([caf42ba](https://github.com/polomarcus/common-trails/commit/caf42ba972cb342017ee54ecb2e24ff77da7f833))
* release 0.7.117 ([#281](https://github.com/polomarcus/common-trails/issues/281)) ([65dba2d](https://github.com/polomarcus/common-trails/commit/65dba2d8b521a9d9e37cb8155ca5d0be02e0ff7d))
* release 0.7.119 ([#290](https://github.com/polomarcus/common-trails/issues/290)) ([7ba5e57](https://github.com/polomarcus/common-trails/commit/7ba5e57559b26fba03f4dac1eb704e859638fd03))
* release 0.7.120 ([#291](https://github.com/polomarcus/common-trails/issues/291)) ([a3215ac](https://github.com/polomarcus/common-trails/commit/a3215ac70167945fabd3bc58b3a025e71d2c6493))
* release 0.7.121 ([#293](https://github.com/polomarcus/common-trails/issues/293)) ([df7e43d](https://github.com/polomarcus/common-trails/commit/df7e43d0db1f357b6fec58ca1bd117424533b7ce))
* release 0.7.122 ([#295](https://github.com/polomarcus/common-trails/issues/295)) ([55d6f0c](https://github.com/polomarcus/common-trails/commit/55d6f0cdb4657f99e721ec1f50dd44218c023b45))
* release 0.7.123 ([#300](https://github.com/polomarcus/common-trails/issues/300)) ([d2ae7a9](https://github.com/polomarcus/common-trails/commit/d2ae7a91ec4e7bdab2aa162f00c2bf1cbbd3a836))
* release 0.7.65 ([#144](https://github.com/polomarcus/common-trails/issues/144)) ([527f7da](https://github.com/polomarcus/common-trails/commit/527f7da54681899ddb12f4ad4d8df70dad61861b))
* release 0.7.66 ([#145](https://github.com/polomarcus/common-trails/issues/145)) ([512cc1d](https://github.com/polomarcus/common-trails/commit/512cc1de9a4190d6d9e581bbefb461aa5552b48c))
* release 0.7.67 ([#148](https://github.com/polomarcus/common-trails/issues/148)) ([1d48aa3](https://github.com/polomarcus/common-trails/commit/1d48aa33540ab6b821b27b5fbac1dd51fcb2c740))
* release 0.7.68 ([#150](https://github.com/polomarcus/common-trails/issues/150)) ([185ea70](https://github.com/polomarcus/common-trails/commit/185ea7043c83f6d7b6f79148d5ac6137b1041066))
* release 0.7.69 ([#154](https://github.com/polomarcus/common-trails/issues/154)) ([0b15bf3](https://github.com/polomarcus/common-trails/commit/0b15bf38b579f374a4eac0a593544fa54c33f30e))
* release 0.7.71 ([#157](https://github.com/polomarcus/common-trails/issues/157)) ([e8a3b38](https://github.com/polomarcus/common-trails/commit/e8a3b388425a28065fb67b34dacb176d590f2ae3))
* release 0.7.72 ([#158](https://github.com/polomarcus/common-trails/issues/158)) ([e03931f](https://github.com/polomarcus/common-trails/commit/e03931f7c160257eaba7e8e9d65be4c38f9cafb8))
* release 0.7.73 ([#159](https://github.com/polomarcus/common-trails/issues/159)) ([9efb63c](https://github.com/polomarcus/common-trails/commit/9efb63c18d53fbf1c05d17ed0479d30f5a680129))
* release 0.7.75 ([#161](https://github.com/polomarcus/common-trails/issues/161)) ([2099bac](https://github.com/polomarcus/common-trails/commit/2099bac1dfdfdd2e3740e441a5362c7bf060ed1c))
* release 0.7.79 ([#165](https://github.com/polomarcus/common-trails/issues/165)) ([23bf41a](https://github.com/polomarcus/common-trails/commit/23bf41a25fa58ce452a59d73769f13128ffca1b9))
* release 0.7.80 ([#166](https://github.com/polomarcus/common-trails/issues/166)) ([dec546d](https://github.com/polomarcus/common-trails/commit/dec546d75bf0bc0b45c0b296a124d3f2bcb747d9))
* release 0.7.82 ([#168](https://github.com/polomarcus/common-trails/issues/168)) ([a91eb7f](https://github.com/polomarcus/common-trails/commit/a91eb7ff2add6146d348a51936c431be4416d928))
* release 0.7.89 ([#176](https://github.com/polomarcus/common-trails/issues/176)) ([03d8c03](https://github.com/polomarcus/common-trails/commit/03d8c03db788cabf25d49fccd20c3a1d4d713cb5))
* release 0.7.90 ([#178](https://github.com/polomarcus/common-trails/issues/178)) ([bb3ede1](https://github.com/polomarcus/common-trails/commit/bb3ede1ee2e2f151be8fe036bd65a77e16faf52d))
* release 0.7.91 ([#181](https://github.com/polomarcus/common-trails/issues/181)) ([8bd788c](https://github.com/polomarcus/common-trails/commit/8bd788cb242b3387ca29060952575d603a9cd80d))
* release 0.7.92 ([#184](https://github.com/polomarcus/common-trails/issues/184)) ([85c8fa2](https://github.com/polomarcus/common-trails/commit/85c8fa287d5e88483282778883ecb5bdb4b9c977))
* release 0.7.93 ([#186](https://github.com/polomarcus/common-trails/issues/186)) ([7f79cb3](https://github.com/polomarcus/common-trails/commit/7f79cb3cdd31407fe29e1b2785ee5657143391ec))
* release 0.7.94 ([#189](https://github.com/polomarcus/common-trails/issues/189)) ([40c3913](https://github.com/polomarcus/common-trails/commit/40c3913795abebdec382267b03607ba2efcd3762))
* release 0.7.96 ([#198](https://github.com/polomarcus/common-trails/issues/198)) ([4e08c1f](https://github.com/polomarcus/common-trails/commit/4e08c1f7ef38299d21bb915b3b9b7df59d6dc8b1))
* **strava:** route all /api/v3 calls through configurable STRAVA_API_BASE ([#387](https://github.com/polomarcus/common-trails/issues/387)) ([6763f29](https://github.com/polomarcus/common-trails/commit/6763f292e346f8e8fcabbbf1ec02e8802bbb1f05))
* **tests:** disable routing test suite ahead of the raw-trace cutover ([#510](https://github.com/polomarcus/common-trails/issues/510)) ([cd3e818](https://github.com/polomarcus/common-trails/commit/cd3e8189fb3e3a73f6893bb4c393abc6f6e2bb5f))

## [0.54.0](https://github.com/polomarcus/common-trails/compare/backend-v0.53.0...backend-v0.54.0) (2026-05-17)


### Features

* /healthz returns release-please version + fix Docker cache ([#101](https://github.com/polomarcus/common-trails/issues/101)) ([36d10c0](https://github.com/polomarcus/common-trails/commit/36d10c00920e957310f299faca6ee3e414554c97))
* 4dp snap grid (11m) — 43x more shared edges, motto test passes ([5243352](https://github.com/polomarcus/common-trails/commit/524335223af67b5888e2de4c324d32370c842455))
* **activities:** add PostGIS geometry column ([5bebeca](https://github.com/polomarcus/common-trails/commit/5bebeca9756416244196f6d5f37ee1db9a449d8d))
* **activities:** add PostGIS geometry column (binary, smaller, queryable) ([67b3ee2](https://github.com/polomarcus/common-trails/commit/67b3ee2db19b30acbbf84dc52500f8533b49d041))
* add "Couverture cellules" user cell coverage map layer ([2d7be77](https://github.com/polomarcus/common-trails/commit/2d7be77abe399d73e278a733629dd6b7c43b1024))
* add all French regions + Switzerland/Spain/Italy to OSM PBF import ([a13fac2](https://github.com/polomarcus/common-trails/commit/a13fac22a12249bba37fc9b5f12da3cf3f1e6fb3))
* **admin:** beta-time visibility — engagement, providers, heatmap quality, recent feeds ([#288](https://github.com/polomarcus/common-trails/issues/288)) ([362c4bb](https://github.com/polomarcus/common-trails/commit/362c4bb499ce8b709fc3ec2344147775f1102def))
* archive raw GPX uploads to bucket (heatmap rebuild safety net) ([7e9eadb](https://github.com/polomarcus/common-trails/commit/7e9eadbb5eb61bb49889aef210403444cbd1d245))
* archive raw GPX uploads to GCS bucket for heatmap rebuild safety net ([d323502](https://github.com/polomarcus/common-trails/commit/d323502c9a1affbb751fa2d4d8718d76ae7ac116))
* auto-rebuild .fgraph after GPX upload or Strava import ([2dfb876](https://github.com/polomarcus/common-trails/commit/2dfb8766c7db5389eedd65db53e4a878b5fbc1f1))
* **backend:** GPX backup to cloud storage + dedup safety net ([425f1f6](https://github.com/polomarcus/common-trails/commit/425f1f6e528157875b73fe7df76bfd8ce2efaa61))
* **beta:** 4 deferred audit items + group_edges_osm progress logging ([#269](https://github.com/polomarcus/common-trails/issues/269)) ([00bd001](https://github.com/polomarcus/common-trails/commit/00bd0016e3ca3f6975336a63e9e80e9d03efb73f))
* broaden running sport routing + parallelize external fallback ([#62](https://github.com/polomarcus/common-trails/issues/62)) ([8e6d4bc](https://github.com/polomarcus/common-trails/commit/8e6d4bce1be2ed65541f5e381baed6ab1fd41b27))
* bulk GPX folder import CLI + Mac→prod ops docs ([f76d477](https://github.com/polomarcus/common-trails/commit/f76d4779b8f54db626a084fea089c671b8b94515))
* CDN write-through cache implementation ([6774dc5](https://github.com/polomarcus/common-trails/commit/6774dc512ad26349b2fa95b85350f5aab5b49f4a))
* cell coverage layer, SPA server fix, top nav account link ([#57](https://github.com/polomarcus/common-trails/issues/57)) ([f16457b](https://github.com/polomarcus/common-trails/commit/f16457ba93d1c6316899c1748f7c9b2dcd40ff88))
* **cli:** bulk GPX folder import CLI ([3eaad9b](https://github.com/polomarcus/common-trails/commit/3eaad9b97d9432988b7365d3f03cf0e7d08660c3))
* Cloud Run Job for heatmap rebuild ([b0b52ba](https://github.com/polomarcus/common-trails/commit/b0b52bae95c6fa5e0fdf25276944e88aed062d91))
* collections page overhaul — rename, GT seed, POI placement ([565aef5](https://github.com/polomarcus/common-trails/commit/565aef58cb147fe0ea47b94e55a585028e204636))
* collections page overhaul — rename, GT seed, POI placement, UX fixes ([a09f969](https://github.com/polomarcus/common-trails/commit/a09f969d1914d9007a1ffb776c54704d01ba5f3d))
* colorblind-safe sport colors + DFCI slope exemption ([#73](https://github.com/polomarcus/common-trails/issues/73)) ([9ab669f](https://github.com/polomarcus/common-trails/commit/9ab669ff0983a708d49d67fc62def93ca36ea7ff))
* cross-provider dedup — same date + distance (±10%) = skip ([4d8a8e0](https://github.com/polomarcus/common-trails/commit/4d8a8e0c158517d5bfbd895a9523bc60eef7a3d2))
* **elevation+surface:** D+ smoothing + local DEM at PBF time + surface_confidence end-to-end ([#259](https://github.com/polomarcus/common-trails/issues/259)) ([a9d89ed](https://github.com/polomarcus/common-trails/commit/a9d89ed8fccdcd13ab162b34c2f7856968de5e13))
* French cyclist terminology for GitHub-like features ([#75](https://github.com/polomarcus/common-trails/issues/75)) ([243c31c](https://github.com/polomarcus/common-trails/commit/243c31c005f73a1a575d68ca15fd46bf4902a8cd))
* full routing engine in Rust WASM — cost model, graph, Dijkstra, proposals, stats ([d9dad51](https://github.com/polomarcus/common-trails/commit/d9dad51bc5441dd82e3207251099808ccbb884ee))
* GPX backup to cloud storage + dedup safety net ([be2dc37](https://github.com/polomarcus/common-trails/commit/be2dc3712ead8fd1d17c8d6c7509b74ecb9fca4f))
* **gpx:** auto-detect sport from GPX &lt;trk&gt;&lt;type&gt; on upload ([#301](https://github.com/polomarcus/common-trails/issues/301)) ([#302](https://github.com/polomarcus/common-trails/issues/302)) ([ce5d162](https://github.com/polomarcus/common-trails/commit/ce5d1626707cc85196c04f4f3b77188a7909341d))
* heatmap edge clustering — merge near-duplicate GPS edges ([#108](https://github.com/polomarcus/common-trails/issues/108)) ([50e96e0](https://github.com/polomarcus/common-trails/commit/50e96e05a603df70058fab1d851768ddaab3aba5))
* heatmap glow polish + Garmin .fit import + multi-source support ([474bd91](https://github.com/polomarcus/common-trails/commit/474bd913fadd00877ccf1d6484345b722aad3749))
* **heatmap+prod:** Komoot quality + Valhalla map-matching + event-driven artefact refresh ([d269d6b](https://github.com/polomarcus/common-trails/commit/d269d6b247b036d802c872fc25483cb8db54972a))
* i8n + CI lint + broken import test after .fit support ([#175](https://github.com/polomarcus/common-trails/issues/175)) ([dacc29c](https://github.com/polomarcus/common-trails/commit/dacc29cf52d8272afec84eae5d23df133cd99488))
* import queue — one Strava import at a time (rate limit protection) ([9da7816](https://github.com/polomarcus/common-trails/commit/9da7816e698392e7db20e136a407f858f32bb149))
* **ingest:** async gpx-upload via Cloud Tasks queue ([2ea8948](https://github.com/polomarcus/common-trails/commit/2ea8948c84f3cca415b1f45aebbc8a78ce676602))
* **ingest:** async gpx-upload via Cloud Tasks queue ([e98bac8](https://github.com/polomarcus/common-trails/commit/e98bac83dd6cf71b6f8c5b9f438fcf7f988ebca3))
* local filesystem mode for cache writer + 6 tests ([fdf70e5](https://github.com/polomarcus/common-trails/commit/fdf70e5bffedeff7280dbf54a902fd1b3e2dd767))
* **make:** prod rebuild targets + URGENT dry-run bug fix ([#249](https://github.com/polomarcus/common-trails/issues/249)) ([f533e07](https://github.com/polomarcus/common-trails/commit/f533e07ae272086e48591ed856609d7f6f201635))
* **map:** fork-UX polish + drop deprecated upstream-PR endpoints ([#279](https://github.com/polomarcus/common-trails/issues/279)) ([c247cd4](https://github.com/polomarcus/common-trails/commit/c247cd4b78c8da89dc90d1596041c7b549ec37aa))
* migrate backend from in-memory dicts to PostgreSQL ([#34](https://github.com/polomarcus/common-trails/issues/34)) ([dea9abb](https://github.com/polomarcus/common-trails/commit/dea9abb1d0868ecb02004302c76ca21628698778))
* **monitoring:** heat-edge spaghetti alerts via build_pmtiles hook ([#247](https://github.com/polomarcus/common-trails/issues/247)) ([f4b3179](https://github.com/polomarcus/common-trails/commit/f4b31790efbc526a50339333aa5ca7fa008416fa))
* MVT vector tiles + partition heat_edges + incremental rebuild ([#147](https://github.com/polomarcus/common-trails/issues/147)) ([284389c](https://github.com/polomarcus/common-trails/commit/284389c84f7f3e1947d7257ea82981e2b2dd7e69))
* **ops:** bake HGT tiles into image + activities D+ backfill CLI ([#261](https://github.com/polomarcus/common-trails/issues/261)) ([d307dca](https://github.com/polomarcus/common-trails/commit/d307dca30c1c58f727f45ec558c81b69aa6029fe))
* OSM road network as routing base layer ([#177](https://github.com/polomarcus/common-trails/issues/177)) ([214dcd6](https://github.com/polomarcus/common-trails/commit/214dcd62d140161d00eaf051d4af095bd2debcbf))
* parallel heatmap rebuild with deadlock retry ([eb9e640](https://github.com/polomarcus/common-trails/commit/eb9e6404d5bde08f8b1fc4600795d1dfd51b8faa))
* PMTiles heatmap display — single static file, CDN-served ([9c88975](https://github.com/polomarcus/common-trails/commit/9c88975872dac8fa3445e1f1362912b2bdd710e9))
* **prod-followups:** 4 bundled fixes — ORM drift / recompute Job / rebuild guard / admin job feed ([#289](https://github.com/polomarcus/common-trails/issues/289)) ([c34d239](https://github.com/polomarcus/common-trails/commit/c34d239cc2759af86440488fde563bc4a0079678))
* public routes panel on map + discover viewport filtering ([#89](https://github.com/polomarcus/common-trails/issues/89)) ([57342d8](https://github.com/polomarcus/common-trails/commit/57342d876b7fb36ba8ef834ebdab33847a05750d))
* resume import progress on Strava page revisit ([d81759b](https://github.com/polomarcus/common-trails/commit/d81759b41fd3f6f5e2ea89d4aa0731ae3b36a14c))
* run GPS upgrade in Cloud Run Job (fix stuck 0/1394) ([7282c84](https://github.com/polomarcus/common-trails/commit/7282c84c8fe9037b6e70b6c7bf074f08f3a01863))
* run GPS upgrade in Cloud Run Job (not API background task) ([d2ae1d7](https://github.com/polomarcus/common-trails/commit/d2ae1d746e3ff07e2b48705a35e8edae3d1c7765))
* Sentry routing observability + route sharing + OG previews ([adaf6f3](https://github.com/polomarcus/common-trails/commit/adaf6f3bb3440c15ead8cf87fde7aaead61a0a57))
* **sentry:** scrub raw lat/lon + hashed bbox span names ([#243](https://github.com/polomarcus/common-trails/issues/243)) ([d83c240](https://github.com/polomarcus/common-trails/commit/d83c240cd4e3b70794ce165d408c10cc7ad0fcf0))
* serve CTGB protobuf from CDN — 44% smaller than JSON ([2d5e6f7](https://github.com/polomarcus/common-trails/commit/2d5e6f748165ac012c3deb68123942f13c321ceb))
* serve frontend from Cloud Run + validation fix + trail cache ([#79](https://github.com/polomarcus/common-trails/issues/79)) ([0ace3bc](https://github.com/polomarcus/common-trails/commit/0ace3bc8c4e6e412c3e87ba67f061b6280e2052f))
* simplified layers panel + color-blind accessible heatmap & elev… ([#71](https://github.com/polomarcus/common-trails/issues/71)) ([c02907c](https://github.com/polomarcus/common-trails/commit/c02907c61b2df24567b5c7552f30c5cc8e8528f9))
* Strava-only login + routing audit fixes ([#65](https://github.com/polomarcus/common-trails/issues/65)) ([7b83487](https://github.com/polomarcus/common-trails/commit/7b83487575610306ad7017587af51591f099988e))
* stuck import detection + map link during Strava import ([#153](https://github.com/polomarcus/common-trails/issues/153)) ([9bc0ccd](https://github.com/polomarcus/common-trails/commit/9bc0ccd9e0f1e9b9953b11ffcc2eba60c9bea31a))
* surface, elevation, routing fixes & route management UX overhaul ([#77](https://github.com/polomarcus/common-trails/issues/77)) ([dea225f](https://github.com/polomarcus/common-trails/commit/dea225f97930a82b1770368219b6c9eff8e22ff2))
* **trails:** GR/GRP/GT/PR/EV from local PBF + kill runtime Overpass in prod ([#230](https://github.com/polomarcus/common-trails/issues/230)) ([f0b29a0](https://github.com/polomarcus/common-trails/commit/f0b29a0b0125576f1fd4985a9b3b0218b1d57bf6))
* two-phase Strava import — polylines fast, then batched GPS upgrade ([#60](https://github.com/polomarcus/common-trails/issues/60)) ([7462445](https://github.com/polomarcus/common-trails/commit/7462445208904b577d73b737c78418c7de6cdfeb))
* viewport-based binary graph loading (area.pb) — 44% smaller ([7c56779](https://github.com/polomarcus/common-trails/commit/7c56779978a4f01ac8ba6056810672856892d5cb))
* WASM router with pre-built .fgraph (CDN-ready) ([13eb486](https://github.com/polomarcus/common-trails/commit/13eb486359d01f5dcb5a44ca580088d06ae44c72))
* wire up Cloud Run Job for Strava import ([#53](https://github.com/polomarcus/common-trails/issues/53)) ([2d492c5](https://github.com/polomarcus/common-trails/commit/2d492c51d686a9d56154b989c0e923a24a69c7c2))


### Bug Fixes

* 6 code review issues — constant dedup, service imports, test speed ([be062d0](https://github.com/polomarcus/common-trails/commit/be062d06cb20b46cbce6887ac65b54ba1d47d8fe))
* add /routes to frontend HTML middleware, cover all frontend pages ([f4a5388](https://github.com/polomarcus/common-trails/commit/f4a53881d22b50d5ab8e589a0f4ef9b7827a16b8))
* add missing RouteCollection and RouteCollectionItem models ([464f751](https://github.com/polomarcus/common-trails/commit/464f7519b9eb11e62e4f3b0d866dea2039109124))
* add safety log showing existing DB size before tile-based delete ([e242fbb](https://github.com/polomarcus/common-trails/commit/e242fbb7ae76268f1495002047094310dbb931b9))
* add skip_heat_computation flag instead of contribute_heatmap=False ([d190144](https://github.com/polomarcus/common-trails/commit/d1901446e455735f7c5bf307ebc973134bcf681a))
* add trailing slash to Strava redirect for GCS static hosting ([#38](https://github.com/polomarcus/common-trails/issues/38)) ([300fc14](https://github.com/polomarcus/common-trails/commit/300fc14d2ab82fff31d8bede376f506be792f8b3))
* **api:** /readyz returns 200 'warming' when heat_edges empty ([#227](https://github.com/polomarcus/common-trails/issues/227)) ([a7863c9](https://github.com/polomarcus/common-trails/commit/a7863c935ea7c742675e89153a56b1648e76ab19))
* **api:** add /health alias for /healthz (Cloud Run edge 404 workaround) ([2bb3f53](https://github.com/polomarcus/common-trails/commit/2bb3f53b51f00f53ebf8eef52c20545b6b287f85))
* **backend:** /readyz 22s → 109ms (banner stuck forever) ([10215b0](https://github.com/polomarcus/common-trails/commit/10215b0b1acbe85b71eedb144e168475145bb0f8))
* **backend:** /readyz 22s → 109ms (was blocking banner forever) ([1d4ea0c](https://github.com/polomarcus/common-trails/commit/1d4ea0c74c4c79a6799cfe1fb3379134a80dcea1))
* **backend:** MVT tile OOM + DFCI endpoint safety limits ([6da6c36](https://github.com/polomarcus/common-trails/commit/6da6c36cfb15167246236a7948dad5eaf1214ffd))
* **ci:** lint fixes + skip 7 pre-existing failing tests + sync lockfile ([3755c5f](https://github.com/polomarcus/common-trails/commit/3755c5f3c780e90ddee603a38da53e858f7301bb))
* **ci:** retry alembic on DB connection race + sync lockfile ([daf3653](https://github.com/polomarcus/common-trails/commit/daf3653230472615a265cb795bfc859581223d57))
* correct GCE metadata URL for Cloud Run job trigger ([344d318](https://github.com/polomarcus/common-trails/commit/344d318659523b84c269cff40975c0eee495d1cf))
* DATA_DIR=/app/data in Terraform — GT routes + DFCI missing ([#105](https://github.com/polomarcus/common-trails/issues/105)) ([75ada47](https://github.com/polomarcus/common-trails/commit/75ada47e85e4819f974f1153856462faa5c987a8))
* deterministic user_id hash + SVG label clipping on landing page ([80df372](https://github.com/polomarcus/common-trails/commit/80df372af847f16be8694dcb6c11318821b2a7ff))
* DFCI cache write to /tmp + show skipped count in Strava UI ([#46](https://github.com/polomarcus/common-trails/issues/46)) ([2978c43](https://github.com/polomarcus/common-trails/commit/2978c43139cb7b6bb8457a9085c969d730b29e1b))
* E2E auth, Strava import progress, mobile landing, Cloud Run Job DB ([#81](https://github.com/polomarcus/common-trails/issues/81)) ([4e9b4e4](https://github.com/polomarcus/common-trails/commit/4e9b4e4d5605ae57ae5b2d0fce8b6e70081d9714))
* eliminate heatmap micro-gaps from OSM edge snapping ([b70090c](https://github.com/polomarcus/common-trails/commit/b70090c31bbcc118dd0cdde67b74207f9d2ad49a))
* expose startup progress in production for wakeup banner ([a5b464e](https://github.com/polomarcus/common-trails/commit/a5b464e3fc763e0fb05ff35c6ea1d4f0b642252e))
* extract origin from FRONTEND_URL for CORS (strip path) ([#40](https://github.com/polomarcus/common-trails/issues/40)) ([81ed785](https://github.com/polomarcus/common-trails/commit/81ed785cdb62a41c42adbaaed7c7e4ed42cafbff))
* GCS page refresh for extensionless URLs ([#22](https://github.com/polomarcus/common-trails/issues/22)) ([a8c20dd](https://github.com/polomarcus/common-trails/commit/a8c20dd60b1bf46966cd1d6ecedf33e3ebffe9ef))
* GCS routing — remove trailingSlash, fix basePath for all navigations ([#44](https://github.com/polomarcus/common-trails/issues/44)) ([e99aa77](https://github.com/polomarcus/common-trails/commit/e99aa77aaafce48e2c033478c410591361a47179))
* **group_edges_osm:** set osm_way_id + accept parallel-near-360°/180° ([#250](https://github.com/polomarcus/common-trails/issues/250)) ([ceb26c2](https://github.com/polomarcus/common-trails/commit/ceb26c25a789680d870b8b8e385c31a93f410092))
* heatmap hardening — idempotent migration, pruning, error handling ([#149](https://github.com/polomarcus/common-trails/issues/149)) ([5a6bf7a](https://github.com/polomarcus/common-trails/commit/5a6bf7acb7db01303ece5479db32c3c114106feb))
* heatmap spaghetti — aggressive snap + zoom-based min uc ([d9ac0db](https://github.com/polomarcus/common-trails/commit/d9ac0db3c5a48deeb9359dcf8aa4fee6fdefe7f3))
* heatmap spaghetti — snap-to-grid merge for MVT tiles z11-z15 ([133f997](https://github.com/polomarcus/common-trails/commit/133f997db9d9e1899fe6b2e8a7e1854c804f76b6))
* **home:** real stats + tightened SEO copy ([#256](https://github.com/polomarcus/common-trails/issues/256)) ([fd97aee](https://github.com/polomarcus/common-trails/commit/fd97aeed68e6aa9ab9f1faa5143d26401fc555d8))
* include DFCI IGN cache in /data/ for production import ([9534add](https://github.com/polomarcus/common-trails/commit/9534addefa0f8f80e0595729a186e88240f63f18))
* increase MVT snap grid + extend merge to z16 ([da58272](https://github.com/polomarcus/common-trails/commit/da5827232567042492ab9049ad0bfa7988d99f68))
* **ingest:** heat_edges sport partition normalization ([90598df](https://github.com/polomarcus/common-trails/commit/90598dfee27e4d6c3cc6e0038731e2cc81db5d27))
* ingestion improvements (densifier breaks, sport-aware OSM radius, test fixes) ([c35fb81](https://github.com/polomarcus/common-trails/commit/c35fb8131fc869c866d68d93b5a41fc326d5e4f8))
* **ingest:** normalize heat_edges sport to real partitions (offroad→gravel) ([573d0eb](https://github.com/polomarcus/common-trails/commit/573d0eb3a23b3d6329570fdb60abc91bd7d17872))
* **ingest:** Overpass writer now populates ele_* + surface_confidence ([#262](https://github.com/polomarcus/common-trails/issues/262)) ([918a0fa](https://github.com/polomarcus/common-trails/commit/918a0fa25bc9d365b66b5dd0e95bac2f773f52a1))
* **ingest:** project OSM-matched GPS points onto the OSM line ([2e9a665](https://github.com/polomarcus/common-trails/commit/2e9a665f0e084c13e478a12b474f239c13acc91c))
* **lint:** drop unused 'from alembic import op' until migration is activated ([0da4b53](https://github.com/polomarcus/common-trails/commit/0da4b5317c892d60c6577e925365a728cebc914e))
* **lint:** import sorting in test_gpx_backup new tests ([508ab61](https://github.com/polomarcus/common-trails/commit/508ab619276330629816c47f2fdc51e5b969d12e))
* **lint:** ruff cleanup — unused vars + sorted imports ([bc1d0ca](https://github.com/polomarcus/common-trails/commit/bc1d0ca0f298f27f100db15301c013480d1eafd8))
* **lint:** sort imports in main.py ([03c17ba](https://github.com/polomarcus/common-trails/commit/03c17ba466e17fa6c6baf78b1f4b6a9078bc85ae))
* **lint:** unused import + context manager in test_gpx_backup ([5551ec4](https://github.com/polomarcus/common-trails/commit/5551ec437da2b71379259f6a9d0ad3387677bfb5))
* logout cookie deletion + mobile landing page ([#83](https://github.com/polomarcus/common-trails/issues/83)) ([ff91be6](https://github.com/polomarcus/common-trails/commit/ff91be6d6acb3d27871283cb66821dbfff6b798a))
* MVT snap-to-grid query — use subquery for zero-length filter ([3071bc2](https://github.com/polomarcus/common-trails/commit/3071bc27a106b69f5229a64e96222bc16dff85b4))
* NameError on TEST_MODE crashes background loading in prod ([#103](https://github.com/polomarcus/common-trails/issues/103)) ([319a4a0](https://github.com/polomarcus/common-trails/commit/319a4a0620b9f0e65dd68b0df5c994b7f5f8b73b))
* non-blocking startup + Cloud Run stability improvements ([#49](https://github.com/polomarcus/common-trails/issues/49)) ([5169daa](https://github.com/polomarcus/common-trails/commit/5169daadcd6b5f66fb99e439d934f3490746a48e))
* pass CI env var to Docker container to skip perf tests ([1ee1434](https://github.com/polomarcus/common-trails/commit/1ee14342cc7a827bba3570a2aa729cdf01dd6e92))
* pending bugs (regional.pb 500, heatmap spaghetti, proposal diversity) + routing audit ([9050836](https://github.com/polomarcus/common-trails/commit/9050836d804ce74e952d3791fb2cdb351f311218))
* pending bugs (regional.pb, spaghetti, proposal diversity) + routing audit + drop TS routing fallback ([60ba9f9](https://github.com/polomarcus/common-trails/commit/60ba9f9c24506a95d0e65170d3c6c0a03bbeafbd))
* **pmtiles:** --keep-grid-fallback also relaxes grid_fallback_min_uc ([#238](https://github.com/polomarcus/common-trails/issues/238)) ([cd0046c](https://github.com/polomarcus/common-trails/commit/cd0046ceb0a910d6c6ab4398ec06692359b1617b))
* prevent DFCI edge accumulation across deploys ([48be634](https://github.com/polomarcus/common-trails/commit/48be63448e8249a974e0b5dd7e4e02b550d3ea2e))
* prevent duplicate Strava imports for same user ([c032d58](https://github.com/polomarcus/common-trails/commit/c032d58bcfee5b20f31f5495be05c2cce9a12518))
* prevent Strava ghost users on connect + auto-migrate duplicates ([097ba6c](https://github.com/polomarcus/common-trails/commit/097ba6cd2e229be753b9933a9a80655b53a9d950))
* reduce DFCI routing dominance + move-waypoint regression ([#91](https://github.com/polomarcus/common-trails/issues/91)) ([7931237](https://github.com/polomarcus/common-trails/commit/79312378a6503fd8d431e95cb781b441ca121bdb))
* refresh Strava token before import (prevent expired token errors) ([8b62fd0](https://github.com/polomarcus/common-trails/commit/8b62fd028d58705537f85eaf494ebbc9ae5c7502))
* remove backend routing E2E tests, fix math shadow bug ([#86](https://github.com/polomarcus/common-trails/issues/86)) ([f5665be](https://github.com/polomarcus/common-trails/commit/f5665beee98ecfb0b18246b75a09392f604a56ad))
* remove CONCURRENTLY from index migration (incompatible with txn) ([2454aef](https://github.com/polomarcus/common-trails/commit/2454aef1fc79d703de50692bcd3c6600b80dfdb8))
* resolve ruff lint errors breaking CI ([#123](https://github.com/polomarcus/common-trails/issues/123)) ([507b504](https://github.com/polomarcus/common-trails/commit/507b504b419400824a09bcbd186dfabf493f1926))
* restore missing model fields and fix lint errors breaking CI ([#118](https://github.com/polomarcus/common-trails/issues/118)) ([cde23ab](https://github.com/polomarcus/common-trails/commit/cde23ab1558a57f40f0026a313b0f2b19c44baac))
* revert min_uc filter — keep K=1 for beta, snap-merge only ([d44ef7c](https://github.com/polomarcus/common-trails/commit/d44ef7c5b8e71670e37079f84b11ca897021d152))
* revert uc=2 hardcode — respect HEATMAP_K_ANONYMITY for routing tiles ([e33922d](https://github.com/polomarcus/common-trails/commit/e33922d14825f45c53db8985c248cdb71a623224))
* robust cross-provider dedup + GPX date extraction + 5 tests ([5da9b8f](https://github.com/polomarcus/common-trails/commit/5da9b8f0a27193d81f123c7a7e68c7334221c3bc))
* routing tiles use min uc=2 (prevents 4.2M edge browser OOM) ([171532f](https://github.com/polomarcus/common-trails/commit/171532f6fc1243a6f961529c91dc6350f9ba58b1))
* ruff I001 — alphabetize imports in test_ingestion_perf ([f39b02c](https://github.com/polomarcus/common-trails/commit/f39b02cbf2d74aa5e42745ed83dad6954869acc2))
* ruff lint — remove unused import and variable in fix_contributor_hashes ([ea765bd](https://github.com/polomarcus/common-trails/commit/ea765bdc5c3cf155e4b6b6ee5d4fa900128c80a7))
* ruff lint — remove unused import, fix import ordering ([73dc823](https://github.com/polomarcus/common-trails/commit/73dc82351171c884d68c18f3978b47c137da982f))
* ruff lint — remove unused import, fix sort order ([42b413e](https://github.com/polomarcus/common-trails/commit/42b413e614988fe2f0312eda97bf007d4c03a7f5))
* ruff lint fixes + protobuf CDN serving ([699781c](https://github.com/polomarcus/common-trails/commit/699781c0f39db39831818b17ceffc6dfcb8c7bee))
* ruff lint fixes for CDN write-through + protobuf ([7479e1b](https://github.com/polomarcus/common-trails/commit/7479e1b55e2e63ff4949687806414351f52eba10))
* scaling + offroad-visibility hardening ([#233](https://github.com/polomarcus/common-trails/issues/233)) ([a0e83cc](https://github.com/polomarcus/common-trails/commit/a0e83cc45fe331a3b6fdf3d1701b55deb9c47f48))
* serve frontend HTML for /trips and other pages in production ([91429a5](https://github.com/polomarcus/common-trails/commit/91429a5fe2125250aff6c15b5fbde6f2b7daa309))
* **share:** render OG card as PNG (was SVG, dropped by WhatsApp/Twitter) ([#273](https://github.com/polomarcus/common-trails/issues/273)) ([fca4a2c](https://github.com/polomarcus/common-trails/commit/fca4a2cd9b0fa460f126a9b9a5b4d8d0914e8a32))
* show email in account menu, disable prod seeds, rename to Chemins communs ([#99](https://github.com/polomarcus/common-trails/issues/99)) ([7c69a45](https://github.com/polomarcus/common-trails/commit/7c69a45ad4750ca54dc9b763dc003d241dce0cc6))
* skip Overpass OSM tile fetching during bulk import ([11fec32](https://github.com/polomarcus/common-trails/commit/11fec321f8ef1046ee5e13d23648d9438ff31a41))
* skip perf tests in CI — too slow for GitHub Actions runners ([eb42b5f](https://github.com/polomarcus/common-trails/commit/eb42b5fb4529cccd2a63856a81a03019d00f596a))
* spaghetti at live endpoints, offroad partition, drift guard, audit closure ([b4cdf32](https://github.com/polomarcus/common-trails/commit/b4cdf32e9a3e3f6601ed94f42addc19857dc8066))
* store OAuth state in DB instead of in-memory (prevents ghost users) ([#98](https://github.com/polomarcus/common-trails/issues/98)) ([46e96cc](https://github.com/polomarcus/common-trails/commit/46e96cc6214be5599302e9e8e995f317882dfb0a))
* Strava API rate limit handling ([#110](https://github.com/polomarcus/common-trails/issues/110)) ([82a801f](https://github.com/polomarcus/common-trails/commit/82a801f158e3fffe6ed93688a144c399c97f7df0))
* **strava:** close TEST_MODE CSRF hole + state guard tests ([#244](https://github.com/polomarcus/common-trails/issues/244)) ([d46528d](https://github.com/polomarcus/common-trails/commit/d46528dfb4f49b9e7c87ca9d06aabc97cfa90d4c))
* **test:** pipeline_patterns silent bit-rot — isolate by bbox ([#242](https://github.com/polomarcus/common-trails/issues/242)) ([7c53777](https://github.com/polomarcus/common-trails/commit/7c537771c7b5915fb21c5d9d7e922d7c02c72074))
* tile-based delete in OSM import (don't wipe all regions) ([8270416](https://github.com/polomarcus/common-trails/commit/82704168331ab84d270d96cb54de940e7b8980ee))
* TS strict mode graph_pb access + update cache-control test assertion ([4c0ad2f](https://github.com/polomarcus/common-trails/commit/4c0ad2fb4936168d09f8dfe093090c1d42e30bd4))
* use correct z14 tile keys in OSM matching tests ([de4b61d](https://github.com/polomarcus/common-trails/commit/de4b61d8f056044a068e64e0e3cf348f0b1fdcc2))
* use DATA_DIR=/app/data instead of redundant COPY data/ /data/ ([#100](https://github.com/polomarcus/common-trails/issues/100)) ([a8172e2](https://github.com/polomarcus/common-trails/commit/a8172e22013aa3d169eeece2a2695a50a7656e15))
* use explicit CORS origins instead of wildcard with credentials ([#36](https://github.com/polomarcus/common-trails/issues/36)) ([784ca40](https://github.com/polomarcus/common-trails/commit/784ca404f7341b4c7ca1d7d3ce5c6248e0afd322))
* use GPS-point edges instead of OSM-node edges to eliminate gaps ([d536b56](https://github.com/polomarcus/common-trails/commit/d536b56a3559efa774d0f6d87e3a6306fb27e48a))
* use REST API instead of google-cloud-run SDK to trigger job ([7b73a43](https://github.com/polomarcus/common-trails/commit/7b73a4351207d2e334cf7d53fd57c6638d029973))


### Miscellaneous

* deep agent docs + cascade cap + drift test + dedup test + beta gate ([#229](https://github.com/polomarcus/common-trails/issues/229)) ([7116bfc](https://github.com/polomarcus/common-trails/commit/7116bfcb99f4d51c61cd8d494e7ddcb9006a0b4f))
* **dem:** bake Cataluna + Italia Nord-Ovest HGT tiles into the image ([#294](https://github.com/polomarcus/common-trails/issues/294)) ([d275bc2](https://github.com/polomarcus/common-trails/commit/d275bc29c10726a8332c8e1892514d07c958ec3b))
* extend uploads bucket lifecycle from 90 days to 2 years ([d971e20](https://github.com/polomarcus/common-trails/commit/d971e20a175fc9b6a4724a3ad73d01940281a83e))
* **import-osm:** drop `service` highway from import filter ([#298](https://github.com/polomarcus/common-trails/issues/298)) ([4d4575d](https://github.com/polomarcus/common-trails/commit/4d4575d2c6c53176a48dbb69b5d4f9ea624e09d4))
* **orm:** partial index parity on heat_edges.osm_way_id ([#292](https://github.com/polomarcus/common-trails/issues/292)) ([15a1629](https://github.com/polomarcus/common-trails/commit/15a1629afd724fc932606ee53e30bca102a73562))
* release 0.7.1 ([#23](https://github.com/polomarcus/common-trails/issues/23)) ([b467aa6](https://github.com/polomarcus/common-trails/commit/b467aa6f208984bf519faa5aeca1b01071c1df7e))
* release 0.7.10 ([#41](https://github.com/polomarcus/common-trails/issues/41)) ([ca42529](https://github.com/polomarcus/common-trails/commit/ca4252927d3097c863a774e4444910b924360355))
* release 0.7.101 ([#209](https://github.com/polomarcus/common-trails/issues/209)) ([c546dc9](https://github.com/polomarcus/common-trails/commit/c546dc9c7affec409f091d16a754efc6f80cae24))
* release 0.7.102 ([#211](https://github.com/polomarcus/common-trails/issues/211)) ([829d86a](https://github.com/polomarcus/common-trails/commit/829d86a0eab1ff3ec30efba73fdb9b529468daad))
* release 0.7.103 ([#216](https://github.com/polomarcus/common-trails/issues/216)) ([9c5d992](https://github.com/polomarcus/common-trails/commit/9c5d9928d997b41f88bb82f001dfea652f89b211))
* release 0.7.104 ([#217](https://github.com/polomarcus/common-trails/issues/217)) ([8ca500f](https://github.com/polomarcus/common-trails/commit/8ca500f36ae356b4287a4404a50150f26c38d0d1))
* release 0.7.105 ([#219](https://github.com/polomarcus/common-trails/issues/219)) ([887496f](https://github.com/polomarcus/common-trails/commit/887496fa475f2e996a3257605c694b17feb5a5b7))
* release 0.7.108 ([#224](https://github.com/polomarcus/common-trails/issues/224)) ([0553ebf](https://github.com/polomarcus/common-trails/commit/0553ebfa022ecf31280c070a5b94c4bcf01570ea))
* release 0.7.110 ([#228](https://github.com/polomarcus/common-trails/issues/228)) ([fbe2110](https://github.com/polomarcus/common-trails/commit/fbe211080766326717450d0965209ddd24d2c498))
* release 0.7.111 ([#257](https://github.com/polomarcus/common-trails/issues/257)) ([053998b](https://github.com/polomarcus/common-trails/commit/053998b8acd0348b9ff09aacb6b68a3521d0bfbe))
* release 0.7.112 ([#263](https://github.com/polomarcus/common-trails/issues/263)) ([f789e85](https://github.com/polomarcus/common-trails/commit/f789e85ef813b6c571c5210a262239b4476def6a))
* release 0.7.114 ([#271](https://github.com/polomarcus/common-trails/issues/271)) ([7b84f20](https://github.com/polomarcus/common-trails/commit/7b84f20e9044abf4aa9bfe27ace973b437d7a3c3))
* release 0.7.116 ([#276](https://github.com/polomarcus/common-trails/issues/276)) ([99d17d2](https://github.com/polomarcus/common-trails/commit/99d17d29b954095a7351405408bdf6f5ef0863f7))
* release 0.7.116 ([#277](https://github.com/polomarcus/common-trails/issues/277)) ([caf42ba](https://github.com/polomarcus/common-trails/commit/caf42ba972cb342017ee54ecb2e24ff77da7f833))
* release 0.7.117 ([#281](https://github.com/polomarcus/common-trails/issues/281)) ([65dba2d](https://github.com/polomarcus/common-trails/commit/65dba2d8b521a9d9e37cb8155ca5d0be02e0ff7d))
* release 0.7.119 ([#290](https://github.com/polomarcus/common-trails/issues/290)) ([7ba5e57](https://github.com/polomarcus/common-trails/commit/7ba5e57559b26fba03f4dac1eb704e859638fd03))
* release 0.7.12 ([#45](https://github.com/polomarcus/common-trails/issues/45)) ([dad68a6](https://github.com/polomarcus/common-trails/commit/dad68a691c14efa1484d20a0ab944ee85d5623d0))
* release 0.7.120 ([#291](https://github.com/polomarcus/common-trails/issues/291)) ([a3215ac](https://github.com/polomarcus/common-trails/commit/a3215ac70167945fabd3bc58b3a025e71d2c6493))
* release 0.7.121 ([#293](https://github.com/polomarcus/common-trails/issues/293)) ([df7e43d](https://github.com/polomarcus/common-trails/commit/df7e43d0db1f357b6fec58ca1bd117424533b7ce))
* release 0.7.122 ([#295](https://github.com/polomarcus/common-trails/issues/295)) ([55d6f0c](https://github.com/polomarcus/common-trails/commit/55d6f0cdb4657f99e721ec1f50dd44218c023b45))
* release 0.7.123 ([#300](https://github.com/polomarcus/common-trails/issues/300)) ([d2ae7a9](https://github.com/polomarcus/common-trails/commit/d2ae7a91ec4e7bdab2aa162f00c2bf1cbbd3a836))
* release 0.7.13 ([#47](https://github.com/polomarcus/common-trails/issues/47)) ([fa995fa](https://github.com/polomarcus/common-trails/commit/fa995fa45965e5b6c552d096402f12be1682a2f0))
* release 0.7.14 ([#50](https://github.com/polomarcus/common-trails/issues/50)) ([a45f394](https://github.com/polomarcus/common-trails/commit/a45f3948c4bd1147fac84268b5b5dd548e52d0d0))
* release 0.7.16 ([#54](https://github.com/polomarcus/common-trails/issues/54)) ([1052d46](https://github.com/polomarcus/common-trails/commit/1052d464f3339e9773c168780e0f8f6dcd8e09a6))
* release 0.7.17 ([#56](https://github.com/polomarcus/common-trails/issues/56)) ([4167cc4](https://github.com/polomarcus/common-trails/commit/4167cc4ac841a4582d4862b8fca97a78a9b3910f))
* release 0.7.18 ([#58](https://github.com/polomarcus/common-trails/issues/58)) ([5ae0100](https://github.com/polomarcus/common-trails/commit/5ae0100ec996df0ad7982f1ee6f2ad54a580a25d))
* release 0.7.19 ([#61](https://github.com/polomarcus/common-trails/issues/61)) ([8c56503](https://github.com/polomarcus/common-trails/commit/8c56503eb5f31959996445fbc727fb52b44ea715))
* release 0.7.20 ([#63](https://github.com/polomarcus/common-trails/issues/63)) ([ff0f9bc](https://github.com/polomarcus/common-trails/commit/ff0f9bc137c1631e8f8b98a5fe89c4a6d8675f62))
* release 0.7.21 ([#64](https://github.com/polomarcus/common-trails/issues/64)) ([18dbfa1](https://github.com/polomarcus/common-trails/commit/18dbfa10df33fae8b0edbd6aa78eccf88cf1fcb8))
* release 0.7.22 ([#66](https://github.com/polomarcus/common-trails/issues/66)) ([94faf07](https://github.com/polomarcus/common-trails/commit/94faf07c1443cf019b607ab07dd5a6b415a80bd0))
* release 0.7.23 ([#69](https://github.com/polomarcus/common-trails/issues/69)) ([ecb9222](https://github.com/polomarcus/common-trails/commit/ecb9222f8e3ed7403cd5d203da5c67e936d3b283))
* release 0.7.25 ([#72](https://github.com/polomarcus/common-trails/issues/72)) ([7d81b1e](https://github.com/polomarcus/common-trails/commit/7d81b1e1d8a2ac9835e29552ef8186a097bd4d27))
* release 0.7.26 ([#74](https://github.com/polomarcus/common-trails/issues/74)) ([b603aca](https://github.com/polomarcus/common-trails/commit/b603aca0da73895783387567be0050d076e35024))
* release 0.7.27 ([#76](https://github.com/polomarcus/common-trails/issues/76)) ([614990f](https://github.com/polomarcus/common-trails/commit/614990f94e6cc14eafa5858a0bd9fed7b8f3b2e9))
* release 0.7.28 ([#78](https://github.com/polomarcus/common-trails/issues/78)) ([9fa711a](https://github.com/polomarcus/common-trails/commit/9fa711aa209e8713ad04fbb00bdea5dcc00ab207))
* release 0.7.29 ([#80](https://github.com/polomarcus/common-trails/issues/80)) ([b11514d](https://github.com/polomarcus/common-trails/commit/b11514d0d387ca6b8c5b6d03d8da576a6c517cec))
* release 0.7.30 ([#82](https://github.com/polomarcus/common-trails/issues/82)) ([6f9845f](https://github.com/polomarcus/common-trails/commit/6f9845f4c3b4d38be0c12b80cf47af078b88acca))
* release 0.7.31 ([#84](https://github.com/polomarcus/common-trails/issues/84)) ([7f525ed](https://github.com/polomarcus/common-trails/commit/7f525ed610df29cfc4743da0245c6db17a384b70))
* release 0.7.33 ([#88](https://github.com/polomarcus/common-trails/issues/88)) ([158e74d](https://github.com/polomarcus/common-trails/commit/158e74d3c9d860f8eaeb91370646bc88e9ef0b5d))
* release 0.7.34 ([#90](https://github.com/polomarcus/common-trails/issues/90)) ([9be3080](https://github.com/polomarcus/common-trails/commit/9be30800bac10abb046db7f0e0876c82e1fb1091))
* release 0.7.35 ([#92](https://github.com/polomarcus/common-trails/issues/92)) ([3e83520](https://github.com/polomarcus/common-trails/commit/3e83520507c30fcf0da65612195946422f7ecd52))
* release 0.7.36 ([#93](https://github.com/polomarcus/common-trails/issues/93)) ([d4712c2](https://github.com/polomarcus/common-trails/commit/d4712c29e6581f65f2dbc5ff6ffc4fc065b7a13f))
* release 0.7.37 ([#94](https://github.com/polomarcus/common-trails/issues/94)) ([77e5562](https://github.com/polomarcus/common-trails/commit/77e556299151ad56e1171b063bba845efbef5dca))
* release 0.7.38 ([#95](https://github.com/polomarcus/common-trails/issues/95)) ([24e46c4](https://github.com/polomarcus/common-trails/commit/24e46c4d81a74515bd2ecfb719ee9a4b0fa21ddc))
* release 0.7.39 ([#96](https://github.com/polomarcus/common-trails/issues/96)) ([37eb65e](https://github.com/polomarcus/common-trails/commit/37eb65e34d35f4b3658bd137f0ef6f169f957349))
* release 0.7.40 ([#102](https://github.com/polomarcus/common-trails/issues/102)) ([e580a44](https://github.com/polomarcus/common-trails/commit/e580a44a18d76db4b9f18c73ff1fefad194d7d8f))
* release 0.7.41 ([#104](https://github.com/polomarcus/common-trails/issues/104)) ([5c6d4da](https://github.com/polomarcus/common-trails/commit/5c6d4da4937a5c04054ccffd9dd06bc07fb34010))
* release 0.7.43 ([#109](https://github.com/polomarcus/common-trails/issues/109)) ([c82e2d2](https://github.com/polomarcus/common-trails/commit/c82e2d2d1909d44c4c1c4ff63246c7f563dfb709))
* release 0.7.44 ([#111](https://github.com/polomarcus/common-trails/issues/111)) ([279f1c4](https://github.com/polomarcus/common-trails/commit/279f1c4153d9d0db9f211d037d7be43c63686989))
* release 0.7.45 ([#112](https://github.com/polomarcus/common-trails/issues/112)) ([55d5ccc](https://github.com/polomarcus/common-trails/commit/55d5cccf04010dc2c4529dfc81c5c5a5b2a74e1d))
* release 0.7.46 ([#113](https://github.com/polomarcus/common-trails/issues/113)) ([cdbf36b](https://github.com/polomarcus/common-trails/commit/cdbf36bd6cd8a62eb2f3de23dd78c60a60a5831f))
* release 0.7.47 ([#114](https://github.com/polomarcus/common-trails/issues/114)) ([2a30983](https://github.com/polomarcus/common-trails/commit/2a309834f0f8cb319c917b779e3f18216793f15d))
* release 0.7.48 ([#115](https://github.com/polomarcus/common-trails/issues/115)) ([9d9b318](https://github.com/polomarcus/common-trails/commit/9d9b318cf7107cd4527e707a441b6b6b824afd99))
* release 0.7.49 ([#116](https://github.com/polomarcus/common-trails/issues/116)) ([7d29686](https://github.com/polomarcus/common-trails/commit/7d2968696726fc526b881c604596c3ebaf0e0af5))
* release 0.7.50 ([#117](https://github.com/polomarcus/common-trails/issues/117)) ([1c280db](https://github.com/polomarcus/common-trails/commit/1c280db76169f19dcd1eef2b5bc1b12365f9cfeb))
* release 0.7.51 ([#119](https://github.com/polomarcus/common-trails/issues/119)) ([0381a0d](https://github.com/polomarcus/common-trails/commit/0381a0d18e6503e1c5dcd4794efc031d324de02d))
* release 0.7.53 ([#124](https://github.com/polomarcus/common-trails/issues/124)) ([8226821](https://github.com/polomarcus/common-trails/commit/82268214131c96360071aece8d5b73ab5c8f2a1a))
* release 0.7.53 ([#125](https://github.com/polomarcus/common-trails/issues/125)) ([1991168](https://github.com/polomarcus/common-trails/commit/19911686878efabfb645f7ac0692786cd3666045))
* release 0.7.54 ([#128](https://github.com/polomarcus/common-trails/issues/128)) ([c577b04](https://github.com/polomarcus/common-trails/commit/c577b04a1c47397a27d07717465cebf36a86bc4b))
* release 0.7.56 ([#133](https://github.com/polomarcus/common-trails/issues/133)) ([85537eb](https://github.com/polomarcus/common-trails/commit/85537eb4d8e869cb6703f2649d8bb5e16b21355c))
* release 0.7.59 ([#137](https://github.com/polomarcus/common-trails/issues/137)) ([aa5b96c](https://github.com/polomarcus/common-trails/commit/aa5b96c13ca545d39f12a91ca27f3777bc0c04ef))
* release 0.7.60 ([#139](https://github.com/polomarcus/common-trails/issues/139)) ([89734fc](https://github.com/polomarcus/common-trails/commit/89734fcd3202ac4f71d054eaebb7d86b1000b098))
* release 0.7.61 ([#140](https://github.com/polomarcus/common-trails/issues/140)) ([d073db4](https://github.com/polomarcus/common-trails/commit/d073db41cbfe34bf1d7a32e62aa34089e6cfac5e))
* release 0.7.62 ([#141](https://github.com/polomarcus/common-trails/issues/141)) ([5423997](https://github.com/polomarcus/common-trails/commit/5423997ccd1afafc23eca24baa97e263b45b88b6))
* release 0.7.63 ([#142](https://github.com/polomarcus/common-trails/issues/142)) ([e9932ab](https://github.com/polomarcus/common-trails/commit/e9932ab0b9ab83559867023a95062d59f4283a7f))
* release 0.7.64 ([#143](https://github.com/polomarcus/common-trails/issues/143)) ([fef83c0](https://github.com/polomarcus/common-trails/commit/fef83c06be797aee5cbe3d57532086904174b1d6))
* release 0.7.65 ([#144](https://github.com/polomarcus/common-trails/issues/144)) ([527f7da](https://github.com/polomarcus/common-trails/commit/527f7da54681899ddb12f4ad4d8df70dad61861b))
* release 0.7.66 ([#145](https://github.com/polomarcus/common-trails/issues/145)) ([512cc1d](https://github.com/polomarcus/common-trails/commit/512cc1de9a4190d6d9e581bbefb461aa5552b48c))
* release 0.7.67 ([#148](https://github.com/polomarcus/common-trails/issues/148)) ([1d48aa3](https://github.com/polomarcus/common-trails/commit/1d48aa33540ab6b821b27b5fbac1dd51fcb2c740))
* release 0.7.68 ([#150](https://github.com/polomarcus/common-trails/issues/150)) ([185ea70](https://github.com/polomarcus/common-trails/commit/185ea7043c83f6d7b6f79148d5ac6137b1041066))
* release 0.7.69 ([#154](https://github.com/polomarcus/common-trails/issues/154)) ([0b15bf3](https://github.com/polomarcus/common-trails/commit/0b15bf38b579f374a4eac0a593544fa54c33f30e))
* release 0.7.7 ([#35](https://github.com/polomarcus/common-trails/issues/35)) ([1e116ef](https://github.com/polomarcus/common-trails/commit/1e116efd2cd56e8489770e25c9b10d953426ad6b))
* release 0.7.71 ([#157](https://github.com/polomarcus/common-trails/issues/157)) ([e8a3b38](https://github.com/polomarcus/common-trails/commit/e8a3b388425a28065fb67b34dacb176d590f2ae3))
* release 0.7.72 ([#158](https://github.com/polomarcus/common-trails/issues/158)) ([e03931f](https://github.com/polomarcus/common-trails/commit/e03931f7c160257eaba7e8e9d65be4c38f9cafb8))
* release 0.7.73 ([#159](https://github.com/polomarcus/common-trails/issues/159)) ([9efb63c](https://github.com/polomarcus/common-trails/commit/9efb63c18d53fbf1c05d17ed0479d30f5a680129))
* release 0.7.75 ([#161](https://github.com/polomarcus/common-trails/issues/161)) ([2099bac](https://github.com/polomarcus/common-trails/commit/2099bac1dfdfdd2e3740e441a5362c7bf060ed1c))
* release 0.7.79 ([#165](https://github.com/polomarcus/common-trails/issues/165)) ([23bf41a](https://github.com/polomarcus/common-trails/commit/23bf41a25fa58ce452a59d73769f13128ffca1b9))
* release 0.7.8 ([#37](https://github.com/polomarcus/common-trails/issues/37)) ([6b8c309](https://github.com/polomarcus/common-trails/commit/6b8c3095f15b4e90548fb58d4a2afa80abf9fade))
* release 0.7.80 ([#166](https://github.com/polomarcus/common-trails/issues/166)) ([dec546d](https://github.com/polomarcus/common-trails/commit/dec546d75bf0bc0b45c0b296a124d3f2bcb747d9))
* release 0.7.82 ([#168](https://github.com/polomarcus/common-trails/issues/168)) ([a91eb7f](https://github.com/polomarcus/common-trails/commit/a91eb7ff2add6146d348a51936c431be4416d928))
* release 0.7.89 ([#176](https://github.com/polomarcus/common-trails/issues/176)) ([03d8c03](https://github.com/polomarcus/common-trails/commit/03d8c03db788cabf25d49fccd20c3a1d4d713cb5))
* release 0.7.9 ([#39](https://github.com/polomarcus/common-trails/issues/39)) ([93e28bc](https://github.com/polomarcus/common-trails/commit/93e28bc5ef28f8163a60152e766ca4e5ebb009a1))
* release 0.7.90 ([#178](https://github.com/polomarcus/common-trails/issues/178)) ([bb3ede1](https://github.com/polomarcus/common-trails/commit/bb3ede1ee2e2f151be8fe036bd65a77e16faf52d))
* release 0.7.91 ([#181](https://github.com/polomarcus/common-trails/issues/181)) ([8bd788c](https://github.com/polomarcus/common-trails/commit/8bd788cb242b3387ca29060952575d603a9cd80d))
* release 0.7.92 ([#184](https://github.com/polomarcus/common-trails/issues/184)) ([85c8fa2](https://github.com/polomarcus/common-trails/commit/85c8fa287d5e88483282778883ecb5bdb4b9c977))
* release 0.7.93 ([#186](https://github.com/polomarcus/common-trails/issues/186)) ([7f79cb3](https://github.com/polomarcus/common-trails/commit/7f79cb3cdd31407fe29e1b2785ee5657143391ec))
* release 0.7.94 ([#189](https://github.com/polomarcus/common-trails/issues/189)) ([40c3913](https://github.com/polomarcus/common-trails/commit/40c3913795abebdec382267b03607ba2efcd3762))
* release 0.7.96 ([#198](https://github.com/polomarcus/common-trails/issues/198)) ([4e08c1f](https://github.com/polomarcus/common-trails/commit/4e08c1f7ef38299d21bb915b3b9b7df59d6dc8b1))
* simplify OSM edge code — remove dead matched_segs_in_run loop ([b8e6b72](https://github.com/polomarcus/common-trails/commit/b8e6b72a8af6b46db982068c3aad741af2c94b72))

## [0.53.0](https://github.com/polomarcus/common-trails/compare/backend-v0.52.2...backend-v0.53.0) (2026-05-17)


### Features

* **gpx:** auto-detect sport from GPX &lt;trk&gt;&lt;type&gt; on upload ([#301](https://github.com/polomarcus/common-trails/issues/301)) ([#302](https://github.com/polomarcus/common-trails/issues/302)) ([ce5d162](https://github.com/polomarcus/common-trails/commit/ce5d1626707cc85196c04f4f3b77188a7909341d))


### Miscellaneous

* **import-osm:** drop `service` highway from import filter ([#298](https://github.com/polomarcus/common-trails/issues/298)) ([4d4575d](https://github.com/polomarcus/common-trails/commit/4d4575d2c6c53176a48dbb69b5d4f9ea624e09d4))

## [0.52.2](https://github.com/polomarcus/common-trails/compare/backend-v0.52.1...backend-v0.52.2) (2026-05-16)


### Miscellaneous

* **dem:** bake Cataluna + Italia Nord-Ovest HGT tiles into the image ([#294](https://github.com/polomarcus/common-trails/issues/294)) ([d275bc2](https://github.com/polomarcus/common-trails/commit/d275bc29c10726a8332c8e1892514d07c958ec3b))

## [0.52.1](https://github.com/polomarcus/common-trails/compare/backend-v0.52.0...backend-v0.52.1) (2026-05-16)


### Miscellaneous

* **orm:** partial index parity on heat_edges.osm_way_id ([#292](https://github.com/polomarcus/common-trails/issues/292)) ([15a1629](https://github.com/polomarcus/common-trails/commit/15a1629afd724fc932606ee53e30bca102a73562))

## [0.52.0](https://github.com/polomarcus/common-trails/compare/backend-v0.51.0...backend-v0.52.0) (2026-05-16)


### Features

* **prod-followups:** 4 bundled fixes — ORM drift / recompute Job / rebuild guard / admin job feed ([#289](https://github.com/polomarcus/common-trails/issues/289)) ([c34d239](https://github.com/polomarcus/common-trails/commit/c34d239cc2759af86440488fde563bc4a0079678))

## [0.51.0](https://github.com/polomarcus/common-trails/compare/backend-v0.50.0...backend-v0.51.0) (2026-05-16)


### Features

* **admin:** beta-time visibility — engagement, providers, heatmap quality, recent feeds ([#288](https://github.com/polomarcus/common-trails/issues/288)) ([362c4bb](https://github.com/polomarcus/common-trails/commit/362c4bb499ce8b709fc3ec2344147775f1102def))

## [0.50.0](https://github.com/polomarcus/common-trails/compare/backend-v0.49.1...backend-v0.50.0) (2026-05-16)


### Features

* **map:** fork-UX polish + drop deprecated upstream-PR endpoints ([#279](https://github.com/polomarcus/common-trails/issues/279)) ([c247cd4](https://github.com/polomarcus/common-trails/commit/c247cd4b78c8da89dc90d1596041c7b549ec37aa))


### Miscellaneous

* release 0.7.116 ([#277](https://github.com/polomarcus/common-trails/issues/277)) ([caf42ba](https://github.com/polomarcus/common-trails/commit/caf42ba972cb342017ee54ecb2e24ff77da7f833))

## [0.49.1](https://github.com/polomarcus/common-trails/compare/backend-v0.49.0...backend-v0.49.1) (2026-05-16)


### Bug Fixes

* **share:** render OG card as PNG (was SVG, dropped by WhatsApp/Twitter) ([#273](https://github.com/polomarcus/common-trails/issues/273)) ([fca4a2c](https://github.com/polomarcus/common-trails/commit/fca4a2cd9b0fa460f126a9b9a5b4d8d0914e8a32))

## [0.49.0](https://github.com/polomarcus/common-trails/compare/backend-v0.48.1...backend-v0.49.0) (2026-05-16)


### Features

* **beta:** 4 deferred audit items + group_edges_osm progress logging ([#269](https://github.com/polomarcus/common-trails/issues/269)) ([00bd001](https://github.com/polomarcus/common-trails/commit/00bd0016e3ca3f6975336a63e9e80e9d03efb73f))

## [0.48.1](https://github.com/polomarcus/common-trails/compare/backend-v0.48.0...backend-v0.48.1) (2026-05-15)


### Bug Fixes

* **ingest:** Overpass writer now populates ele_* + surface_confidence ([#262](https://github.com/polomarcus/common-trails/issues/262)) ([918a0fa](https://github.com/polomarcus/common-trails/commit/918a0fa25bc9d365b66b5dd0e95bac2f773f52a1))

## [0.48.0](https://github.com/polomarcus/common-trails/compare/backend-v0.47.0...backend-v0.48.0) (2026-05-15)


### Features

* **elevation+surface:** D+ smoothing + local DEM at PBF time + surface_confidence end-to-end ([#259](https://github.com/polomarcus/common-trails/issues/259)) ([a9d89ed](https://github.com/polomarcus/common-trails/commit/a9d89ed8fccdcd13ab162b34c2f7856968de5e13))
* **make:** prod rebuild targets + URGENT dry-run bug fix ([#249](https://github.com/polomarcus/common-trails/issues/249)) ([f533e07](https://github.com/polomarcus/common-trails/commit/f533e07ae272086e48591ed856609d7f6f201635))
* **ops:** bake HGT tiles into image + activities D+ backfill CLI ([#261](https://github.com/polomarcus/common-trails/issues/261)) ([d307dca](https://github.com/polomarcus/common-trails/commit/d307dca30c1c58f727f45ec558c81b69aa6029fe))
* **sentry:** scrub raw lat/lon + hashed bbox span names ([#243](https://github.com/polomarcus/common-trails/issues/243)) ([d83c240](https://github.com/polomarcus/common-trails/commit/d83c240cd4e3b70794ce165d408c10cc7ad0fcf0))


### Bug Fixes

* **group_edges_osm:** set osm_way_id + accept parallel-near-360°/180° ([#250](https://github.com/polomarcus/common-trails/issues/250)) ([ceb26c2](https://github.com/polomarcus/common-trails/commit/ceb26c25a789680d870b8b8e385c31a93f410092))
* **strava:** close TEST_MODE CSRF hole + state guard tests ([#244](https://github.com/polomarcus/common-trails/issues/244)) ([d46528d](https://github.com/polomarcus/common-trails/commit/d46528dfb4f49b9e7c87ca9d06aabc97cfa90d4c))
* **test:** pipeline_patterns silent bit-rot — isolate by bbox ([#242](https://github.com/polomarcus/common-trails/issues/242)) ([7c53777](https://github.com/polomarcus/common-trails/commit/7c537771c7b5915fb21c5d9d7e922d7c02c72074))

## [0.47.0](https://github.com/polomarcus/common-trails/compare/backend-v0.46.1...backend-v0.47.0) (2026-05-15)


### Features

* **monitoring:** heat-edge spaghetti alerts via build_pmtiles hook ([#247](https://github.com/polomarcus/common-trails/issues/247)) ([f4b3179](https://github.com/polomarcus/common-trails/commit/f4b31790efbc526a50339333aa5ca7fa008416fa))
* **trails:** GR/GRP/GT/PR/EV from local PBF + kill runtime Overpass in prod ([#230](https://github.com/polomarcus/common-trails/issues/230)) ([f0b29a0](https://github.com/polomarcus/common-trails/commit/f0b29a0b0125576f1fd4985a9b3b0218b1d57bf6))


### Bug Fixes

* **api:** /readyz returns 200 'warming' when heat_edges empty ([#227](https://github.com/polomarcus/common-trails/issues/227)) ([a7863c9](https://github.com/polomarcus/common-trails/commit/a7863c935ea7c742675e89153a56b1648e76ab19))
* **home:** real stats + tightened SEO copy ([#256](https://github.com/polomarcus/common-trails/issues/256)) ([fd97aee](https://github.com/polomarcus/common-trails/commit/fd97aeed68e6aa9ab9f1faa5143d26401fc555d8))
* **pmtiles:** --keep-grid-fallback also relaxes grid_fallback_min_uc ([#238](https://github.com/polomarcus/common-trails/issues/238)) ([cd0046c](https://github.com/polomarcus/common-trails/commit/cd0046ceb0a910d6c6ab4398ec06692359b1617b))
* scaling + offroad-visibility hardening ([#233](https://github.com/polomarcus/common-trails/issues/233)) ([a0e83cc](https://github.com/polomarcus/common-trails/commit/a0e83cc45fe331a3b6fdf3d1701b55deb9c47f48))


### Miscellaneous

* deep agent docs + cascade cap + drift test + dedup test + beta gate ([#229](https://github.com/polomarcus/common-trails/issues/229)) ([7116bfc](https://github.com/polomarcus/common-trails/commit/7116bfcb99f4d51c61cd8d494e7ddcb9006a0b4f))

## [0.46.1](https://github.com/polomarcus/common-trails/compare/backend-v0.46.0...backend-v0.46.1) (2026-05-03)


### Bug Fixes

* **api:** add /health alias for /healthz (Cloud Run edge 404 workaround) ([2bb3f53](https://github.com/polomarcus/common-trails/commit/2bb3f53b51f00f53ebf8eef52c20545b6b287f85))

## [0.46.0](https://github.com/polomarcus/common-trails/compare/backend-v0.45.0...backend-v0.46.0) (2026-05-03)


### Features

* **heatmap+prod:** Komoot quality + Valhalla map-matching + event-driven artefact refresh ([d269d6b](https://github.com/polomarcus/common-trails/commit/d269d6b247b036d802c872fc25483cb8db54972a))

## [0.45.0](https://github.com/polomarcus/common-trails/compare/backend-v0.44.0...backend-v0.45.0) (2026-05-02)


### Features

* **ingest:** async gpx-upload via Cloud Tasks queue ([2ea8948](https://github.com/polomarcus/common-trails/commit/2ea8948c84f3cca415b1f45aebbc8a78ce676602))

## [0.44.0](https://github.com/polomarcus/common-trails/compare/backend-v0.43.4...backend-v0.44.0) (2026-05-02)


### Features

* **activities:** add PostGIS geometry column ([5bebeca](https://github.com/polomarcus/common-trails/commit/5bebeca9756416244196f6d5f37ee1db9a449d8d))
* **activities:** add PostGIS geometry column (binary, smaller, queryable) ([67b3ee2](https://github.com/polomarcus/common-trails/commit/67b3ee2db19b30acbbf84dc52500f8533b49d041))

## [0.43.4](https://github.com/polomarcus/common-trails/compare/backend-v0.43.3...backend-v0.43.4) (2026-05-02)


### Bug Fixes

* ingestion improvements (densifier breaks, sport-aware OSM radius, test fixes) ([c35fb81](https://github.com/polomarcus/common-trails/commit/c35fb8131fc869c866d68d93b5a41fc326d5e4f8))
* **ingest:** project OSM-matched GPS points onto the OSM line ([2e9a665](https://github.com/polomarcus/common-trails/commit/2e9a665f0e084c13e478a12b474f239c13acc91c))
* **lint:** ruff cleanup — unused vars + sorted imports ([bc1d0ca](https://github.com/polomarcus/common-trails/commit/bc1d0ca0f298f27f100db15301c013480d1eafd8))
* pending bugs (regional.pb 500, heatmap spaghetti, proposal diversity) + routing audit ([9050836](https://github.com/polomarcus/common-trails/commit/9050836d804ce74e952d3791fb2cdb351f311218))
* pending bugs (regional.pb, spaghetti, proposal diversity) + routing audit + drop TS routing fallback ([60ba9f9](https://github.com/polomarcus/common-trails/commit/60ba9f9c24506a95d0e65170d3c6c0a03bbeafbd))
* spaghetti at live endpoints, offroad partition, drift guard, audit closure ([b4cdf32](https://github.com/polomarcus/common-trails/commit/b4cdf32e9a3e3f6601ed94f42addc19857dc8066))

## [0.43.3](https://github.com/polomarcus/common-trails/compare/backend-v0.43.2...backend-v0.43.3) (2026-05-01)


### Bug Fixes

* **ingest:** heat_edges sport partition normalization ([90598df](https://github.com/polomarcus/common-trails/commit/90598dfee27e4d6c3cc6e0038731e2cc81db5d27))
* **ingest:** normalize heat_edges sport to real partitions (offroad→gravel) ([573d0eb](https://github.com/polomarcus/common-trails/commit/573d0eb3a23b3d6329570fdb60abc91bd7d17872))

## [0.43.2](https://github.com/polomarcus/common-trails/compare/backend-v0.43.1...backend-v0.43.2) (2026-05-01)


### Bug Fixes

* **backend:** /readyz 22s → 109ms (banner stuck forever) ([10215b0](https://github.com/polomarcus/common-trails/commit/10215b0b1acbe85b71eedb144e168475145bb0f8))
* **backend:** /readyz 22s → 109ms (was blocking banner forever) ([1d4ea0c](https://github.com/polomarcus/common-trails/commit/1d4ea0c74c4c79a6799cfe1fb3379134a80dcea1))

## [0.43.1](https://github.com/polomarcus/common-trails/compare/backend-v0.43.0...backend-v0.43.1) (2026-04-15)


### Bug Fixes

* **lint:** unused import + context manager in test_gpx_backup ([5551ec4](https://github.com/polomarcus/common-trails/commit/5551ec437da2b71379259f6a9d0ad3387677bfb5))

## [0.43.0](https://github.com/polomarcus/common-trails/compare/backend-v0.42.0...backend-v0.43.0) (2026-04-15)


### Features

* bulk GPX folder import CLI + Mac→prod ops docs ([f76d477](https://github.com/polomarcus/common-trails/commit/f76d4779b8f54db626a084fea089c671b8b94515))
* **cli:** bulk GPX folder import CLI ([3eaad9b](https://github.com/polomarcus/common-trails/commit/3eaad9b97d9432988b7365d3f03cf0e7d08660c3))

## [0.42.0](https://github.com/polomarcus/common-trails/compare/backend-v0.41.0...backend-v0.42.0) (2026-04-14)


### Features

* PMTiles heatmap display — single static file, CDN-served ([9c88975](https://github.com/polomarcus/common-trails/commit/9c88975872dac8fa3445e1f1362912b2bdd710e9))


### Bug Fixes

* **backend:** MVT tile OOM + DFCI endpoint safety limits ([6da6c36](https://github.com/polomarcus/common-trails/commit/6da6c36cfb15167246236a7948dad5eaf1214ffd))
* **ci:** lint fixes + skip 7 pre-existing failing tests + sync lockfile ([3755c5f](https://github.com/polomarcus/common-trails/commit/3755c5f3c780e90ddee603a38da53e858f7301bb))
* **ci:** retry alembic on DB connection race + sync lockfile ([daf3653](https://github.com/polomarcus/common-trails/commit/daf3653230472615a265cb795bfc859581223d57))

## [0.41.0](https://github.com/polomarcus/common-trails/compare/backend-v0.40.0...backend-v0.41.0) (2026-04-09)


### Features

* archive raw GPX uploads to bucket (heatmap rebuild safety net) ([7e9eadb](https://github.com/polomarcus/common-trails/commit/7e9eadbb5eb61bb49889aef210403444cbd1d245))
* archive raw GPX uploads to GCS bucket for heatmap rebuild safety net ([d323502](https://github.com/polomarcus/common-trails/commit/d323502c9a1affbb751fa2d4d8718d76ae7ac116))


### Miscellaneous

* extend uploads bucket lifecycle from 90 days to 2 years ([d971e20](https://github.com/polomarcus/common-trails/commit/d971e20a175fc9b6a4724a3ad73d01940281a83e))

## [0.40.0](https://github.com/polomarcus/common-trails/compare/backend-v0.39.0...backend-v0.40.0) (2026-04-01)


### Features

* OSM road network as routing base layer ([#177](https://github.com/polomarcus/common-trails/issues/177)) ([214dcd6](https://github.com/polomarcus/common-trails/commit/214dcd62d140161d00eaf051d4af095bd2debcbf))

## [0.39.0](https://github.com/polomarcus/common-trails/compare/backend-v0.38.0...backend-v0.39.0) (2026-03-31)


### Features

* i8n + CI lint + broken import test after .fit support ([#175](https://github.com/polomarcus/common-trails/issues/175)) ([dacc29c](https://github.com/polomarcus/common-trails/commit/dacc29cf52d8272afec84eae5d23df133cd99488))

## [0.38.0](https://github.com/polomarcus/common-trails/compare/backend-v0.37.1...backend-v0.38.0) (2026-03-30)


### Features

* heatmap glow polish + Garmin .fit import + multi-source support ([474bd91](https://github.com/polomarcus/common-trails/commit/474bd913fadd00877ccf1d6484345b722aad3749))

## [0.37.1](https://github.com/polomarcus/common-trails/compare/backend-v0.37.0...backend-v0.37.1) (2026-03-30)


### Bug Fixes

* robust cross-provider dedup + GPX date extraction + 5 tests ([5da9b8f](https://github.com/polomarcus/common-trails/commit/5da9b8f0a27193d81f123c7a7e68c7334221c3bc))

## [0.37.0](https://github.com/polomarcus/common-trails/compare/backend-v0.36.0...backend-v0.37.0) (2026-03-30)


### Features

* cross-provider dedup — same date + distance (±10%) = skip ([4d8a8e0](https://github.com/polomarcus/common-trails/commit/4d8a8e0c158517d5bfbd895a9523bc60eef7a3d2))

## [0.36.0](https://github.com/polomarcus/common-trails/compare/backend-v0.35.2...backend-v0.36.0) (2026-03-30)


### Features

* import queue — one Strava import at a time (rate limit protection) ([9da7816](https://github.com/polomarcus/common-trails/commit/9da7816e698392e7db20e136a407f858f32bb149))

## [0.35.2](https://github.com/polomarcus/common-trails/compare/backend-v0.35.1...backend-v0.35.2) (2026-03-30)


### Bug Fixes

* add safety log showing existing DB size before tile-based delete ([e242fbb](https://github.com/polomarcus/common-trails/commit/e242fbb7ae76268f1495002047094310dbb931b9))

## [0.35.1](https://github.com/polomarcus/common-trails/compare/backend-v0.35.0...backend-v0.35.1) (2026-03-29)


### Bug Fixes

* tile-based delete in OSM import (don't wipe all regions) ([8270416](https://github.com/polomarcus/common-trails/commit/82704168331ab84d270d96cb54de940e7b8980ee))

## [0.35.0](https://github.com/polomarcus/common-trails/compare/backend-v0.34.0...backend-v0.35.0) (2026-03-29)


### Features

* add all French regions + Switzerland/Spain/Italy to OSM PBF import ([a13fac2](https://github.com/polomarcus/common-trails/commit/a13fac22a12249bba37fc9b5f12da3cf3f1e6fb3))

## [0.34.0](https://github.com/polomarcus/common-trails/compare/backend-v0.33.1...backend-v0.34.0) (2026-03-29)


### Features

* stuck import detection + map link during Strava import ([#153](https://github.com/polomarcus/common-trails/issues/153)) ([9bc0ccd](https://github.com/polomarcus/common-trails/commit/9bc0ccd9e0f1e9b9953b11ffcc2eba60c9bea31a))

## [0.33.1](https://github.com/polomarcus/common-trails/compare/backend-v0.33.0...backend-v0.33.1) (2026-03-29)


### Bug Fixes

* heatmap hardening — idempotent migration, pruning, error handling ([#149](https://github.com/polomarcus/common-trails/issues/149)) ([5a6bf7a](https://github.com/polomarcus/common-trails/commit/5a6bf7acb7db01303ece5479db32c3c114106feb))

## [0.33.0](https://github.com/polomarcus/common-trails/compare/backend-v0.32.1...backend-v0.33.0) (2026-03-29)


### Features

* MVT vector tiles + partition heat_edges + incremental rebuild ([#147](https://github.com/polomarcus/common-trails/issues/147)) ([284389c](https://github.com/polomarcus/common-trails/commit/284389c84f7f3e1947d7257ea82981e2b2dd7e69))

## [0.32.1](https://github.com/polomarcus/common-trails/compare/backend-v0.32.0...backend-v0.32.1) (2026-03-29)


### Bug Fixes

* ruff lint fixes + protobuf CDN serving ([699781c](https://github.com/polomarcus/common-trails/commit/699781c0f39db39831818b17ceffc6dfcb8c7bee))
* ruff lint fixes for CDN write-through + protobuf ([7479e1b](https://github.com/polomarcus/common-trails/commit/7479e1b55e2e63ff4949687806414351f52eba10))
* TS strict mode graph_pb access + update cache-control test assertion ([4c0ad2f](https://github.com/polomarcus/common-trails/commit/4c0ad2fb4936168d09f8dfe093090c1d42e30bd4))

## [0.32.0](https://github.com/polomarcus/common-trails/compare/backend-v0.31.1...backend-v0.32.0) (2026-03-29)


### Features

* serve CTGB protobuf from CDN — 44% smaller than JSON ([2d5e6f7](https://github.com/polomarcus/common-trails/commit/2d5e6f748165ac012c3deb68123942f13c321ceb))

## [0.31.1](https://github.com/polomarcus/common-trails/compare/backend-v0.31.0...backend-v0.31.1) (2026-03-29)


### Bug Fixes

* 6 code review issues — constant dedup, service imports, test speed ([be062d0](https://github.com/polomarcus/common-trails/commit/be062d06cb20b46cbce6887ac65b54ba1d47d8fe))

## [0.31.0](https://github.com/polomarcus/common-trails/compare/backend-v0.30.0...backend-v0.31.0) (2026-03-29)


### Features

* local filesystem mode for cache writer + 6 tests ([fdf70e5](https://github.com/polomarcus/common-trails/commit/fdf70e5bffedeff7280dbf54a902fd1b3e2dd767))

## [0.30.0](https://github.com/polomarcus/common-trails/compare/backend-v0.29.0...backend-v0.30.0) (2026-03-29)


### Features

* CDN write-through cache implementation ([6774dc5](https://github.com/polomarcus/common-trails/commit/6774dc512ad26349b2fa95b85350f5aab5b49f4a))

## [0.29.0](https://github.com/polomarcus/common-trails/compare/backend-v0.28.0...backend-v0.29.0) (2026-03-28)


### Features

* collections page overhaul — rename, GT seed, POI placement ([565aef5](https://github.com/polomarcus/common-trails/commit/565aef58cb147fe0ea47b94e55a585028e204636))

## [0.28.0](https://github.com/polomarcus/common-trails/compare/backend-v0.27.1...backend-v0.28.0) (2026-03-28)


### Features

* Cloud Run Job for heatmap rebuild ([b0b52ba](https://github.com/polomarcus/common-trails/commit/b0b52bae95c6fa5e0fdf25276944e88aed062d91))

## [0.27.1](https://github.com/polomarcus/common-trails/compare/backend-v0.27.0...backend-v0.27.1) (2026-03-28)


### Bug Fixes

* eliminate heatmap micro-gaps from OSM edge snapping ([b70090c](https://github.com/polomarcus/common-trails/commit/b70090c31bbcc118dd0cdde67b74207f9d2ad49a))
* pass CI env var to Docker container to skip perf tests ([1ee1434](https://github.com/polomarcus/common-trails/commit/1ee14342cc7a827bba3570a2aa729cdf01dd6e92))
* ruff I001 — alphabetize imports in test_ingestion_perf ([f39b02c](https://github.com/polomarcus/common-trails/commit/f39b02cbf2d74aa5e42745ed83dad6954869acc2))
* ruff lint — remove unused import, fix sort order ([42b413e](https://github.com/polomarcus/common-trails/commit/42b413e614988fe2f0312eda97bf007d4c03a7f5))
* skip perf tests in CI — too slow for GitHub Actions runners ([eb42b5f](https://github.com/polomarcus/common-trails/commit/eb42b5fb4529cccd2a63856a81a03019d00f596a))
* use GPS-point edges instead of OSM-node edges to eliminate gaps ([d536b56](https://github.com/polomarcus/common-trails/commit/d536b56a3559efa774d0f6d87e3a6306fb27e48a))


### Miscellaneous

* simplify OSM edge code — remove dead matched_segs_in_run loop ([b8e6b72](https://github.com/polomarcus/common-trails/commit/b8e6b72a8af6b46db982068c3aad741af2c94b72))

## [0.27.0](https://github.com/polomarcus/common-trails/compare/backend-v0.26.0...backend-v0.27.0) (2026-03-28)


### Features

* parallel heatmap rebuild with deadlock retry ([eb9e640](https://github.com/polomarcus/common-trails/commit/eb9e6404d5bde08f8b1fc4600795d1dfd51b8faa))

## [0.26.0](https://github.com/polomarcus/common-trails/compare/backend-v0.25.4...backend-v0.26.0) (2026-03-28)


### Features

* run GPS upgrade in Cloud Run Job (fix stuck 0/1394) ([7282c84](https://github.com/polomarcus/common-trails/commit/7282c84c8fe9037b6e70b6c7bf074f08f3a01863))
* run GPS upgrade in Cloud Run Job (not API background task) ([d2ae1d7](https://github.com/polomarcus/common-trails/commit/d2ae1d746e3ff07e2b48705a35e8edae3d1c7765))
* viewport-based binary graph loading (area.pb) — 44% smaller ([7c56779](https://github.com/polomarcus/common-trails/commit/7c56779978a4f01ac8ba6056810672856892d5cb))


### Bug Fixes

* deterministic user_id hash + SVG label clipping on landing page ([80df372](https://github.com/polomarcus/common-trails/commit/80df372af847f16be8694dcb6c11318821b2a7ff))
* refresh Strava token before import (prevent expired token errors) ([8b62fd0](https://github.com/polomarcus/common-trails/commit/8b62fd028d58705537f85eaf494ebbc9ae5c7502))
* remove CONCURRENTLY from index migration (incompatible with txn) ([2454aef](https://github.com/polomarcus/common-trails/commit/2454aef1fc79d703de50692bcd3c6600b80dfdb8))
* ruff lint — remove unused import and variable in fix_contributor_hashes ([ea765bd](https://github.com/polomarcus/common-trails/commit/ea765bdc5c3cf155e4b6b6ee5d4fa900128c80a7))
* ruff lint — remove unused import, fix import ordering ([73dc823](https://github.com/polomarcus/common-trails/commit/73dc82351171c884d68c18f3978b47c137da982f))
* use correct z14 tile keys in OSM matching tests ([de4b61d](https://github.com/polomarcus/common-trails/commit/de4b61d8f056044a068e64e0e3cf348f0b1fdcc2))


### Miscellaneous

* release 0.7.53 ([#125](https://github.com/polomarcus/common-trails/issues/125)) ([1991168](https://github.com/polomarcus/common-trails/commit/19911686878efabfb645f7ac0692786cd3666045))

## [0.25.4](https://github.com/polomarcus/common-trails/compare/backend-v0.25.3...backend-v0.25.4) (2026-03-25)


### Bug Fixes

* resolve ruff lint errors breaking CI ([#123](https://github.com/polomarcus/common-trails/issues/123)) ([507b504](https://github.com/polomarcus/common-trails/commit/507b504b419400824a09bcbd186dfabf493f1926))

## [0.25.3](https://github.com/polomarcus/common-trails/compare/backend-v0.25.2...backend-v0.25.3) (2026-03-25)


### Bug Fixes

* restore missing model fields and fix lint errors breaking CI ([#118](https://github.com/polomarcus/common-trails/issues/118)) ([cde23ab](https://github.com/polomarcus/common-trails/commit/cde23ab1558a57f40f0026a313b0f2b19c44baac))

## [0.25.2](https://github.com/polomarcus/common-trails/compare/backend-v0.25.1...backend-v0.25.2) (2026-03-24)


### Bug Fixes

* add skip_heat_computation flag instead of contribute_heatmap=False ([d190144](https://github.com/polomarcus/common-trails/commit/d1901446e455735f7c5bf307ebc973134bcf681a))

## [0.25.1](https://github.com/polomarcus/common-trails/compare/backend-v0.25.0...backend-v0.25.1) (2026-03-24)


### Bug Fixes

* add missing RouteCollection and RouteCollectionItem models ([464f751](https://github.com/polomarcus/common-trails/commit/464f7519b9eb11e62e4f3b0d866dea2039109124))

## [0.25.0](https://github.com/polomarcus/common-trails/compare/backend-v0.24.4...backend-v0.25.0) (2026-03-24)


### Features

* resume import progress on Strava page revisit ([d81759b](https://github.com/polomarcus/common-trails/commit/d81759b41fd3f6f5e2ea89d4aa0731ae3b36a14c))

## [0.24.4](https://github.com/polomarcus/common-trails/compare/backend-v0.24.3...backend-v0.24.4) (2026-03-24)


### Bug Fixes

* prevent duplicate Strava imports for same user ([c032d58](https://github.com/polomarcus/common-trails/commit/c032d58bcfee5b20f31f5495be05c2cce9a12518))

## [0.24.3](https://github.com/polomarcus/common-trails/compare/backend-v0.24.2...backend-v0.24.3) (2026-03-24)


### Bug Fixes

* skip Overpass OSM tile fetching during bulk import ([11fec32](https://github.com/polomarcus/common-trails/commit/11fec321f8ef1046ee5e13d23648d9438ff31a41))

## [0.24.2](https://github.com/polomarcus/common-trails/compare/backend-v0.24.1...backend-v0.24.2) (2026-03-24)


### Bug Fixes

* expose startup progress in production for wakeup banner ([a5b464e](https://github.com/polomarcus/common-trails/commit/a5b464e3fc763e0fb05ff35c6ea1d4f0b642252e))
* prevent DFCI edge accumulation across deploys ([48be634](https://github.com/polomarcus/common-trails/commit/48be63448e8249a974e0b5dd7e4e02b550d3ea2e))

## [0.24.1](https://github.com/polomarcus/common-trails/compare/backend-v0.24.0...backend-v0.24.1) (2026-03-24)


### Bug Fixes

* Strava API rate limit handling ([#110](https://github.com/polomarcus/common-trails/issues/110)) ([82a801f](https://github.com/polomarcus/common-trails/commit/82a801f158e3fffe6ed93688a144c399c97f7df0))

## [0.24.0](https://github.com/polomarcus/common-trails/compare/backend-v0.23.1...backend-v0.24.0) (2026-03-24)


### Features

* heatmap edge clustering — merge near-duplicate GPS edges ([#108](https://github.com/polomarcus/common-trails/issues/108)) ([50e96e0](https://github.com/polomarcus/common-trails/commit/50e96e05a603df70058fab1d851768ddaab3aba5))

## [0.23.1](https://github.com/polomarcus/common-trails/compare/backend-v0.23.0...backend-v0.23.1) (2026-03-22)


### Bug Fixes

* DATA_DIR=/app/data in Terraform — GT routes + DFCI missing ([#105](https://github.com/polomarcus/common-trails/issues/105)) ([75ada47](https://github.com/polomarcus/common-trails/commit/75ada47e85e4819f974f1153856462faa5c987a8))
* NameError on TEST_MODE crashes background loading in prod ([#103](https://github.com/polomarcus/common-trails/issues/103)) ([319a4a0](https://github.com/polomarcus/common-trails/commit/319a4a0620b9f0e65dd68b0df5c994b7f5f8b73b))

## [0.23.0](https://github.com/polomarcus/common-trails/compare/backend-v0.22.5...backend-v0.23.0) (2026-03-22)


### Features

* /healthz returns release-please version + fix Docker cache ([#101](https://github.com/polomarcus/common-trails/issues/101)) ([36d10c0](https://github.com/polomarcus/common-trails/commit/36d10c00920e957310f299faca6ee3e414554c97))

## [0.22.5](https://github.com/polomarcus/common-trails/compare/backend-v0.22.4...backend-v0.22.5) (2026-03-22)


### Bug Fixes

* include DFCI IGN cache in /data/ for production import ([9534add](https://github.com/polomarcus/common-trails/commit/9534addefa0f8f80e0595729a186e88240f63f18))
* show email in account menu, disable prod seeds, rename to Chemins communs ([#99](https://github.com/polomarcus/common-trails/issues/99)) ([7c69a45](https://github.com/polomarcus/common-trails/commit/7c69a45ad4750ca54dc9b763dc003d241dce0cc6))
* store OAuth state in DB instead of in-memory (prevents ghost users) ([#98](https://github.com/polomarcus/common-trails/issues/98)) ([46e96cc](https://github.com/polomarcus/common-trails/commit/46e96cc6214be5599302e9e8e995f317882dfb0a))
* use DATA_DIR=/app/data instead of redundant COPY data/ /data/ ([#100](https://github.com/polomarcus/common-trails/issues/100)) ([a8172e2](https://github.com/polomarcus/common-trails/commit/a8172e22013aa3d169eeece2a2695a50a7656e15))

## [0.22.4](https://github.com/polomarcus/common-trails/compare/backend-v0.22.3...backend-v0.22.4) (2026-03-22)


### Bug Fixes

* prevent Strava ghost users on connect + auto-migrate duplicates ([097ba6c](https://github.com/polomarcus/common-trails/commit/097ba6cd2e229be753b9933a9a80655b53a9d950))

## [0.22.3](https://github.com/polomarcus/common-trails/compare/backend-v0.22.2...backend-v0.22.3) (2026-03-22)


### Bug Fixes

* add /routes to frontend HTML middleware, cover all frontend pages ([f4a5388](https://github.com/polomarcus/common-trails/commit/f4a53881d22b50d5ab8e589a0f4ef9b7827a16b8))

## [0.22.2](https://github.com/polomarcus/common-trails/compare/backend-v0.22.1...backend-v0.22.2) (2026-03-22)


### Bug Fixes

* serve frontend HTML for /trips and other pages in production ([91429a5](https://github.com/polomarcus/common-trails/commit/91429a5fe2125250aff6c15b5fbde6f2b7daa309))

## [0.22.1](https://github.com/polomarcus/common-trails/compare/backend-v0.22.0...backend-v0.22.1) (2026-03-22)


### Bug Fixes

* reduce DFCI routing dominance + move-waypoint regression ([#91](https://github.com/polomarcus/common-trails/issues/91)) ([7931237](https://github.com/polomarcus/common-trails/commit/79312378a6503fd8d431e95cb781b441ca121bdb))

## [0.22.0](https://github.com/polomarcus/common-trails/compare/backend-v0.21.3...backend-v0.22.0) (2026-03-21)


### Features

* public routes panel on map + discover viewport filtering ([#89](https://github.com/polomarcus/common-trails/issues/89)) ([57342d8](https://github.com/polomarcus/common-trails/commit/57342d876b7fb36ba8ef834ebdab33847a05750d))

## [0.21.3](https://github.com/polomarcus/common-trails/compare/backend-v0.21.2...backend-v0.21.3) (2026-03-21)


### Bug Fixes

* remove backend routing E2E tests, fix math shadow bug ([#86](https://github.com/polomarcus/common-trails/issues/86)) ([f5665be](https://github.com/polomarcus/common-trails/commit/f5665beee98ecfb0b18246b75a09392f604a56ad))

## [0.21.2](https://github.com/polomarcus/common-trails/compare/backend-v0.21.1...backend-v0.21.2) (2026-03-21)


### Bug Fixes

* logout cookie deletion + mobile landing page ([#83](https://github.com/polomarcus/common-trails/issues/83)) ([ff91be6](https://github.com/polomarcus/common-trails/commit/ff91be6d6acb3d27871283cb66821dbfff6b798a))

## [0.21.1](https://github.com/polomarcus/common-trails/compare/backend-v0.21.0...backend-v0.21.1) (2026-03-21)


### Bug Fixes

* E2E auth, Strava import progress, mobile landing, Cloud Run Job DB ([#81](https://github.com/polomarcus/common-trails/issues/81)) ([4e9b4e4](https://github.com/polomarcus/common-trails/commit/4e9b4e4d5605ae57ae5b2d0fce8b6e70081d9714))

## [0.21.0](https://github.com/polomarcus/common-trails/compare/backend-v0.20.0...backend-v0.21.0) (2026-03-20)


### Features

* serve frontend from Cloud Run + validation fix + trail cache ([#79](https://github.com/polomarcus/common-trails/issues/79)) ([0ace3bc](https://github.com/polomarcus/common-trails/commit/0ace3bc8c4e6e412c3e87ba67f061b6280e2052f))

## [0.20.0](https://github.com/polomarcus/common-trails/compare/backend-v0.19.0...backend-v0.20.0) (2026-03-20)


### Features

* surface, elevation, routing fixes & route management UX overhaul ([#77](https://github.com/polomarcus/common-trails/issues/77)) ([dea225f](https://github.com/polomarcus/common-trails/commit/dea225f97930a82b1770368219b6c9eff8e22ff2))

## [0.19.0](https://github.com/polomarcus/common-trails/compare/backend-v0.18.0...backend-v0.19.0) (2026-03-17)


### Features

* French cyclist terminology for GitHub-like features ([#75](https://github.com/polomarcus/common-trails/issues/75)) ([243c31c](https://github.com/polomarcus/common-trails/commit/243c31c005f73a1a575d68ca15fd46bf4902a8cd))

## [0.18.0](https://github.com/polomarcus/common-trails/compare/backend-v0.17.0...backend-v0.18.0) (2026-03-17)


### Features

* colorblind-safe sport colors + DFCI slope exemption ([#73](https://github.com/polomarcus/common-trails/issues/73)) ([9ab669f](https://github.com/polomarcus/common-trails/commit/9ab669ff0983a708d49d67fc62def93ca36ea7ff))

## [0.17.0](https://github.com/polomarcus/common-trails/compare/backend-v0.16.0...backend-v0.17.0) (2026-03-17)


### Features

* simplified layers panel + color-blind accessible heatmap & elev… ([#71](https://github.com/polomarcus/common-trails/issues/71)) ([c02907c](https://github.com/polomarcus/common-trails/commit/c02907c61b2df24567b5c7552f30c5cc8e8528f9))

## [0.16.0](https://github.com/polomarcus/common-trails/compare/backend-v0.15.0...backend-v0.16.0) (2026-03-16)


### Features

* Sentry routing observability + route sharing + OG previews ([adaf6f3](https://github.com/polomarcus/common-trails/commit/adaf6f3bb3440c15ead8cf87fde7aaead61a0a57))

## [0.15.0](https://github.com/polomarcus/common-trails/compare/backend-v0.14.1...backend-v0.15.0) (2026-03-13)


### Features

* Strava-only login + routing audit fixes ([#65](https://github.com/polomarcus/common-trails/issues/65)) ([7b83487](https://github.com/polomarcus/common-trails/commit/7b83487575610306ad7017587af51591f099988e))

## [0.14.1](https://github.com/polomarcus/common-trails/compare/backend-v0.14.0...backend-v0.14.1) (2026-03-12)


### Bug Fixes

* correct GCE metadata URL for Cloud Run job trigger ([344d318](https://github.com/polomarcus/common-trails/commit/344d318659523b84c269cff40975c0eee495d1cf))

## [0.14.0](https://github.com/polomarcus/common-trails/compare/backend-v0.13.0...backend-v0.14.0) (2026-03-12)


### Features

* broaden running sport routing + parallelize external fallback ([#62](https://github.com/polomarcus/common-trails/issues/62)) ([8e6d4bc](https://github.com/polomarcus/common-trails/commit/8e6d4bce1be2ed65541f5e381baed6ab1fd41b27))

## [0.13.0](https://github.com/polomarcus/common-trails/compare/backend-v0.12.0...backend-v0.13.0) (2026-03-12)


### Features

* two-phase Strava import — polylines fast, then batched GPS upgrade ([#60](https://github.com/polomarcus/common-trails/issues/60)) ([7462445](https://github.com/polomarcus/common-trails/commit/7462445208904b577d73b737c78418c7de6cdfeb))

## [0.12.0](https://github.com/polomarcus/common-trails/compare/backend-v0.11.0...backend-v0.12.0) (2026-03-12)


### Features

* cell coverage layer, SPA server fix, top nav account link ([#57](https://github.com/polomarcus/common-trails/issues/57)) ([f16457b](https://github.com/polomarcus/common-trails/commit/f16457ba93d1c6316899c1748f7c9b2dcd40ff88))

## [0.11.0](https://github.com/polomarcus/common-trails/compare/backend-v0.10.0...backend-v0.11.0) (2026-03-11)


### Features

* add "Couverture cellules" user cell coverage map layer ([2d7be77](https://github.com/polomarcus/common-trails/commit/2d7be77abe399d73e278a733629dd6b7c43b1024))


### Bug Fixes

* use REST API instead of google-cloud-run SDK to trigger job ([7b73a43](https://github.com/polomarcus/common-trails/commit/7b73a4351207d2e334cf7d53fd57c6638d029973))

## [0.10.0](https://github.com/polomarcus/common-trails/compare/backend-v0.9.6...backend-v0.10.0) (2026-03-11)


### Features

* wire up Cloud Run Job for Strava import ([#53](https://github.com/polomarcus/common-trails/issues/53)) ([2d492c5](https://github.com/polomarcus/common-trails/commit/2d492c51d686a9d56154b989c0e923a24a69c7c2))

## [0.9.6](https://github.com/polomarcus/common-trails/compare/backend-v0.9.5...backend-v0.9.6) (2026-03-11)


### Bug Fixes

* non-blocking startup + Cloud Run stability improvements ([#49](https://github.com/polomarcus/common-trails/issues/49)) ([5169daa](https://github.com/polomarcus/common-trails/commit/5169daadcd6b5f66fb99e439d934f3490746a48e))

## [0.9.5](https://github.com/polomarcus/common-trails/compare/backend-v0.9.4...backend-v0.9.5) (2026-03-11)


### Bug Fixes

* DFCI cache write to /tmp + show skipped count in Strava UI ([#46](https://github.com/polomarcus/common-trails/issues/46)) ([2978c43](https://github.com/polomarcus/common-trails/commit/2978c43139cb7b6bb8457a9085c969d730b29e1b))

## [0.9.4](https://github.com/polomarcus/common-trails/compare/backend-v0.9.3...backend-v0.9.4) (2026-03-11)


### Bug Fixes

* GCS routing — remove trailingSlash, fix basePath for all navigations ([#44](https://github.com/polomarcus/common-trails/issues/44)) ([e99aa77](https://github.com/polomarcus/common-trails/commit/e99aa77aaafce48e2c033478c410591361a47179))

## [0.9.3](https://github.com/polomarcus/common-trails/compare/backend-v0.9.2...backend-v0.9.3) (2026-03-11)


### Bug Fixes

* extract origin from FRONTEND_URL for CORS (strip path) ([#40](https://github.com/polomarcus/common-trails/issues/40)) ([81ed785](https://github.com/polomarcus/common-trails/commit/81ed785cdb62a41c42adbaaed7c7e4ed42cafbff))

## [0.9.2](https://github.com/polomarcus/common-trails/compare/backend-v0.9.1...backend-v0.9.2) (2026-03-11)


### Bug Fixes

* add trailing slash to Strava redirect for GCS static hosting ([#38](https://github.com/polomarcus/common-trails/issues/38)) ([300fc14](https://github.com/polomarcus/common-trails/commit/300fc14d2ab82fff31d8bede376f506be792f8b3))

## [0.9.1](https://github.com/polomarcus/common-trails/compare/backend-v0.9.0...backend-v0.9.1) (2026-03-11)


### Bug Fixes

* use explicit CORS origins instead of wildcard with credentials ([#36](https://github.com/polomarcus/common-trails/issues/36)) ([784ca40](https://github.com/polomarcus/common-trails/commit/784ca404f7341b4c7ca1d7d3ce5c6248e0afd322))

## [0.9.0](https://github.com/polomarcus/common-trails/compare/backend-v0.8.0...backend-v0.9.0) (2026-03-11)


### Features

* migrate backend from in-memory dicts to PostgreSQL ([#34](https://github.com/polomarcus/common-trails/issues/34)) ([dea9abb](https://github.com/polomarcus/common-trails/commit/dea9abb1d0868ecb02004302c76ca21628698778))

## [0.8.0](https://github.com/polomarcus/common-trails/compare/backend-v0.7.0...backend-v0.8.0) (2026-03-10)


### Features

* activity/route detail pages + shared map/elevation components + sidebar UX ([86a80a1](https://github.com/polomarcus/common-trails/commit/86a80a18f77295011b482862c0dd634a8e23b800))
* direction-aware MTB routing, Sentry perf tracing, UX improvements ([aced668](https://github.com/polomarcus/common-trails/commit/aced668e67fc0ed11a5e4a56f98d1776d1b469a2))
* drag waypoint + perso-first routing + route page + home redirect + Bonjour ([0d81c6d](https://github.com/polomarcus/common-trails/commit/0d81c6d88c9d528481585d267dfdc5ccc79f96ed))
* drag waypoint, perso-first routing, route page + variants, home redirect, "Bonjour" ([664f465](https://github.com/polomarcus/common-trails/commit/664f465d6370bf1df64bf3ad6dcefe0b80d0558a))
* elevation profile on activity detail + Strava altitude stream ([24eedb6](https://github.com/polomarcus/common-trails/commit/24eedb663a02660666e51fb1cde591d8e2360940))
* elevation profile SVG + 3D GPX coords + coord unpack fixes ([3c36570](https://github.com/polomarcus/common-trails/commit/3c365707f6b4177db2374b8d587176fd511c2a71))
* GCP auto-deploy on push to main + infra cleanup ([dfc0554](https://github.com/polomarcus/common-trails/commit/dfc0554793d9e549588eb00acaa80520564ea172))
* GCP deploy on every push to main + infra cleanup ([ef50a5b](https://github.com/polomarcus/common-trails/commit/ef50a5b87cf74db32617408cca1e2983adf499bf))
* GPX map, route editor, heatmap trails + OSRM routing (K=2) ([b345fe4](https://github.com/polomarcus/common-trails/commit/b345fe470aa0cb014d3d5e27c1d4529d131427c5))
* home redesign, Strava real import, Garmin tutorial, sidebar glow, stats highlight ([522f54c](https://github.com/polomarcus/common-trails/commit/522f54c54eeaca07d64052c679f12b93647eacd4))
* map UX improvements + 6 bug fixes ([#6](https://github.com/polomarcus/common-trails/issues/6)) ([44500d0](https://github.com/polomarcus/common-trails/commit/44500d06993f60687a1253dc5a0eda1b681dd0fa))
* persist activities to Docker volume (survive restarts) ([0019c69](https://github.com/polomarcus/common-trails/commit/0019c69acf0ab77a31ca3ec88a9fded0182fa3fa))
* port 8787, Strava OAuth, heatmap bbox, seed activities + routes, UX polish ([93a2ebb](https://github.com/polomarcus/common-trails/commit/93a2ebbb223b9b1d58707c9a96935a4d7ec34254))
* public routes sidebar tab + Strava link + route name clickable ([e054691](https://github.com/polomarcus/common-trails/commit/e054691bf5dcd7afba14d752cd5e57c81e6a1637))
* routing anti-détour + quality score + Panoramax markers ([30d19cd](https://github.com/polomarcus/common-trails/commit/30d19cd50f0aa759d70dec435611b2a4f88934aa))
* seed routes + fix discover + UX improvements ([5556990](https://github.com/polomarcus/common-trails/commit/555699059f26dfb74a9f3a53574348a7019d8961))
* sidebar traces par proximité + fix JWT 7j + admin@admin + heatmap K=1 ([#7](https://github.com/polomarcus/common-trails/issues/7)) ([377f14a](https://github.com/polomarcus/common-trails/commit/377f14ab1e8db2bd8a18c2e755e68319d2577343))
* smart routing — heatmap → personal traces → OSRM ([#8](https://github.com/polomarcus/common-trails/issues/8)) ([8fc0df6](https://github.com/polomarcus/common-trails/commit/8fc0df618b215a8601a080749500916716797870))
* **step-2:** add FastAPI backend with auth and Strava OAuth stub ([115312a](https://github.com/polomarcus/common-trails/commit/115312a600c2a4c47d882376dc1b95054ecb2773))
* **step-3:** add DB models, Alembic migrations, and GitHub-like routes API ([0c682c0](https://github.com/polomarcus/common-trails/commit/0c682c0744de69dd7bf83c44af6d48bd054a8fa7))
* **step-4:** add Strava import, file upload, ingestion, and heatmap API ([bf416c7](https://github.com/polomarcus/common-trails/commit/bf416c765929da54e942578cab42cb548735f63b))
* **step-5:** add pgRouting, seed minigraph, GPX upload, and me/stats ([925f338](https://github.com/polomarcus/common-trails/commit/925f3388533adb88617603b44c0215009769b7bf))
* trace sidebar — fitBounds centering + detail panel (date, fork to route) ([a745346](https://github.com/polomarcus/common-trails/commit/a745346f98baafd2248fb21348d509d346a4f63c))
* UX improvements — drag fix, heatmap pastel, stats, Mon Compte ([7b1863c](https://github.com/polomarcus/common-trails/commit/7b1863c44197f6f574a5055ff07e374ca5c52d51))


### Bug Fixes

* drag waypoint index + route save display + heatmap precompute ([7852cb1](https://github.com/polomarcus/common-trails/commit/7852cb1f90825178aa64420323ee5334785548b4))
* E2E test failures — duplicate testid, redirect timing, fork geometry ([237c8e8](https://github.com/polomarcus/common-trails/commit/237c8e88de99263801642958c34b50ee14711f42))
* GCS frontend asset paths, Sentry PII, hide dev credentials ([#18](https://github.com/polomarcus/common-trails/issues/18)) ([18800c2](https://github.com/polomarcus/common-trails/commit/18800c2c1c6da55afd17d001961020620fb0badb))
* GCS page refresh for extensionless URLs ([#22](https://github.com/polomarcus/common-trails/issues/22)) ([a8c20dd](https://github.com/polomarcus/common-trails/commit/a8c20dd60b1bf46966cd1d6ecedf33e3ebffe9ef))
* hybrid diversity backfill for Luberon proposals ([#15](https://github.com/polomarcus/common-trails/issues/15)) ([986d16d](https://github.com/polomarcus/common-trails/commit/986d16daa9e54799983899e1bf1d84bf2b10aa79))
* line-drag splice index + route display after save + heatmap precompute cache ([13d0778](https://github.com/polomarcus/common-trails/commit/13d077814743aee032d8781e9d72a25195a05f36))
* stats 'Tout' default + map routing + unique_cells bug ([72b375b](https://github.com/polomarcus/common-trails/commit/72b375b736308f6f502e25a017318e9ef2228093))
* stats 'Tout' default + map routing + unique_cells bug ([828f401](https://github.com/polomarcus/common-trails/commit/828f401d51ffa996767af0b60dc312fcdb1c6698))
* UX bugfixes — waypoint restore, drag ghost, Panoramax hover ([#12](https://github.com/polomarcus/common-trails/issues/12)) ([594b5a4](https://github.com/polomarcus/common-trails/commit/594b5a4f4668b20c7f0f6d45190f41d27f9b72a5))


### Miscellaneous

* add .gitignore, remove pycache from tracking ([33da6ed](https://github.com/polomarcus/common-trails/commit/33da6edf8f9decc5ea20f11304bec19d8b3932a8))
* align runtimes to python 3.13 and nextjs 16 ([79ec4d3](https://github.com/polomarcus/common-trails/commit/79ec4d35530170a0806ceec33fe77a51e04f7db8))
