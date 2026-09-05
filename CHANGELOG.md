# Changelog

## [0.7.168](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.167...common-trails-v0.7.168) (2026-09-05)


### Features

* **calque:** per-sport export only — drop the all-sports "Tous" raster ([#14](https://github.com/polomarcus/common-trails/issues/14)) ([df82cad](https://github.com/polomarcus/common-trails/commit/df82cadee6e73b0bfa2fcfe0419d7530f3a35607))
* **calque:** per-sport raster overlays for gpx.studio + discoverability ([#11](https://github.com/polomarcus/common-trails/issues/11)) ([b15573b](https://github.com/polomarcus/common-trails/commit/b15573b07c17c28ffa3d4c00770a237eee02a117))
* **export:** surface heatmap export on home + main menu, fix calque URL ([#12](https://github.com/polomarcus/common-trails/issues/12)) ([9c29797](https://github.com/polomarcus/common-trails/commit/9c29797c52346512a0b3ff747316b996e14db469))


### Bug Fixes

* **calque:** cap per-sport rasters at z12 so all 6 pyramids fit the job ([#13](https://github.com/polomarcus/common-trails/issues/13)) ([30e0442](https://github.com/polomarcus/common-trails/commit/30e04420067674c1db3c969acf69f3da1dab222f))
* **deploy:** assert build-pmtiles job at 8Gi — CD was reverting the OOM bump ([#19](https://github.com/polomarcus/common-trails/issues/19)) ([e4e4843](https://github.com/polomarcus/common-trails/commit/e4e48431c8f3b34b7e8ec633425ecef3190bc727))
* **ingest:** Garmin nested-zip archives + larger GPX cap + surfaced upload errors ([#4](https://github.com/polomarcus/common-trails/issues/4)) ([9b1fcf0](https://github.com/polomarcus/common-trails/commit/9b1fcf00aab0fb465eb89e7e07852d50cc9a4602))
* **map:** apply the sport chip filter on /map (it did nothing before) ([#10](https://github.com/polomarcus/common-trails/issues/10)) ([555c70f](https://github.com/polomarcus/common-trails/commit/555c70f5f632d216c88f16bdc5479ed8326f8266))
* **map:** React [#418](https://github.com/polomarcus/common-trails/issues/418) hydration crash for logged-in visitors (+ honest k_anonymity pointer) ([#6](https://github.com/polomarcus/common-trails/issues/6)) ([2eec3cc](https://github.com/polomarcus/common-trails/commit/2eec3cc87c3fbc43f278fbfd0e7ce9c62299414d))
* **pmtiles:** isolate the raw export in a child process — the 4Gi OOM fix ([#16](https://github.com/polomarcus/common-trails/issues/16)) ([7b35ee2](https://github.com/polomarcus/common-trails/commit/7b35ee269f55f77ce77d095c4642554cf9b1c67b))


### Miscellaneous

* remove orphan tile-cache SW + add archive-import cost-optimization analysis ([#5](https://github.com/polomarcus/common-trails/issues/5)) ([c7d3be9](https://github.com/polomarcus/common-trails/commit/c7d3be9b94c97f6fa9058c40d8ec0a3d7f0d5001))

## [0.7.167](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.166...common-trails-v0.7.167) (2026-08-10)


### Features

* **heatmap:** serve community heatmap from first-party tiles.chemins-communs.fr ([#629](https://github.com/polomarcus/common-trails/issues/629)) ([09287d9](https://github.com/polomarcus/common-trails/commit/09287d9663d761722cc13438ed0a0437b92abd40))

## [0.7.166](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.165...common-trails-v0.7.166) (2026-08-10)


### Features

* **heatmap:** gate direction arrows on strong directional dominance ([#627](https://github.com/polomarcus/common-trails/issues/627)) ([7e6b86f](https://github.com/polomarcus/common-trails/commit/7e6b86f7b674021b87d52d5cdfc1de472ce1a49e))


### Bug Fixes

* **frontend:** drop false "anonymised" copy + orphan-page redirect stubs ([#625](https://github.com/polomarcus/common-trails/issues/625)) ([ded53c2](https://github.com/polomarcus/common-trails/commit/ded53c28bc8aebac7d9dc037790b57bec813d6a8))
* **heatmap:** bound the pass_count-regrade split so GPS noise can't OOM the PMTiles build ([#626](https://github.com/polomarcus/common-trails/issues/626)) ([4f3d632](https://github.com/polomarcus/common-trails/commit/4f3d632766ae508f691932e00fcbb6bae45981eb))


### Miscellaneous

* release 0.7.165 ([#624](https://github.com/polomarcus/common-trails/issues/624)) ([5f400e7](https://github.com/polomarcus/common-trails/commit/5f400e76c209518c4d661fef5cd606aab06936ce))

## [0.7.165](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.164...common-trails-v0.7.165) (2026-08-09)


### Bug Fixes

* **heatmap:** per-feature LOCAL pass_count/heat_score on raw traces ([#621](https://github.com/polomarcus/common-trails/issues/621)) ([72d6304](https://github.com/polomarcus/common-trails/commit/72d6304ddc384511593257e3d1da881d276a1e43))
* **security:** abuse/cost hardening for public beta (rate-limit key, upload throttles, archive quotas, heatmap/geocode cost guards) ([#622](https://github.com/polomarcus/common-trails/issues/622)) ([f4b971b](https://github.com/polomarcus/common-trails/commit/f4b971b027651c8536c5344aaaf67ec44a1ea6cb))

## [0.7.164](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.163...common-trails-v0.7.164) (2026-08-09)


### Features

* **heatmap:** arrows point the DOMINANT direction + encode dominance strength ([#618](https://github.com/polomarcus/common-trails/issues/618)) ([7098a9a](https://github.com/polomarcus/common-trails/commit/7098a9a4bc77f3204471f7d0e9715043d33febd5))


### Bug Fixes

* **strava:** signup gate at top for logged-out + drop redundant Garmin one-liner ([#617](https://github.com/polomarcus/common-trails/issues/617)) ([7aab608](https://github.com/polomarcus/common-trails/commit/7aab608175c6738afbcef7ae2f3608767cd485c9))

## [0.7.163](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.162...common-trails-v0.7.163) (2026-08-09)


### Bug Fixes

* **backend:** stream /me/activities, gzip+cap /heatmap/export, fresher cache, job-config guard ([#614](https://github.com/polomarcus/common-trails/issues/614)) ([e83c816](https://github.com/polomarcus/common-trails/commit/e83c816f79900d800398de8cd6ff4a0d99593c26))
* **frontend:** resolve heatmap via freshness pointer + fail-loud on unset env URLs ([#615](https://github.com/polomarcus/common-trails/issues/615)) ([2517f30](https://github.com/polomarcus/common-trails/commit/2517f30015b534e8755490cf4cc63fe7c35c80e2))

## [0.7.162](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.161...common-trails-v0.7.162) (2026-08-09)


### Bug Fixes

* **cache:** revalidate the HTML entry (fixes stale-bundle "no heatmap") + /strava polish ([#612](https://github.com/polomarcus/common-trails/issues/612)) ([16eecdd](https://github.com/polomarcus/common-trails/commit/16eecdd3499d7c776c7ce67238ba29769f4641de))

## [0.7.161](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.160...common-trails-v0.7.161) (2026-08-08)


### Features

* **onboarding:** clarify home signup+import funnel + Strava/Garmin parity on /strava ([#610](https://github.com/polomarcus/common-trails/issues/610)) ([36ba640](https://github.com/polomarcus/common-trails/commit/36ba6407d36a27fdf8fcfa29d33073ef15578467))

## [0.7.160](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.159...common-trails-v0.7.160) (2026-08-08)


### Features

* **stats:** rewrite /stats as a clean post-pivot personal dashboard + restore nav ([#608](https://github.com/polomarcus/common-trails/issues/608)) ([a64f047](https://github.com/polomarcus/common-trails/commit/a64f047b362d32819bf6b96e9de632b543c4f827))

## [0.7.159](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.158...common-trails-v0.7.159) (2026-08-08)


### Features

* **email:** 6-monthly "re-sync your data" reminder for stale contributors ([#605](https://github.com/polomarcus/common-trails/issues/605)) ([fd4ae22](https://github.com/polomarcus/common-trails/commit/fd4ae22b3504ccef0d55ae8d76b7f17c608435fb))
* **strava+nav:** show contribution status on /strava; orphan the broken /stats ([#606](https://github.com/polomarcus/common-trails/issues/606)) ([f00b7d5](https://github.com/polomarcus/common-trails/commit/f00b7d5d840051c3eab4a2491a35f196846f6547))

## [0.7.158](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.157...common-trails-v0.7.158) (2026-08-07)


### Bug Fixes

* **index:** post-pivot home copy + 3-step narrative + km-stat & DFCI payload fixes ([#603](https://github.com/polomarcus/common-trails/issues/603)) ([7a39f30](https://github.com/polomarcus/common-trails/commit/7a39f307fef163725ea6c03a24c359ddd0f281bf))

## [0.7.157](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.156...common-trails-v0.7.157) (2026-08-07)


### Bug Fixes

* **home:** remove decommissioned-routing card + show lines (not dots) at default zoom ([#601](https://github.com/polomarcus/common-trails/issues/601)) ([417d114](https://github.com/polomarcus/common-trails/commit/417d1142f08ffb95ee2728ddc2412b5142f6c683))

## [0.7.156](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.155...common-trails-v0.7.156) (2026-08-07)


### Bug Fixes

* **ingest:** raise MAX_ZIP_MEMBERS 5000→20000 — real Garmin "Export All" is 6000+ .fit ([#599](https://github.com/polomarcus/common-trails/issues/599)) ([b220c9c](https://github.com/polomarcus/common-trails/commit/b220c9ca106389733719e5334ee7fcf402f916a0))

## [0.7.155](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.154...common-trails-v0.7.155) (2026-08-07)


### Features

* **home:** surface the gpx.studio/VisuGPX calque on the public index ([#597](https://github.com/polomarcus/common-trails/issues/597)) ([4bf3de2](https://github.com/polomarcus/common-trails/commit/4bf3de25440abe14fda6de1cce7fa4b5e70de59c))

## [0.7.154](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.153...common-trails-v0.7.154) (2026-08-07)


### Bug Fixes

* **gdpr:** prune old immutable pmtiles snapshots — keep only the latest ([#594](https://github.com/polomarcus/common-trails/issues/594)) ([40be99d](https://github.com/polomarcus/common-trails/commit/40be99d4ce005c3d499a259c377a529fd8be792b))

## [0.7.153](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.152...common-trails-v0.7.153) (2026-08-07)


### Features

* **contribute:** Garmin-export help link in the dropzone ([#592](https://github.com/polomarcus/common-trails/issues/592)) ([8eda40d](https://github.com/polomarcus/common-trails/commit/8eda40de52985cafd2ffe604c5d280a551e1dc00))

## [0.7.152](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.151...common-trails-v0.7.152) (2026-08-07)


### Features

* **ingest:** recurse into nested zips — Garmin "Export All" archives now import ([#590](https://github.com/polomarcus/common-trails/issues/590)) ([3e9764f](https://github.com/polomarcus/common-trails/commit/3e9764fd6df59e733b565550a41c7c1711e8f546))

## [0.7.151](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.150...common-trails-v0.7.151) (2026-08-07)


### Bug Fixes

* **gdpr:** purge stale raster calque tiles each build (deleted/below-K traces no longer linger) ([#587](https://github.com/polomarcus/common-trails/issues/587)) ([832c0bc](https://github.com/polomarcus/common-trails/commit/832c0bc8c17560a2e9d1b03e354fdc7c6d572c76))

## [0.7.150](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.149...common-trails-v0.7.150) (2026-08-07)


### Bug Fixes

* **infra:** wire SENTRY_DSN, un-landmine the heredoc, gate deploy-gcp to manual ([#585](https://github.com/polomarcus/common-trails/issues/585)) ([2e8cfce](https://github.com/polomarcus/common-trails/commit/2e8cfce66fa9f10a8841d6c08a5d126ce428c828))

## [0.7.149](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.148...common-trails-v0.7.149) (2026-08-07)


### Bug Fixes

* **map:** the in-map GPX-preview import now shows feedback (was a silent no-op) ([#583](https://github.com/polomarcus/common-trails/issues/583)) ([46f796e](https://github.com/polomarcus/common-trails/commit/46f796ef7e13c8184a1772270926329c37d61cf5))

## [0.7.148](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.147...common-trails-v0.7.148) (2026-08-07)


### Features

* **contribute:** drop the consent GATE — implicit consent, no friction ([#581](https://github.com/polomarcus/common-trails/issues/581)) ([92ddc02](https://github.com/polomarcus/common-trails/commit/92ddc029451289ea4d11d6d25b296176b5a4a527))

## [0.7.147](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.146...common-trails-v0.7.147) (2026-08-07)


### Features

* **contribute:** pre-check the ODbL consent by default (remove the upload friction) ([#579](https://github.com/polomarcus/common-trails/issues/579)) ([d2b4863](https://github.com/polomarcus/common-trails/commit/d2b4863d16e0e468bf2c19053c1ecdad280ccfb0))

## [0.7.146](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.145...common-trails-v0.7.146) (2026-08-06)


### Features

* **email:** the "your traces are on the map" email promotes the gpx.studio/VisuGPX calque ([#574](https://github.com/polomarcus/common-trails/issues/574)) ([1e29d12](https://github.com/polomarcus/common-trails/commit/1e29d1272169114061935fa71625eaaa0507ca6e))
* **export:** "use as a calque in gpx.studio / VisuGPX" section in the export modal ([#576](https://github.com/polomarcus/common-trails/issues/576)) ([40d6689](https://github.com/polomarcus/common-trails/commit/40d6689766a3fa99445e4c8986c3afde70d6bbfb))
* **heatmap:** raster XYZ tile pyramid → the heatmap as an importable calque (gpx.studio/VisuGPX) ([#573](https://github.com/polomarcus/common-trails/issues/573)) ([feb16e7](https://github.com/polomarcus/common-trails/commit/feb16e736151563435cf56a1ed06d42c19a2c11d))


### Bug Fixes

* **deploy:** drain job was missing RESEND_API_KEY → "traces on the map" email never sent ([#575](https://github.com/polomarcus/common-trails/issues/575)) ([0863577](https://github.com/polomarcus/common-trails/commit/0863577c8fa4d64e8f5e2f19b82fbba81f36c0c6))
* MVP audit batch 1 — 4 HIGH findings (FIT pollution, raster ordering, Cloud Tasks project, banner) ([#577](https://github.com/polomarcus/common-trails/issues/577)) ([224080c](https://github.com/polomarcus/common-trails/commit/224080c5ddafa3d0b42bcd5d34e655253148f26b))

## [0.7.145](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.144...common-trails-v0.7.145) (2026-08-06)


### Bug Fixes

* **compliance:** /gpx/upload also requires consent to publish to the community layer ([#571](https://github.com/polomarcus/common-trails/issues/571)) ([23ee39e](https://github.com/polomarcus/common-trails/commit/23ee39e5d1804479dad62739ac338008691ed5b7))
* **compliance:** no community/ODbL write on /imports/files without a consent row ([#570](https://github.com/polomarcus/common-trails/issues/570)) ([836a4bf](https://github.com/polomarcus/common-trails/commit/836a4bfd7248c658a75d81789cdf26315f649938))
* **e2e:** un-stale shard-2 specs after raster-heatmap + nav redesign; fix real hero heatmap regression ([#566](https://github.com/polomarcus/common-trails/issues/566)) ([25d838c](https://github.com/polomarcus/common-trails/commit/25d838c61e609097ca84a26da562b0afe4d6abd9))
* **gdpr:** activity deletion + account merge now refresh the community map ([#568](https://github.com/polomarcus/common-trails/issues/568)) ([60ad6d3](https://github.com/polomarcus/common-trails/commit/60ad6d36d8a9936976d87bfde170ea103cfc088a))
* **imports:** loose GPX/FIT upload now triggers the PMTiles rebuild so a contribution appears on the map ([#567](https://github.com/polomarcus/common-trails/issues/567)) ([8a8e68c](https://github.com/polomarcus/common-trails/commit/8a8e68c68fc889ecd548ce5e000bd247a75e4dfd))
* **map:** raster heatmap-opacity set at runtime was an invalid maplibre expr (route-dim never applied) ([#569](https://github.com/polomarcus/common-trails/issues/569)) ([8e09c2c](https://github.com/polomarcus/common-trails/commit/8e09c2c9c7aad9f8d5d9bb59b32ab8410585676a))

## [0.7.144](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.143...common-trails-v0.7.144) (2026-08-06)


### Bug Fixes

* **heatmap:** de-blob raster at street zoom — decay intensity + cap radius + fade opacity ([#563](https://github.com/polomarcus/common-trails/issues/563)) ([e6a03c3](https://github.com/polomarcus/common-trails/commit/e6a03c3d833cce5d01be22dbd53cd7f7a5db481d))

## [0.7.143](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.142...common-trails-v0.7.143) (2026-08-06)


### Bug Fixes

* **e2e:** un-red shard 1 — export specs pinned a header the streaming refactor dropped ([#562](https://github.com/polomarcus/common-trails/issues/562)) ([5fc9166](https://github.com/polomarcus/common-trails/commit/5fc91661444e0e8734cca9ccd508f8fb55bc0b28))

## [0.7.142](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.141...common-trails-v0.7.142) (2026-08-06)


### Features

* **heatmap:** raster-style density heatmap (Strava look) — fixes the diffuse vector-line pâté ([#560](https://github.com/polomarcus/common-trails/issues/560)) ([3561823](https://github.com/polomarcus/common-trails/commit/3561823db9f885969df36d9f302ed9b026c9fbcc))

## [0.7.141](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.140...common-trails-v0.7.141) (2026-08-06)


### Features

* **heatmap:** per-direction pass counts for the mtb/gravel tooltip ([#557](https://github.com/polomarcus/common-trails/issues/557)) ([31e2373](https://github.com/polomarcus/common-trails/commit/31e2373b6f408c4d0f0689247a7da2597195870a))

## [0.7.140](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.139...common-trails-v0.7.140) (2026-08-05)


### Bug Fixes

* **heatmap:** tame the orange "pâté" — thinner lines + capped opacity ([#555](https://github.com/polomarcus/common-trails/issues/555)) ([da3159c](https://github.com/polomarcus/common-trails/commit/da3159cbfedf48b0e3c600395b585e64d64538db))

## [0.7.139](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.138...common-trails-v0.7.139) (2026-08-05)


### Bug Fixes

* **export:** stream GPX/GeoJSON/KML serialization end-to-end (kill the OOM 503) ([#553](https://github.com/polomarcus/common-trails/issues/553)) ([4026466](https://github.com/polomarcus/common-trails/commit/40264661d008b888595b698d111bcb1af14d0d43))

## [0.7.138](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.137...common-trails-v0.7.138) (2026-08-05)


### Bug Fixes

* **export:** serve raw heatmap exports from the pre-built geojsonl (kill the 503) ([#550](https://github.com/polomarcus/common-trails/issues/550)) ([7e05c7b](https://github.com/polomarcus/common-trails/commit/7e05c7b2c96147b6f069f0419b4b3301227420dd))

## [0.7.137](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.136...common-trails-v0.7.137) (2026-08-05)


### Bug Fixes

* **startup:** two raw-mode startup errors after the heat_edges DROP ([#549](https://github.com/polomarcus/common-trails/issues/549)) ([93e8708](https://github.com/polomarcus/common-trails/commit/93e8708851ad25d980a66923d4789ed34107c45c))

## [0.7.136](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.135...common-trails-v0.7.136) (2026-08-05)


### Bug Fixes

* **map:** community heatmap rendered NOTHING in prod — pmtiles source loaded the SPA HTML ([#546](https://github.com/polomarcus/common-trails/issues/546)) ([048640d](https://github.com/polomarcus/common-trails/commit/048640dae0e9632bd11af1b0732fc30631a4a01f))

## [0.7.135](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.134...common-trails-v0.7.135) (2026-08-05)


### Miscellaneous

* **ingest:** remove dead matched-era graph_builder module + CDN publish path ([#544](https://github.com/polomarcus/common-trails/issues/544)) ([b06ec30](https://github.com/polomarcus/common-trails/commit/b06ec30720711cf6ffbc148a79bb68a865d8b02f))

## [0.7.134](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.133...common-trails-v0.7.134) (2026-08-05)


### Features

* **heatmap:** directional one-way score + MTB/gravel arrow layer ([#541](https://github.com/polomarcus/common-trails/issues/541)) ([ba897ee](https://github.com/polomarcus/common-trails/commit/ba897ee18a926e740cb02b425b73ad7844b56666))

## [0.7.133](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.132...common-trails-v0.7.133) (2026-08-05)


### Miscellaneous

* **infra:** retire terraform from prod (defuse the €65/mo apply-trap) ([#539](https://github.com/polomarcus/common-trails/issues/539)) ([ade41ea](https://github.com/polomarcus/common-trails/commit/ade41eaf5db7fa3ef0a5ae531e90afd5c1f29d22))

## [0.7.132](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.131...common-trails-v0.7.132) (2026-08-05)


### Miscellaneous

* **ingest:** remove dead matched-era osm_enrich module + CLIs ([#537](https://github.com/polomarcus/common-trails/issues/537)) ([ae2dee9](https://github.com/polomarcus/common-trails/commit/ae2dee98cdc01de936dbe95ca286776977401dae))

## [0.7.131](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.130...common-trails-v0.7.131) (2026-08-04)


### Miscellaneous

* **social:** decommission the inert Garmin Connect integration ([#532](https://github.com/polomarcus/common-trails/issues/532)) ([ae9c091](https://github.com/polomarcus/common-trails/commit/ae9c091ff5098d5e38f9f9b992abf5742cb83d91))

## [0.7.130](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.129...common-trails-v0.7.130) (2026-08-04)


### Miscellaneous

* **i18n:** drop dead route-editor keys after the WASM editor removal ([#518](https://github.com/polomarcus/common-trails/issues/518)/[#519](https://github.com/polomarcus/common-trails/issues/519)) ([#526](https://github.com/polomarcus/common-trails/issues/526)) ([704ae4b](https://github.com/polomarcus/common-trails/commit/704ae4b8079591c67d20b83426fb792ba5dd25be))

## [0.7.129](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.128...common-trails-v0.7.129) (2026-08-04)


### Bug Fixes

* **heat:** gate the last live heat_edges/heat_edges_agg readers so the tables can be DROPPED ([#529](https://github.com/polomarcus/common-trails/issues/529)) ([7fa5267](https://github.com/polomarcus/common-trails/commit/7fa52675ef8011001a116ec44d3555ef27abbe45))

## [0.7.128](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.127...common-trails-v0.7.128) (2026-08-04)


### Features

* **accounts:** link-time self-merge of synthetic Strava accounts + one-off dup migration ([#477](https://github.com/polomarcus/common-trails/issues/477)) ([#527](https://github.com/polomarcus/common-trails/issues/527)) ([d570ca4](https://github.com/polomarcus/common-trails/commit/d570ca4666f14dc5fffb9436bf5dd18445f9bf3e))

## [0.7.127](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.126...common-trails-v0.7.127) (2026-08-04)


### Miscellaneous

* **backend:** remove dead overpass surface-tag service module ([#525](https://github.com/polomarcus/common-trails/issues/525)) ([2fad68d](https://github.com/polomarcus/common-trails/commit/2fad68d9f31de3e924a99d58ce9b2359bfecf5e4))

## [0.7.126](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.125...common-trails-v0.7.126) (2026-08-04)


### Miscellaneous

* **deploy:** drop dead matched-era jobs + assert per-job memory declaratively ([#521](https://github.com/polomarcus/common-trails/issues/521)) ([cf671f1](https://github.com/polomarcus/common-trails/commit/cf671f1354ecd67b28f61c144942d3006d002386))

## [0.7.125](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.124...common-trails-v0.7.125) (2026-08-04)


### Bug Fixes

* **stats:** count community-eligible activities only in compute_community_stats ([#520](https://github.com/polomarcus/common-trails/issues/520)) ([8c3aec8](https://github.com/polomarcus/common-trails/commit/8c3aec8541a416c017d2e360c59538dca8e93c8a))

## [0.7.124](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.123...common-trails-v0.7.124) (2026-08-04)


### Features

* **admin:** archives (contributions) observability card — GET /admin/archives + /admin pills+table ([#490](https://github.com/polomarcus/common-trails/issues/490)) ([344aeb9](https://github.com/polomarcus/common-trails/commit/344aeb99b2eadc5e01ecc8c2c0005dba693eb7f0))
* **admin:** heatmap monitoring dashboard — freshness panel + evolution chart ([#447](https://github.com/polomarcus/common-trails/issues/447)) ([5cf6ac3](https://github.com/polomarcus/common-trails/commit/5cf6ac32624965b0c4790034bef01a8b53dbccc9))
* **admin:** per-user observability dashboard (users table) ([#443](https://github.com/polomarcus/common-trails/issues/443)) ([2d6f2fe](https://github.com/polomarcus/common-trails/commit/2d6f2fe83b22b5da2792501c795dbf8cbe6c7675))
* **archive:** terminal-state email when a Strava archive finishes draining ([#496](https://github.com/polomarcus/common-trails/issues/496)) ([5a63066](https://github.com/polomarcus/common-trails/commit/5a63066b4a7a75980e5c02fba182c07224f178fa))
* **auth:** passwordless email magic-link login + Resend (Phase 1) ([#476](https://github.com/polomarcus/common-trails/issues/476)) ([dd50ebe](https://github.com/polomarcus/common-trails/commit/dd50ebef15ff02e954fc6504e8a9bbf213458166))
* **auth:** unify Strava-connect and email-login accounts ([#477](https://github.com/polomarcus/common-trails/issues/477)) ([1b276b8](https://github.com/polomarcus/common-trails/commit/1b276b8daabbe6066a52ec00cfdc45bd47fadb44))
* **cli:** one-shot founder archive-import CLI (consented ODbL contribution) ([#460](https://github.com/polomarcus/common-trails/issues/460)) ([77ddf06](https://github.com/polomarcus/common-trails/commit/77ddf0688fae1f5e52a9361277902dd50e8f0ca4))
* **contribute:** ergonomic, guided Strava-archive contribution journey ([#464](https://github.com/polomarcus/common-trails/issues/464)) ([76bc5e8](https://github.com/polomarcus/common-trails/commit/76bc5e8a1e42dcf5d8738036194063746e6aa25b))
* **cutover:** public community map, members-only EXPORT ([#509](https://github.com/polomarcus/common-trails/issues/509)) ([27675bf](https://github.com/polomarcus/common-trails/commit/27675bf73cd885992434448bef22d902604ea841))
* **diagnostics:** critical-connector symmetry audit + longest-hop bridge candidates ([#411](https://github.com/polomarcus/common-trails/issues/411)) ([179bbde](https://github.com/polomarcus/common-trails/commit/179bbde05461573fc03287387f5588f00a48b509))
* **export:** GPX 1.1 export of the community heatmap — VisuGPX-compatible ([#436](https://github.com/polomarcus/common-trails/issues/436)) ([275ea95](https://github.com/polomarcus/common-trails/commit/275ea952cfc8d52d1b7b8378976ec5d541e56489))
* **export:** Phase 1 — PMTiles download + GCS storage ([#395](https://github.com/polomarcus/common-trails/issues/395)) ([93ef26d](https://github.com/polomarcus/common-trails/commit/93ef26d5a4b715aeb7488b066bcc728ca3bc3e6a))
* **export:** Phase 2 — MBTiles + GeoJSON download ([#396](https://github.com/polomarcus/common-trails/issues/396)) ([fb131f7](https://github.com/polomarcus/common-trails/commit/fb131f76333010866b1bfc962f8439afb930a2e6))
* **export:** Phase 2.5 — MBTiles raster for Alpine Quest ([#399](https://github.com/polomarcus/common-trails/issues/399)) ([7511205](https://github.com/polomarcus/common-trails/commit/7511205b61b82e22e91824b8a35e6bde07474745))
* **export:** Phase 3 — async &gt; 50km, KML, versioning ([#400](https://github.com/polomarcus/common-trails/issues/400)) ([de48777](https://github.com/polomarcus/common-trails/commit/de4877783c473a416b0c84c8b1f3d0cc81594e2e))
* **gdpr:** DELETE /me/activities/{id} — per-activity deletion incl. community heat contributions ([#498](https://github.com/polomarcus/common-trails/issues/498)) ([d22ce5c](https://github.com/polomarcus/common-trails/commit/d22ce5c573bf0418cbc9651c7412d08511877cbf))
* **gpx:** infer sport from Strava activities.csv + skip out-of-scope ([#339](https://github.com/polomarcus/common-trails/issues/339)) ([2d4b8eb](https://github.com/polomarcus/common-trails/commit/2d4b8ebbd62096239552bb2d513d1b33993bb684))
* **heat:** connectivity diagnostic CLI over the routable graph ([#410](https://github.com/polomarcus/common-trails/issues/410)) ([74fc840](https://github.com/polomarcus/common-trails/commit/74fc8405e4eb89d62d6a0366d8704f7ac4ddf7dc))
* **heatmap:** incremental heat_edges_agg aggregate — fixes PMTiles OOM on db-f1-micro ([#441](https://github.com/polomarcus/common-trails/issues/441)) ([fc7ef0d](https://github.com/polomarcus/common-trails/commit/fc7ef0d997b4225bf78984debf7eb9e14158d45f))
* **heatmap:** keep off-OSM desire lines — env-driven, SSOT'd across both display readers ([#501](https://github.com/polomarcus/common-trails/issues/501)) ([726c8af](https://github.com/polomarcus/common-trails/commit/726c8af1eb9ffe8691fb591dae157ec41adc1c53))
* **heatmap:** osm_way_id fix + ingestion goldens + full test-coverage hardening + perf ([#420](https://github.com/polomarcus/common-trails/issues/420)) ([4c6ec53](https://github.com/polomarcus/common-trails/commit/4c6ec5334aac0426f5b64610b5a874cc7d6d0fbd))
* **heatmap:** raw-trace display source + endpoint masking (flag-gated, default off) ([#505](https://github.com/polomarcus/common-trails/issues/505)) ([5edf49f](https://github.com/polomarcus/common-trails/commit/5edf49f3e7df8e42330884df0f3f413eff774ba1))
* **home:** ONE canonical import place = /strava (remove inline upload forms) ([#487](https://github.com/polomarcus/common-trails/issues/487)) ([511b6d2](https://github.com/polomarcus/common-trails/commit/511b6d2bd319c51ad0e4cb7b23e5242e4a045acc))
* **home:** pivot-first messaging (community heatmap) + fix flagship stats source ([#465](https://github.com/polomarcus/common-trails/issues/465)) ([ef55495](https://github.com/polomarcus/common-trails/commit/ef554953b9f597384a52d37b44846fbb56bb65e8))
* **home:** wire real community stats from build-time stats.json ([#446](https://github.com/polomarcus/common-trails/issues/446)) ([5c3702d](https://github.com/polomarcus/common-trails/commit/5c3702d01001ef5b4a9e018ac533d01039828fcd))
* **import:** classify gravel/mtb from Strava CSV name + clean-heatmap snapshot/restore ([#430](https://github.com/polomarcus/common-trails/issues/430)) ([14fd39b](https://github.com/polomarcus/common-trails/commit/14fd39b1d19214ee8eb454e309c62caff7af4b48))
* **imports:** event-driven trigger of ingest-pending-archives job on /complete ([#482](https://github.com/polomarcus/common-trails/issues/482)) ([c6e438c](https://github.com/polomarcus/common-trails/commit/c6e438c00cc490b9df0a393f6efe48197db4a228))
* **imports:** finish unified contribution dropzone — one canonical import place ([#484](https://github.com/polomarcus/common-trails/issues/484)) ([f2b16fe](https://github.com/polomarcus/common-trails/commit/f2b16fe828060e7190169068f0669c376c93531a))
* **imports:** signed-URL direct-to-GCS upload for large Strava archives ([#455](https://github.com/polomarcus/common-trails/issues/455)) ([6a97a18](https://github.com/polomarcus/common-trails/commit/6a97a186d82e30805ba8cfb488109e62d2e20741))
* **infra:** microservice split — light 512Mi web + scale-to-zero 2Gi worker ([#462](https://github.com/polomarcus/common-trails/issues/462)) ([e583691](https://github.com/polomarcus/common-trails/commit/e583691b605453232c1a49eab19f0e3f569d6383))
* **ingest:** bridge/tunnel critical-connectors layer in routing graph ([#381](https://github.com/polomarcus/common-trails/issues/381)) ([14ae74c](https://github.com/polomarcus/common-trails/commit/14ae74c4ef2b41c57c26d2a1fadcb8fe02fe055b))
* **ingest:** consented Strava-archive community-contribution import ([#451](https://github.com/polomarcus/common-trails/issues/451)) ([d4b41ce](https://github.com/polomarcus/common-trails/commit/d4b41ce55eafdce1276b920db5494f1ad3fddb2f))
* **ingest:** HMM/Viterbi spatial map-matcher (model the whole trajectory) ([#421](https://github.com/polomarcus/common-trails/issues/421)) ([1629dab](https://github.com/polomarcus/common-trails/commit/1629dab6150e6b1e3e48dfa675a859dd1e84472f))
* **ingest:** ping PMTiles rebuild after archive drain imports (event-driven) ([#486](https://github.com/polomarcus/common-trails/issues/486)) ([ec89d82](https://github.com/polomarcus/common-trails/commit/ec89d82f8f6153c91ffe3f56f51a0c79af06b511))
* **ingest:** separate PUBLIC (manual_upload) from PERSONAL (strava_api) provenance (②③) ([#453](https://github.com/polomarcus/common-trails/issues/453)) ([a9f95ba](https://github.com/polomarcus/common-trails/commit/a9f95bae9421fa8337d4ce5436b81623d9e51c02))
* **nav:** disable the /discover page (frozen social/discovery) ([#475](https://github.com/polomarcus/common-trails/issues/475)) ([fd83c13](https://github.com/polomarcus/common-trails/commit/fd83c13b01f41e188538c11ee09bbeacbb811a73))
* **notifications:** "your ride is on the map" toast on new Strava sync ([#449](https://github.com/polomarcus/common-trails/issues/449)) ([bc2b411](https://github.com/polomarcus/common-trails/commit/bc2b41110d2475dfd82495b7163ce6f32f3be3fa))
* **notifications:** post-login notifications for long-running imports ([#309](https://github.com/polomarcus/common-trails/issues/309)) ([d4b1c2d](https://github.com/polomarcus/common-trails/commit/d4b1c2d41fffa6863f1c5bd6799a35d8cecb074f))
* **prod:** warm the web service (min-instances=1) — kill the cold-start ([#468](https://github.com/polomarcus/common-trails/issues/468)) ([c760f2e](https://github.com/polomarcus/common-trails/commit/c760f2e6f7d821a7395e53ab81b9dc3776916d11))
* **prod:** wire UPLOADS_BUCKET into the deploy env (enables signed-URL archive upload) ([#459](https://github.com/polomarcus/common-trails/issues/459)) ([d5d55dc](https://github.com/polomarcus/common-trails/commit/d5d55dca0a860de6fd5ea6aeb40131d24644e71e))
* **routing:** 1.30× cost penalty for critical-connector edges (PR [#381](https://github.com/polomarcus/common-trails/issues/381) S2-C) ([#384](https://github.com/polomarcus/common-trails/issues/384)) ([1d78dae](https://github.com/polomarcus/common-trails/commit/1d78dae6f6c5584d320556f67eb51c76b9c635f7))
* **routing:** shard-first re-architecture + OSM-in-the-shard (restore the motto) ([#417](https://github.com/polomarcus/common-trails/issues/417)) ([75fee35](https://github.com/polomarcus/common-trails/commit/75fee350e08e976d72be8e1acfa2c20e67f9d2b0))
* **strava-archive:** mission 'why upload a file' explainer copy ([#454](https://github.com/polomarcus/common-trails/issues/454)) ([6b85b33](https://github.com/polomarcus/common-trails/commit/6b85b338d1150f5b48095a7fec49733306670e52))
* **strava:** accurate activity count via /athlete/activities pagination ([#306](https://github.com/polomarcus/common-trails/issues/306)) ([0c9e719](https://github.com/polomarcus/common-trails/commit/0c9e7193565ab504c9aa088fb98f014581794181))
* **strava:** cancel personal Strava view UI — /strava becomes a single-job contribution page ([#480](https://github.com/polomarcus/common-trails/issues/480)) ([5f1cf77](https://github.com/polomarcus/common-trails/commit/5f1cf77cac1370d82a7afd955cde63d4e37c1b69))
* **strava:** coherent two-flow /strava — contribute (upload) primary, connect (personal) secondary ([#463](https://github.com/polomarcus/common-trails/issues/463)) ([3b6763a](https://github.com/polomarcus/common-trails/commit/3b6763a3ba2002bb13eda303831accaf39883b55))
* **strava:** friends-beta quick wins — GPX skip + decommission cron + daily alerts ([#342](https://github.com/polomarcus/common-trails/issues/342)) ([d9bf660](https://github.com/polomarcus/common-trails/commit/d9bf66002cc34bae3112e82d6433987a53ebf985))
* **strava:** link 'ton export Strava' to the on-page how-to + 'en 3 minutes' ([#481](https://github.com/polomarcus/common-trails/issues/481)) ([1480e86](https://github.com/polomarcus/common-trails/commit/1480e86f117d4dc6ab03bbf10a20dbd844756ac8))
* **strava:** logged-out visitors SEE the contribution journey, not a login wall ([#469](https://github.com/polomarcus/common-trails/issues/469)) ([6fc7e94](https://github.com/polomarcus/common-trails/commit/6fc7e9499a5f803bcd4b48a6f5ceedf9a6f08d41))
* **strava:** production-access readiness — official Connect button, /support page, brand-guideline fixes ([#350](https://github.com/polomarcus/common-trails/issues/350)) ([63d21e3](https://github.com/polomarcus/common-trails/commit/63d21e3530afb58a94c6b1cd85485d4ab0dc4de3))
* **strava:** render photo thumbnails on activity detail panel ([#307](https://github.com/polomarcus/common-trails/issues/307)) ([105d3ba](https://github.com/polomarcus/common-trails/commit/105d3bab79ec3c73c0bf7977cf82477d4d50b825))
* **strava:** visible jokes during import + ETA on Découverte phase ([#336](https://github.com/polomarcus/common-trails/issues/336)) ([c8426f8](https://github.com/polomarcus/common-trails/commit/c8426f8c8147b8570067d1e4ff11b1cf31b90536))
* **substrate:** osm_ways side-table + region-partitioned osm_road_edges — 26 GB → 4.1 GB (−84%) ([#435](https://github.com/polomarcus/common-trails/issues/435)) ([b194305](https://github.com/polomarcus/common-trails/commit/b194305bf703bd871d2eed7d45ad0ed1a43a1f81))
* **terraform:** MAX_PAGES_PER_USER=2 on resync_strava job ([#382](https://github.com/polomarcus/common-trails/issues/382)) ([d9b661c](https://github.com/polomarcus/common-trails/commit/d9b661c951ad8b15a2333c02715300865da269ec))
* **ux:** split the contribution hero into two labeled intent blocks ([#492](https://github.com/polomarcus/common-trails/issues/492)) ([cc8b1d2](https://github.com/polomarcus/common-trails/commit/cc8b1d21b0a630041d5b16c6e99bee303428bc0d))
* **valhalla:** make valhalla-pbf recipe + env + validated end-to-end results ([#422](https://github.com/polomarcus/common-trails/issues/422)) ([4866e23](https://github.com/polomarcus/common-trails/commit/4866e23c91b27737776fc36c003a779a59170ee9))


### Bug Fixes

* **alembic:** resolve duplicate revision 0054 (export [#400](https://github.com/polomarcus/common-trails/issues/400) + matview-drop [#424](https://github.com/polomarcus/common-trails/issues/424)) ([#426](https://github.com/polomarcus/common-trails/issues/426)) ([f7b9590](https://github.com/polomarcus/common-trails/commit/f7b95901d8b06e7c18be098b3c0140121da2f359))
* **archive:** drain robustness — ingestible-only zip caps + stale-processing recovery + admin requeue ([#495](https://github.com/polomarcus/common-trails/issues/495)) ([da38863](https://github.com/polomarcus/common-trails/commit/da388635a6b957f1aaa17f65398b0f4317646738))
* **archive:** find activities.csv at any depth in re-zipped exports + skip macOS junk ([#489](https://github.com/polomarcus/common-trails/issues/489)) ([710ece1](https://github.com/polomarcus/common-trails/commit/710ece1c9af54d83a056d584f7c8d1e54c321363))
* **archive:** junk zip members no longer count against MAX_ZIP_MEMBERS ([#494](https://github.com/polomarcus/common-trails/issues/494)) ([3c505f8](https://github.com/polomarcus/common-trails/commit/3c505f8e6216d267901fb8a5a1a0e4f58aa45073))
* **archive:** sign PUT URLs via IAM signBlob when credentials have no private key ([#491](https://github.com/polomarcus/common-trails/issues/491)) ([3a50328](https://github.com/polomarcus/common-trails/commit/3a5032831f0aea0c7611341f35be698494aa2462))
* **audit:** close all code-level 🔴 from the full codebase audit ([#427](https://github.com/polomarcus/common-trails/issues/427)) ([0691f6f](https://github.com/polomarcus/common-trails/commit/0691f6f70f7e62b1d32f8228e7e9ded5b5b7e689))
* **consent:** make the ODbL consent checkbox a prominent upload gate ([#514](https://github.com/polomarcus/common-trails/issues/514)) ([1b4d061](https://github.com/polomarcus/common-trails/commit/1b4d061ba964afb98f3ed44ce0caf9bfed9db57e))
* **consent:** record ODbL consent on the loose GPX/FIT path + source-neutral wording (audit gap [#7](https://github.com/polomarcus/common-trails/issues/7)) ([#499](https://github.com/polomarcus/common-trails/issues/499)) ([c621572](https://github.com/polomarcus/common-trails/commit/c6215729243eec676781b764ae1acf5a2c790c4c))
* **db:** cascade-delete heat_edge_contributors when heat_edge is deleted ([#318](https://github.com/polomarcus/common-trails/issues/318)) ([db1a7df](https://github.com/polomarcus/common-trails/commit/db1a7df1c081cea7548b367907c9c16600614821))
* **deploy:** assert Cloud Run JOB env in deploy SSOT — kills the drift class (audit gap [#6](https://github.com/polomarcus/common-trails/issues/6)) ([#502](https://github.com/polomarcus/common-trails/issues/502)) ([5de27ee](https://github.com/polomarcus/common-trails/commit/5de27eea4b2ef427a968c1b17fa8e70d6428c0ec))
* **deploy:** wire TF_VAR_api_public_url so INTERNAL_*_HANDLER_URL vars resolve ([#343](https://github.com/polomarcus/common-trails/issues/343)) ([5b50581](https://github.com/polomarcus/common-trails/commit/5b50581b4e372719aa043917f9ce77bc23cdcf9c))
* **editor:** logged-out save shows draft+login prompt instead of dead-end error ([#438](https://github.com/polomarcus/common-trails/issues/438)) ([c8d1b41](https://github.com/polomarcus/common-trails/commit/c8d1b41bc58c3bceace0849651f54e33d687f645))
* **export:** aggregate heatmap GeoJSON/KML by way — continuous lines for gpx.studio ([#432](https://github.com/polomarcus/common-trails/issues/432)) ([f5cb3cc](https://github.com/polomarcus/common-trails/commit/f5cb3ccc4a52fc3559ae0c323c239dbd52670c29))
* **gpx:** activity_date dropped on HTTP upload (cross-provider dedup broken) ([#330](https://github.com/polomarcus/common-trails/issues/330)) ([0623420](https://github.com/polomarcus/common-trails/commit/0623420d8f1556e201419dc05bf5c21551fc9df7))
* **gpx:** extract .gpx.gz / .fit.gz members from Strava bulk-export ZIPs ([#356](https://github.com/polomarcus/common-trails/issues/356)) ([bf1f03f](https://github.com/polomarcus/common-trails/commit/bf1f03f6220e0f65d80c2b27960f5b5ee6929915))
* **gpx:** harden stdlib XML against DTD entity-expansion attacks (defusedxml) ([#358](https://github.com/polomarcus/common-trails/issues/358)) ([7c289f7](https://github.com/polomarcus/common-trails/commit/7c289f7baa264938e4c95e83f8248e2129a8e7d8))
* **gpx:** harden upload paths against zip-bomb + dense-coord DoS ([#317](https://github.com/polomarcus/common-trails/issues/317)) ([182612f](https://github.com/polomarcus/common-trails/commit/182612f9f7d8218a9aba8324c75ebbd95cfc6788))
* **gpx:** per-member error for ZIP-member size + path-traversal ([#359](https://github.com/polomarcus/common-trails/issues/359)) ([2dec366](https://github.com/polomarcus/common-trails/commit/2dec366b5fd9a4e40d249ff1da0395d604fb88d5))
* **gpx:** unblock event loop + archive every bundle + index dedup ([#319](https://github.com/polomarcus/common-trails/issues/319)) ([6c3c132](https://github.com/polomarcus/common-trails/commit/6c3c132a044e348d2dd078d4a9a130b4ed3c5314))
* **heat-edges:** drop redundant 5dp re-snap on OSM-matched edges ([#374](https://github.com/polomarcus/common-trails/issues/374)) ([2aca743](https://github.com/polomarcus/common-trails/commit/2aca74303706ea448b08991ae377745246a9e617))
* **heat-edges:** pass_count tied to per-activity contributions, not UPSERT count ([#369](https://github.com/polomarcus/common-trails/issues/369)) ([a28026f](https://github.com/polomarcus/common-trails/commit/a28026ff7a7e51b02e61bf907b77f1026f822b16))
* **heatmap/raw:** reject GPS-glitch outliers before render ([#511](https://github.com/polomarcus/common-trails/issues/511)) ([1a790c1](https://github.com/polomarcus/common-trails/commit/1a790c17cbe7cc047adac3fcd2ab250fd8a4e8fd))
* **heatmap:** PMTiles light path touches only heat_edges_agg — no raw scan on f1-micro ([#441](https://github.com/polomarcus/common-trails/issues/441) follow-up) ([#442](https://github.com/polomarcus/common-trails/issues/442)) ([43023c1](https://github.com/polomarcus/common-trails/commit/43023c198472e17eaae7b7dc5f55286da9d64f10))
* **heatmap:** tame the glow so density reads as a gradient, not an orange blob ([#515](https://github.com/polomarcus/common-trails/issues/515)) ([b13e4fb](https://github.com/polomarcus/common-trails/commit/b13e4fb66498ed6a65251feebed1527bdf121d1d))
* **home:** hero heatmap renders static PMTiles lines — kill the purple fog + live-MVT DB load ([#493](https://github.com/polomarcus/common-trails/issues/493)) ([aa1a60a](https://github.com/polomarcus/common-trails/commit/aa1a60a841131556afbbab50e2d556a8a98729ae))
* **home:** logged-in arrival on / stays on the pivot showcase (no auto-redirect to /map) ([#470](https://github.com/polomarcus/common-trails/issues/470)) ([6b2d3a0](https://github.com/polomarcus/common-trails/commit/6b2d3a0e7d221b6fad795c5ba1a8a87b16a6661a))
* **home:** real GitHub repo link + heatmap-first meta description ([#497](https://github.com/polomarcus/common-trails/issues/497)) ([21c79de](https://github.com/polomarcus/common-trails/commit/21c79de60fcea45f83858ceca666cad36d532042))
* **i18n:** consolidate the MAP surface through t() — no more FR/EN mix ([#471](https://github.com/polomarcus/common-trails/issues/471)) ([521c356](https://github.com/polomarcus/common-trails/commit/521c356c6f98193ae880a80db7db70a4d6761ca8))
* **i18n:** honest Strava-connect copy (PERSONAL) + clearer Explorer toggle ([#474](https://github.com/polomarcus/common-trails/issues/474)) ([3e5949f](https://github.com/polomarcus/common-trails/commit/3e5949f09908a8f23f4ff23596a8e3cf390be741))
* **i18n:** route hardcoded FR literals through t() — coherent single-locale site ([#452](https://github.com/polomarcus/common-trails/issues/452)) ([8625744](https://github.com/polomarcus/common-trails/commit/86257448a19a66dbe6dfc2af5f26087dffc5bf15))
* **i18n:** route Strava import wizard strings through i18n (no more FR/EN mix) ([#440](https://github.com/polomarcus/common-trails/issues/440)) ([7790367](https://github.com/polomarcus/common-trails/commit/7790367f0e3c12f30bb40bd6e82f5db692c359fd))
* **i18n:** translate hardcoded French strings in the Strava import flow ([#388](https://github.com/polomarcus/common-trails/issues/388)) ([5ccfa1d](https://github.com/polomarcus/common-trails/commit/5ccfa1d2f6c48d8656e3de1ca3301a236563f0a0))
* **imports:** enforce MAX_GPX_SIZE per-file on /imports/files single .gpx + .fit branches ([#354](https://github.com/polomarcus/common-trails/issues/354)) ([f21d519](https://github.com/polomarcus/common-trails/commit/f21d5196ce467cec0cdcd6e380c81f20d4b7f5ce))
* **imports:** raise archive cap to 2 GB — real Strava exports are 1 GB+ ([#483](https://github.com/polomarcus/common-trails/issues/483)) ([33fbd4e](https://github.com/polomarcus/common-trails/commit/33fbd4e371b7febf68d194ca5cd362d115d7c017))
* **imports:** size-guard the signed-URL archive upload ([#455](https://github.com/polomarcus/common-trails/issues/455) fast-follow) ([#457](https://github.com/polomarcus/common-trails/issues/457)) ([34ad349](https://github.com/polomarcus/common-trails/commit/34ad349978122ba5ef67eaef34ed4f9b2f3120b6))
* **infra:** codify FRONTEND_URL on Cloud Run (Strava OAuth localhost bug) ([#328](https://github.com/polomarcus/common-trails/issues/328)) ([9bb1e47](https://github.com/polomarcus/common-trails/commit/9bb1e470ccff498c20b20cbe77efebfcde173ad0))
* **infra:** grant deploy SA monitoring.dashboardEditor (fixes apply 403) ([#364](https://github.com/polomarcus/common-trails/issues/364)) ([6791136](https://github.com/polomarcus/common-trails/commit/6791136dae03a4e0b48a1905dc3ee7539c3795e8))
* **infra:** scheduler audience must use var.api_public_url (matches env var) ([#406](https://github.com/polomarcus/common-trails/issues/406)) ([1c7b765](https://github.com/polomarcus/common-trails/commit/1c7b765344b55d639a9c05287f36d49ec0575cec))
* **infra:** wire HEATMAP_GCS_BUCKET env on the api service ([#398](https://github.com/polomarcus/common-trails/issues/398)) ([c95b84b](https://github.com/polomarcus/common-trails/commit/c95b84b060abdf3603a04337487f34e27589fe47))
* **ingest:** arc-length bucket Valhalla matched_points along OSM polyline ([#376](https://github.com/polomarcus/common-trails/issues/376)) ([8e17fc1](https://github.com/polomarcus/common-trails/commit/8e17fc156a8ae0e6e2b9d5152323c461c88f2b31))
* **ingest:** coerce ISO 8601 string activity_date before dedup-window subtraction ([#379](https://github.com/polomarcus/common-trails/issues/379)) ([41ae36f](https://github.com/polomarcus/common-trails/commit/41ae36f80315a12b5e4f719466a1a9ca867a7f62))
* **ingest:** heat_edges.match_source 'grid' → 'grid_fallback' (doc/code drift) ([#353](https://github.com/polomarcus/common-trails/issues/353)) ([354e5e2](https://github.com/polomarcus/common-trails/commit/354e5e2ba4be32eff48931f517bbebc241d59f21))
* **ingest:** motorway under-passage in critical-connectors CTE + boundary test pin ([#383](https://github.com/polomarcus/common-trails/issues/383)) ([5184612](https://github.com/polomarcus/common-trails/commit/51846129c9c361fd06b09ab5764d7d8d374475d7))
* **ingest:** skip OSM-match + heat_edges write under the raw-trace pivot ([#513](https://github.com/polomarcus/common-trails/issues/513)) ([4accd4d](https://github.com/polomarcus/common-trails/commit/4accd4dc6cd5916395a4b20cb7c02296518c6f9f))
* **ingest:** unified sport classifier across all intakes + folder upload UX ([#431](https://github.com/polomarcus/common-trails/issues/431)) ([14b183c](https://github.com/polomarcus/common-trails/commit/14b183ca556dcd8f98dd7cbed842d3073deee27e))
* **map-import:** in-flight progress message is grey + has a spinner ([#327](https://github.com/polomarcus/common-trails/issues/327)) ([30d8e77](https://github.com/polomarcus/common-trails/commit/30d8e77ab0fb2b18e4fdecd5ead39fdfd0cd1a7f))
* **map-import:** real progress + actionable error messages ([#323](https://github.com/polomarcus/common-trails/issues/323)) ([6cb97d2](https://github.com/polomarcus/common-trails/commit/6cb97d291fa1842ad47ad7f419d140727348fdc4))
* **map:** clicking the map while proposals pending recovers + places waypoint ([#401](https://github.com/polomarcus/common-trails/issues/401)) ([751cdc8](https://github.com/polomarcus/common-trails/commit/751cdc8c0ae097779d1dfca2793d4a35934518f5))
* **map:** consolidate import/upload entry points + accurate K-anonymity copy ([#461](https://github.com/polomarcus/common-trails/issues/461)) ([59de08d](https://github.com/polomarcus/common-trails/commit/59de08d32dad9569cf52afde5a07221b549eea34))
* **map:** hydration mismatch on /map for returning users (React [#418](https://github.com/polomarcus/common-trails/issues/418)) ([#310](https://github.com/polomarcus/common-trails/issues/310)) ([ab54b16](https://github.com/polomarcus/common-trails/commit/ab54b16221f52d90197e218ca477c19639aa5cc4))
* **map:** rename ambiguous '⬇️ Fond'/'Basemap' export button → 'Exporter'/'Export' ([#472](https://github.com/polomarcus/common-trails/issues/472)) ([c111fab](https://github.com/polomarcus/common-trails/commit/c111fabe1019641c0086538e9a5240073b5c5916))
* **monitoring+e2e:** heat-quality O(n), Makefile occitanie default, E2E UI/MVT/Strava-mock hardening + ES/IT regions ([#434](https://github.com/polomarcus/common-trails/issues/434)) ([93e824e](https://github.com/polomarcus/common-trails/commit/93e824e8b19d2e2e99a88a317c5059df147638a8))
* **notifications:** PERSONAL-only "ride synced" toast copy (post-pivot) ([#467](https://github.com/polomarcus/common-trails/issues/467)) ([5dad75c](https://github.com/polomarcus/common-trails/commit/5dad75c40e8b115fe07b28ea4f1be3cb22ecd10f))
* **raw:** finish the cutover on the read/export paths (no 500 on dropped substrate, no OOM) ([#517](https://github.com/polomarcus/common-trails/issues/517)) ([2d507a3](https://github.com/polomarcus/common-trails/commit/2d507a38d8c3e37bdc1862656cd36d00a95c01e1))
* release DB pool between batches, paginate stats fetches, pause polling when tab hidden ([#311](https://github.com/polomarcus/common-trails/issues/311)) ([ea8fb11](https://github.com/polomarcus/common-trails/commit/ea8fb11029adac9c35c64a21e86a1e582d490500))
* **route-editor:** line-drag deviates the route, doesn't annotate it ([#370](https://github.com/polomarcus/common-trails/issues/370)) ([c97774a](https://github.com/polomarcus/common-trails/commit/c97774a90da9b33c00988a142353564e24292382))
* **route-editor:** line-drag off-by-one — waypoint inserts at segIdx, not segIdx+1 ([#366](https://github.com/polomarcus/common-trails/issues/366)) ([a48f04d](https://github.com/polomarcus/common-trails/commit/a48f04de5d7555ac71204d9bd8a08ace93f79150))
* **routing-client:** recovery loads wrong Worker script — every post-fallback session was permanently degraded ([b8de2c8](https://github.com/polomarcus/common-trails/commit/b8de2c84fc8ce0d436380b03c1809730ad926cf0))
* **routing:** couche propre — chain-and-chunk heat + node weld, trails segmentés, pass_count, shards PACA/Corse, garde substrat ([#437](https://github.com/polomarcus/common-trails/issues/437)) ([58a2df6](https://github.com/polomarcus/common-trails/commit/58a2df6e73e027c52471185be321e75251062a34))
* **routing:** drag-edit U-turn de-spur — corrected detection, opt-in, display-only ([#405](https://github.com/polomarcus/common-trails/issues/405)) ([8139767](https://github.com/polomarcus/common-trails/commit/813976791cdd88c839ab73f282acda736bc37ff7))
* **routing:** gravel/mtb route on dense heat-only local area, not 200km shard ([#413](https://github.com/polomarcus/common-trails/issues/413)) ([f795fbc](https://github.com/polomarcus/common-trails/commit/f795fbcad77f4c067f0823600152a232caa08e0b))
* **routing:** off-road follow-ups from [#402](https://github.com/polomarcus/common-trails/issues/402) review (bbox tracking + explicit skip_osm) ([#404](https://github.com/polomarcus/common-trails/issues/404)) ([3662de7](https://github.com/polomarcus/common-trails/commit/3662de74e8a350e971743bc5d2697b57759b2296))
* **routing:** off-road loads bounded full-OSM area, no Lez straight-line ([#402](https://github.com/polomarcus/common-trails/issues/402)) ([3db6ff9](https://github.com/polomarcus/common-trails/commit/3db6ff95b00d0a4a7cd76dd051fac9988266f69b))
* **routing:** paved-skeleton connectors fix gravel/mtb shard fragmentation ([#429](https://github.com/polomarcus/common-trails/issues/429)) ([15db85c](https://github.com/polomarcus/common-trails/commit/15db85c1f94a681757a440b3d9f76a7d7f3a613e))
* **strava + pmtiles:** defensive outer except in _run_strava_import + prefer non-null way_geometry ([#357](https://github.com/polomarcus/common-trails/issues/357)) ([b772f61](https://github.com/polomarcus/common-trails/commit/b772f613b1dbf2cd467785c1837a6c4abba5ea98))
* **strava:** _run_photo_import — short-lived sessions per batch ([#331](https://github.com/polomarcus/common-trails/issues/331)) ([da18611](https://github.com/polomarcus/common-trails/commit/da186114d4ad8b1fc6c1cd52717565be9cc90f4d))
* **strava-client:** Retry-After propagation + photo 429 batch break + shared httpx client ([#332](https://github.com/polomarcus/common-trails/issues/332)) ([bc5500c](https://github.com/polomarcus/common-trails/commit/bc5500c8fc3c4e2b53fa71ae0cc03795bb436e3c))
* **strava:** audit S2 bundle — phase-2 notif + CLI cascade/cap + ghost existing_ids rollback ([#347](https://github.com/polomarcus/common-trails/issues/347)) ([99dbf6c](https://github.com/polomarcus/common-trails/commit/99dbf6c26cb1c4012a3abd500c02c7df4921069b))
* **strava:** audit S2.6 + S2.7 + S3 cleanup bundle — dedup tighten, account preference, sentry parity, run_import tests ([#348](https://github.com/polomarcus/common-trails/issues/348)) ([2105665](https://github.com/polomarcus/common-trails/commit/2105665e446d339aadbed360560a201c95fb641b))
* **strava:** audit S3.3 — replace DB-backed OAuth state with signed JWT ([#349](https://github.com/polomarcus/common-trails/issues/349)) ([ca2b660](https://github.com/polomarcus/common-trails/commit/ca2b66081c3c30ceb0ac3e0eeb486451e8809880))
* **strava:** codify env vars + repair resync_strava import + agent doc ([#329](https://github.com/polomarcus/common-trails/issues/329)) ([2b32976](https://github.com/polomarcus/common-trails/commit/2b329765059081a46e25763b2fe3c971e5fa55da))
* **strava:** emit user notification when ImportJob is OOM-killed (signal-9 bypass) ([#344](https://github.com/polomarcus/common-trails/issues/344)) ([28d9990](https://github.com/polomarcus/common-trails/commit/28d99905028295c798cb2c247b6ab37284f7977e))
* **strava:** encrypt IntegrationAccount tokens at rest (Fernet + Secret Manager) ([#335](https://github.com/polomarcus/common-trails/issues/335)) ([01dcd05](https://github.com/polomarcus/common-trails/commit/01dcd05e04461dcd416a8e582f9be8c8fafc803d))
* **strava:** hydration mismatch on /strava (React [#418](https://github.com/polomarcus/common-trails/issues/418)) ([#316](https://github.com/polomarcus/common-trails/issues/316)) ([bacac55](https://github.com/polomarcus/common-trails/commit/bacac556b970932c494c66ea12554d2926137c56))
* **strava:** job lifecycle — user-scoped queue + page cap + stale cutoff + OAuth TTL ([#333](https://github.com/polomarcus/common-trails/issues/333)) ([8e312b4](https://github.com/polomarcus/common-trails/commit/8e312b4f196238f0ed028385cb1127ebb03c5f43))
* **strava:** observability + perf — Sentry, SQL agg, Cloud Tasks heat, preview cache, failed phase surface ([#334](https://github.com/polomarcus/common-trails/issues/334)) ([b6a3e3e](https://github.com/polomarcus/common-trails/commit/b6a3e3e86933bd931b6b306487d2a789a7868914))
* **strava:** post-[#345](https://github.com/polomarcus/common-trails/issues/345) audit S1s — Job Phase 3 pool hygiene + CLI secret leak + webhook subscription_id allow-list ([#346](https://github.com/polomarcus/common-trails/issues/346)) ([8686089](https://github.com/polomarcus/common-trails/commit/86860899e7a80e643b3fe4c8a9130c134037caf7))
* **strava:** prevent OOM in 512 Mi import job — bound osm_grid_cache ([#345](https://github.com/polomarcus/common-trails/issues/345)) ([4fa873b](https://github.com/polomarcus/common-trails/commit/4fa873bfe21bfcb1b3e2ff0b9d9deb20294527a6))
* **strava:** sweep — sentry capture-first + suppression in 4 sister handlers ([#360](https://github.com/polomarcus/common-trails/issues/360)) ([ff182f2](https://github.com/polomarcus/common-trails/commit/ff182f29f6507331b766819cee50d6b4e32e6f44))
* **strava:** sync longevity — trailing-window reconciliation, subscription liveness, reconnect prompt ([#439](https://github.com/polomarcus/common-trails/issues/439)) ([4a9e840](https://github.com/polomarcus/common-trails/commit/4a9e840624fb0ae12c2d72cc516ec0bf566f983b))
* **strava:** trim verbose /strava copy — collapse the why, drop the duplicated tagline ([#478](https://github.com/polomarcus/common-trails/issues/478)) ([fdb98de](https://github.com/polomarcus/common-trails/commit/fdb98defb358d78ebe4e984d2cc85297638ba7ef))
* **strava:** unwedge prod — short-lived sessions + persist gps_total ([#314](https://github.com/polomarcus/common-trails/issues/314)) ([5573b57](https://github.com/polomarcus/common-trails/commit/5573b57520e4ac6e754d1fb389a438b9f2c3207a))
* **strava:** widen import_jobs.cursor to Text + write last_synced_at on every successful sync ([#355](https://github.com/polomarcus/common-trails/issues/355)) ([0f7d311](https://github.com/polomarcus/common-trails/commit/0f7d3118b609798b2717812b0fc3b1a2dd378de0))
* **strava:** zombie job cleanup + queue UI + admin cancel ([#338](https://github.com/polomarcus/common-trails/issues/338)) ([fdfb3f5](https://github.com/polomarcus/common-trails/commit/fdfb3f55636ebd58ef4408a5617d5740ccde992d))
* **terraform:** add JWT_SECRET to import_strava + resync_strava Jobs ([#337](https://github.com/polomarcus/common-trails/issues/337)) ([c318522](https://github.com/polomarcus/common-trails/commit/c318522fd0a52e3b482d880567b82a2578e46fd7))
* **ui:** beta banner no longer overlaps route-editor + proposal sheets ([#365](https://github.com/polomarcus/common-trails/issues/365)) ([46fcb8d](https://github.com/polomarcus/common-trails/commit/46fcb8dd32bfacffd60f224b67255f07db37ca00))
* **ui:** declutter the top bar — fewer top-level links + one map control ([#473](https://github.com/polomarcus/common-trails/issues/473)) ([238e1cd](https://github.com/polomarcus/common-trails/commit/238e1cda4e9369b179ee09664b60832d5b6d4644))
* **ui:** GPX drop zone only reacts to OS file drags, not internal page drags ([#367](https://github.com/polomarcus/common-trails/issues/367)) ([cb6392d](https://github.com/polomarcus/common-trails/commit/cb6392d46b09747e0e2e69c6ee987d95865a7a4a))


### Miscellaneous

* decommission the in-app WASM routing / route editor ([#518](https://github.com/polomarcus/common-trails/issues/518)) ([134737d](https://github.com/polomarcus/common-trails/commit/134737d5f849e7161d47223dd47daa3496ef6f67))
* delete broken-at-scale backend GET /routing endpoint ([#372](https://github.com/polomarcus/common-trails/issues/372)) ([be83318](https://github.com/polomarcus/common-trails/commit/be83318fda1fda7fcd561b97044affe52c020945))
* **e2e:** drop dead full.json routing specs + shipped TODO skip ([#389](https://github.com/polomarcus/common-trails/issues/389)) ([280d827](https://github.com/polomarcus/common-trails/commit/280d82783dcd7981e9809337f2e36a5ed6308143))
* **email:** send from verified chemins-communs.fr, not the resend.dev test sender ([#479](https://github.com/polomarcus/common-trails/issues/479)) ([84928b3](https://github.com/polomarcus/common-trails/commit/84928b338b812738e0c93b61c17e0f51f61b3a31))
* **frontend:** ship sourcemaps to prod for debuggable React errors ([#326](https://github.com/polomarcus/common-trails/issues/326)) ([4c9ecd7](https://github.com/polomarcus/common-trails/commit/4c9ecd7d28712f2f852fc2d9f9a4ec43fd53db5f))
* **ingest:** close S3 nits from heat_edges + Valhalla bucket reviews ([#377](https://github.com/polomarcus/common-trails/issues/377)) ([2383338](https://github.com/polomarcus/common-trails/commit/2383338467cb848f94698750501933c4580fd6f1))
* local-routing-refresh skill + Layer 3 golden snapshot ([#385](https://github.com/polomarcus/common-trails/issues/385)) ([b34fae6](https://github.com/polomarcus/common-trails/commit/b34fae66081bf7d608de9b0d673f687e1205b19f))
* **oauth:** drop dead `oauth_states` table (post-[#349](https://github.com/polomarcus/common-trails/issues/349) cleanup) ([#351](https://github.com/polomarcus/common-trails/issues/351)) ([e1c787b](https://github.com/polomarcus/common-trails/commit/e1c787ba97aaab6e8cc47a8fc9fca9325ef92534))
* project-wide audit - bug fixes + doc drift cleanup ([#312](https://github.com/polomarcus/common-trails/issues/312)) ([9551933](https://github.com/polomarcus/common-trails/commit/955193342b5912ef4a108205b8af6e0bf0722266))
* release ([#305](https://github.com/polomarcus/common-trails/issues/305)) ([c3767ec](https://github.com/polomarcus/common-trails/commit/c3767eca755a4547ad8c894b499a180d0b9e1c36))
* **skills:** add `heatmap` skill — ingestion brain + connectivity diagnostics ([#408](https://github.com/polomarcus/common-trails/issues/408)) ([b596127](https://github.com/polomarcus/common-trails/commit/b596127ffff2c7ffde99b4dff60193fcb2dec391))
* **skills:** add `routing` skill — client-routing brain + route-quality smoke ([#407](https://github.com/polomarcus/common-trails/issues/407)) ([925a25a](https://github.com/polomarcus/common-trails/commit/925a25a58bd7477d28797100868378ffc963442e))
* **strava:** route all /api/v3 calls through configurable STRAVA_API_BASE ([#387](https://github.com/polomarcus/common-trails/issues/387)) ([6763f29](https://github.com/polomarcus/common-trails/commit/6763f292e346f8e8fcabbbf1ec02e8802bbb1f05))
* **tests:** disable routing test suite ahead of the raw-trace cutover ([#510](https://github.com/polomarcus/common-trails/issues/510)) ([cd3e818](https://github.com/polomarcus/common-trails/commit/cd3e8189fb3e3a73f6893bb4c393abc6f6e2bb5f))
* **wasm-router:** rebuild .fgraph shards w/ road sport + post-Valhalla heat_edges ([#392](https://github.com/polomarcus/common-trails/issues/392)) ([1b304d3](https://github.com/polomarcus/common-trails/commit/1b304d358648dac77398fb6e0ad7a427e0b078ce))

## [0.7.123](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.122...common-trails-v0.7.123) (2026-05-17)


### Features

* **gpx:** auto-detect sport from GPX &lt;trk&gt;&lt;type&gt; on upload ([#301](https://github.com/polomarcus/common-trails/issues/301)) ([#302](https://github.com/polomarcus/common-trails/issues/302)) ([ce5d162](https://github.com/polomarcus/common-trails/commit/ce5d1626707cc85196c04f4f3b77188a7909341d))


### Bug Fixes

* **strava:** loading state on Connecter Strava button ([#303](https://github.com/polomarcus/common-trails/issues/303)) ([133c6df](https://github.com/polomarcus/common-trails/commit/133c6df0a80fb787f30d92c81a7c158bfaaa0932))
* **strava:** reassure user during import bootstrap ([#304](https://github.com/polomarcus/common-trails/issues/304)) ([5456053](https://github.com/polomarcus/common-trails/commit/54560535fc3282fa721ed2aebb937697e6c64d54))


### Miscellaneous

* **import-osm:** drop `service` highway from import filter ([#298](https://github.com/polomarcus/common-trails/issues/298)) ([4d4575d](https://github.com/polomarcus/common-trails/commit/4d4575d2c6c53176a48dbb69b5d4f9ea624e09d4))

## [0.7.122](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.121...common-trails-v0.7.122) (2026-05-16)


### Miscellaneous

* **dem:** bake Cataluna + Italia Nord-Ovest HGT tiles into the image ([#294](https://github.com/polomarcus/common-trails/issues/294)) ([d275bc2](https://github.com/polomarcus/common-trails/commit/d275bc29c10726a8332c8e1892514d07c958ec3b))

## [0.7.121](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.120...common-trails-v0.7.121) (2026-05-16)


### Miscellaneous

* **orm:** partial index parity on heat_edges.osm_way_id ([#292](https://github.com/polomarcus/common-trails/issues/292)) ([15a1629](https://github.com/polomarcus/common-trails/commit/15a1629afd724fc932606ee53e30bca102a73562))

## [0.7.120](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.119...common-trails-v0.7.120) (2026-05-16)


### Features

* **prod-followups:** 4 bundled fixes — ORM drift / recompute Job / rebuild guard / admin job feed ([#289](https://github.com/polomarcus/common-trails/issues/289)) ([c34d239](https://github.com/polomarcus/common-trails/commit/c34d239cc2759af86440488fde563bc4a0079678))

## [0.7.119](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.118...common-trails-v0.7.119) (2026-05-16)


### Features

* **admin:** beta-time visibility — engagement, providers, heatmap quality, recent feeds ([#288](https://github.com/polomarcus/common-trails/issues/288)) ([362c4bb](https://github.com/polomarcus/common-trails/commit/362c4bb499ce8b709fc3ec2344147775f1102def))

## [0.7.118](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.117...common-trails-v0.7.118) (2026-05-16)


### Miscellaneous

* **terraform:** 3 more IAM grants the deploy SA needs to refresh state ([#286](https://github.com/polomarcus/common-trails/issues/286)) ([cb8d655](https://github.com/polomarcus/common-trails/commit/cb8d655ee3bfdd1e2a4210f1773f9a1f09ea1f5f))
* **terraform:** grant deploy SA compute.viewer + cloudtasks.viewer ([#283](https://github.com/polomarcus/common-trails/issues/283)) ([f2b697a](https://github.com/polomarcus/common-trails/commit/f2b697a943cd57e49fe3255e56ea4119f7087dcb))
* **terraform:** ignore autoresize-grown disk_size + manually-patched tier ([#285](https://github.com/polomarcus/common-trails/issues/285)) ([6002f9f](https://github.com/polomarcus/common-trails/commit/6002f9fecba850df766166cafc093e26374ac7fa))
* **terraform:** publish heatmap cache to CDN on every rebuild + agent doc ([#287](https://github.com/polomarcus/common-trails/issues/287)) ([9f670a2](https://github.com/polomarcus/common-trails/commit/9f670a2e83c0171df32d4286c429fbe4d04388a8))

## [0.7.117](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.116...common-trails-v0.7.117) (2026-05-16)


### Features

* **map:** fork-UX polish + drop deprecated upstream-PR endpoints ([#279](https://github.com/polomarcus/common-trails/issues/279)) ([c247cd4](https://github.com/polomarcus/common-trails/commit/c247cd4b78c8da89dc90d1596041c7b549ec37aa))


### Miscellaneous

* **i18n:** translate 20+ user-visible FR strings in /discover and /stats ([#272](https://github.com/polomarcus/common-trails/issues/272)) ([7f19a35](https://github.com/polomarcus/common-trails/commit/7f19a3539a3372537e47993f223abc142b09eeb5))
* release 0.7.116 ([#277](https://github.com/polomarcus/common-trails/issues/277)) ([caf42ba](https://github.com/polomarcus/common-trails/commit/caf42ba972cb342017ee54ecb2e24ff77da7f833))

## [0.7.116](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.115...common-trails-v0.7.116) (2026-05-16)


### Bug Fixes

* **share:** render OG card as PNG (was SVG, dropped by WhatsApp/Twitter) ([#273](https://github.com/polomarcus/common-trails/issues/273)) ([fca4a2c](https://github.com/polomarcus/common-trails/commit/fca4a2cd9b0fa460f126a9b9a5b4d8d0914e8a32))

## [0.7.115](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.114...common-trails-v0.7.115) (2026-05-16)


### Features

* **ops:** group_edges_osm as Cloud Run Job (in-region, 10-30× faster) ([#270](https://github.com/polomarcus/common-trails/issues/270)) ([9955901](https://github.com/polomarcus/common-trails/commit/9955901036c8cf5ad0909a99ecb7bdcc77929aeb))

## [0.7.114](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.113...common-trails-v0.7.114) (2026-05-16)


### Features

* **beta:** 4 deferred audit items + group_edges_osm progress logging ([#269](https://github.com/polomarcus/common-trails/issues/269)) ([00bd001](https://github.com/polomarcus/common-trails/commit/00bd0016e3ca3f6975336a63e9e80e9d03efb73f))

## [0.7.113](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.112...common-trails-v0.7.113) (2026-05-15)


### Features

* **route-actions:** i18n the 4 main action buttons + morning handoff doc ([#268](https://github.com/polomarcus/common-trails/issues/268)) ([6b2a9e8](https://github.com/polomarcus/common-trails/commit/6b2a9e822e6913a18e301803cf7da4842cf35ba1))
* **seo:** PNG og-card + per-page OG overrides + dynamic sitemap ([#265](https://github.com/polomarcus/common-trails/issues/265)) ([b687ec7](https://github.com/polomarcus/common-trails/commit/b687ec7822da588592d803f7b9671d7f1891dc88))


### Bug Fixes

* **beta:** 5 P0 fixes from friends-beta audit ([#266](https://github.com/polomarcus/common-trails/issues/266)) ([0fb528e](https://github.com/polomarcus/common-trails/commit/0fb528ec0d9dc4b5b72199380ac33d0555a5dfeb))

## [0.7.112](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.111...common-trails-v0.7.112) (2026-05-15)


### Bug Fixes

* **ingest:** Overpass writer now populates ele_* + surface_confidence ([#262](https://github.com/polomarcus/common-trails/issues/262)) ([918a0fa](https://github.com/polomarcus/common-trails/commit/918a0fa25bc9d365b66b5dd0e95bac2f773f52a1))

## [0.7.111](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.110...common-trails-v0.7.111) (2026-05-15)


### Features

* **elevation+surface:** D+ smoothing + local DEM at PBF time + surface_confidence end-to-end ([#259](https://github.com/polomarcus/common-trails/issues/259)) ([a9d89ed](https://github.com/polomarcus/common-trails/commit/a9d89ed8fccdcd13ab162b34c2f7856968de5e13))
* **make:** prod rebuild targets + URGENT dry-run bug fix ([#249](https://github.com/polomarcus/common-trails/issues/249)) ([f533e07](https://github.com/polomarcus/common-trails/commit/f533e07ae272086e48591ed856609d7f6f201635))
* **ops:** bake HGT tiles into image + activities D+ backfill CLI ([#261](https://github.com/polomarcus/common-trails/issues/261)) ([d307dca](https://github.com/polomarcus/common-trails/commit/d307dca30c1c58f727f45ec558c81b69aa6029fe))
* **ops:** Cloud Run Job for OSM PBF import (10-30× faster than proxy) ([#255](https://github.com/polomarcus/common-trails/issues/255)) ([67fc05e](https://github.com/polomarcus/common-trails/commit/67fc05e1fdcbc6860b3729b4b27c339729bdcb52))
* **sentry:** scrub raw lat/lon + hashed bbox span names ([#243](https://github.com/polomarcus/common-trails/issues/243)) ([d83c240](https://github.com/polomarcus/common-trails/commit/d83c240cd4e3b70794ce165d408c10cc7ad0fcf0))


### Bug Fixes

* **group_edges_osm:** set osm_way_id + accept parallel-near-360°/180° ([#250](https://github.com/polomarcus/common-trails/issues/250)) ([ceb26c2](https://github.com/polomarcus/common-trails/commit/ceb26c25a789680d870b8b8e385c31a93f410092))
* **stats:** drop hardcoded "51 000 / 48 000" + relabel contributors ([#258](https://github.com/polomarcus/common-trails/issues/258)) ([4a59ae6](https://github.com/polomarcus/common-trails/commit/4a59ae6d6e1f3b59d138abbcedaa6d6e32e79700))
* **strava:** close TEST_MODE CSRF hole + state guard tests ([#244](https://github.com/polomarcus/common-trails/issues/244)) ([d46528d](https://github.com/polomarcus/common-trails/commit/d46528dfb4f49b9e7c87ca9d06aabc97cfa90d4c))
* **test:** pipeline_patterns silent bit-rot — isolate by bbox ([#242](https://github.com/polomarcus/common-trails/issues/242)) ([7c53777](https://github.com/polomarcus/common-trails/commit/7c537771c7b5915fb21c5d9d7e922d7c02c72074))

## [0.7.110](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.109...common-trails-v0.7.110) (2026-05-15)


### Features

* **monitoring:** heat-edge spaghetti alerts via build_pmtiles hook ([#247](https://github.com/polomarcus/common-trails/issues/247)) ([f4b3179](https://github.com/polomarcus/common-trails/commit/f4b31790efbc526a50339333aa5ca7fa008416fa))
* **trails:** GR/GRP/GT/PR/EV from local PBF + kill runtime Overpass in prod ([#230](https://github.com/polomarcus/common-trails/issues/230)) ([f0b29a0](https://github.com/polomarcus/common-trails/commit/f0b29a0b0125576f1fd4985a9b3b0218b1d57bf6))


### Bug Fixes

* **api:** /readyz returns 200 'warming' when heat_edges empty ([#227](https://github.com/polomarcus/common-trails/issues/227)) ([a7863c9](https://github.com/polomarcus/common-trails/commit/a7863c935ea7c742675e89153a56b1648e76ab19))
* **home:** real stats + tightened SEO copy ([#256](https://github.com/polomarcus/common-trails/issues/256)) ([fd97aee](https://github.com/polomarcus/common-trails/commit/fd97aeed68e6aa9ab9f1faa5143d26401fc555d8))
* **make:** import-osm-roads-local must run detached (no SIGHUP on disconnect) ([#240](https://github.com/polomarcus/common-trails/issues/240)) ([f70ab2c](https://github.com/polomarcus/common-trails/commit/f70ab2c00aea8102b54157146898d5676d3b92a6))
* **pmtiles:** --keep-grid-fallback also relaxes grid_fallback_min_uc ([#238](https://github.com/polomarcus/common-trails/issues/238)) ([cd0046c](https://github.com/polomarcus/common-trails/commit/cd0046ceb0a910d6c6ab4398ec06692359b1617b))
* scaling + offroad-visibility hardening ([#233](https://github.com/polomarcus/common-trails/issues/233)) ([a0e83cc](https://github.com/polomarcus/common-trails/commit/a0e83cc45fe331a3b6fdf3d1701b55deb9c47f48))


### Miscellaneous

* deep agent docs + cascade cap + drift test + dedup test + beta gate ([#229](https://github.com/polomarcus/common-trails/issues/229)) ([7116bfc](https://github.com/polomarcus/common-trails/commit/7116bfcb99f4d51c61cd8d494e7ddcb9006a0b4f))
* **make:** add heatmap-clean-rebuild target ([#239](https://github.com/polomarcus/common-trails/issues/239)) ([dc05068](https://github.com/polomarcus/common-trails/commit/dc050683f34bdbe36d956132eb32ad48f85f8d63))
* post-friends-test polish — i18n, e2e, mobile audit ([#231](https://github.com/polomarcus/common-trails/issues/231)) ([9da2dc8](https://github.com/polomarcus/common-trails/commit/9da2dc856039e0b9f8ae4409598256c379172754))

## [0.7.109](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.108...common-trails-v0.7.109) (2026-05-03)


### Features

* **prod:** make prod-import-strava (local export → prod via cloud-sql-proxy) ([#225](https://github.com/polomarcus/common-trails/issues/225)) ([9501ed7](https://github.com/polomarcus/common-trails/commit/9501ed730621fb1f45d6333164ea0a9903ccb2fe))

## [0.7.108](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.107...common-trails-v0.7.108) (2026-05-03)


### Bug Fixes

* **api:** add /health alias for /healthz (Cloud Run edge 404 workaround) ([2bb3f53](https://github.com/polomarcus/common-trails/commit/2bb3f53b51f00f53ebf8eef52c20545b6b287f85))

## [0.7.107](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.106...common-trails-v0.7.107) (2026-05-03)


### Features

* **prod:** South France OSM import via local + cloud-sql-proxy ([ae2689e](https://github.com/polomarcus/common-trails/commit/ae2689e855d4876293b9c561d83f4a74c58e80de))

## [0.7.106](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.105...common-trails-v0.7.106) (2026-05-03)


### Bug Fixes

* **make:** gcp-precheck also verifies ADC, not just gcloud config ([eaacae1](https://github.com/polomarcus/common-trails/commit/eaacae1112d34b884e5c4ebcc509b9f40f33a36c))

## [0.7.105](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.104...common-trails-v0.7.105) (2026-05-03)


### Features

* **heatmap+prod:** Komoot quality + Valhalla map-matching + event-driven artefact refresh ([d269d6b](https://github.com/polomarcus/common-trails/commit/d269d6b247b036d802c872fc25483cb8db54972a))

## [0.7.104](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.103...common-trails-v0.7.104) (2026-05-02)


### Features

* **ingest:** async gpx-upload via Cloud Tasks queue ([2ea8948](https://github.com/polomarcus/common-trails/commit/2ea8948c84f3cca415b1f45aebbc8a78ce676602))

## [0.7.103](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.102...common-trails-v0.7.103) (2026-05-02)


### Features

* **activities:** add PostGIS geometry column ([5bebeca](https://github.com/polomarcus/common-trails/commit/5bebeca9756416244196f6d5f37ee1db9a449d8d))
* **activities:** add PostGIS geometry column (binary, smaller, queryable) ([67b3ee2](https://github.com/polomarcus/common-trails/commit/67b3ee2db19b30acbbf84dc52500f8533b49d041))

## [0.7.102](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.101...common-trails-v0.7.102) (2026-05-02)


### Bug Fixes

* ingestion improvements (densifier breaks, sport-aware OSM radius, test fixes) ([c35fb81](https://github.com/polomarcus/common-trails/commit/c35fb8131fc869c866d68d93b5a41fc326d5e4f8))
* **ingest:** project OSM-matched GPS points onto the OSM line ([2e9a665](https://github.com/polomarcus/common-trails/commit/2e9a665f0e084c13e478a12b474f239c13acc91c))
* **lint:** ruff cleanup — unused vars + sorted imports ([bc1d0ca](https://github.com/polomarcus/common-trails/commit/bc1d0ca0f298f27f100db15301c013480d1eafd8))
* pending bugs (regional.pb 500, heatmap spaghetti, proposal diversity) + routing audit ([9050836](https://github.com/polomarcus/common-trails/commit/9050836d804ce74e952d3791fb2cdb351f311218))
* pending bugs (regional.pb, spaghetti, proposal diversity) + routing audit + drop TS routing fallback ([60ba9f9](https://github.com/polomarcus/common-trails/commit/60ba9f9c24506a95d0e65170d3c6c0a03bbeafbd))
* spaghetti at live endpoints, offroad partition, drift guard, audit closure ([b4cdf32](https://github.com/polomarcus/common-trails/commit/b4cdf32e9a3e3f6601ed94f42addc19857dc8066))


### Miscellaneous

* bind-mount heatmap-display.pmtiles into frontend container ([74fdcb8](https://github.com/polomarcus/common-trails/commit/74fdcb8cf0daceed65cc5eedecfe2bc013b4bc5c))
* untrack wasm-router/target/ and frontend/tsconfig.tsbuildinfo ([c755604](https://github.com/polomarcus/common-trails/commit/c755604f46ad92f7a559a4a9ccc9d0deae512d57))

## [0.7.101](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.100...common-trails-v0.7.101) (2026-05-01)


### Bug Fixes

* **ingest:** heat_edges sport partition normalization ([90598df](https://github.com/polomarcus/common-trails/commit/90598dfee27e4d6c3cc6e0038731e2cc81db5d27))
* **ingest:** normalize heat_edges sport to real partitions (offroad→gravel) ([573d0eb](https://github.com/polomarcus/common-trails/commit/573d0eb3a23b3d6329570fdb60abc91bd7d17872))

## [0.7.100](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.99...common-trails-v0.7.100) (2026-05-01)


### Bug Fixes

* **routing:** EXTENDED_SNAP_M 3km → 10km (drag-modify straight lines) ([f644eab](https://github.com/polomarcus/common-trails/commit/f644eab43b36794163ec8e5479d8baec32dba9ea))
* **routing:** EXTENDED_SNAP_M 3km→10km + regression guard ([b53ff8e](https://github.com/polomarcus/common-trails/commit/b53ff8ea683a62647db91b9553390c48abc79b13))

## [0.7.99](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.98...common-trails-v0.7.99) (2026-05-01)


### Bug Fixes

* **routing:** passive heartbeat — don't fall back when Worker is busy ([3f81f67](https://github.com/polomarcus/common-trails/commit/3f81f6735bceffb642e4cf434e812a0b9652fae3))
* **routing:** passive heartbeat — fixes 'Worker unresponsive' cascade ([f52c0b4](https://github.com/polomarcus/common-trails/commit/f52c0b453fb7ebff2ba778b798539f12414e67f1))

## [0.7.98](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.97...common-trails-v0.7.98) (2026-05-01)


### Bug Fixes

* **routing:** rename routeCoords → clientRoute (drag-modify broken) ([c96d6d8](https://github.com/polomarcus/common-trails/commit/c96d6d86af21abd1e6211d706359c24d7ac11d44))
* **routing:** wasmRouter.routeCoords → clientRoute (broke drag-modify) ([c08853f](https://github.com/polomarcus/common-trails/commit/c08853ffba038507be1241fb155bab1582a44945))

## [0.7.97](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.96...common-trails-v0.7.97) (2026-05-01)


### Bug Fixes

* **db:** bump shm_size to 1GB — fixes 'Chargement' stuck banner ([473c452](https://github.com/polomarcus/common-trails/commit/473c45202b0d937a5834a05421dda91f52ec4a50))
* **db:** bump shm_size to 1GB to prevent DiskFull on parallel queries ([e6213b7](https://github.com/polomarcus/common-trails/commit/e6213b7d3fa37570738b1075bb7bc40753fe618e))

## [0.7.96](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.95...common-trails-v0.7.96) (2026-05-01)


### Bug Fixes

* **backend:** /readyz 22s → 109ms (banner stuck forever) ([10215b0](https://github.com/polomarcus/common-trails/commit/10215b0b1acbe85b71eedb144e168475145bb0f8))
* **backend:** /readyz 22s → 109ms (was blocking banner forever) ([1d4ea0c](https://github.com/polomarcus/common-trails/commit/1d4ea0c74c4c79a6799cfe1fb3379134a80dcea1))

## [0.7.95](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.94...common-trails-v0.7.95) (2026-05-01)


### Bug Fixes

* **routing:** coerce wasmRouter.snapCoordFull result — unblocks motto test ([1dfd2d8](https://github.com/polomarcus/common-trails/commit/1dfd2d824376b7f0e41fa44ed5add36baea0433b))
* **routing:** coerce wasmRouter.snapCoordFull result to plain array ([f248aa3](https://github.com/polomarcus/common-trails/commit/f248aa3d9e47e03d608c8184d63c528b546ba352))

## [0.7.94](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.93...common-trails-v0.7.94) (2026-04-15)


### Bug Fixes

* **lint:** unused import + context manager in test_gpx_backup ([5551ec4](https://github.com/polomarcus/common-trails/commit/5551ec437da2b71379259f6a9d0ad3387677bfb5))

## [0.7.93](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.92...common-trails-v0.7.93) (2026-04-15)


### Features

* bulk GPX folder import CLI + Mac→prod ops docs ([f76d477](https://github.com/polomarcus/common-trails/commit/f76d4779b8f54db626a084fea089c671b8b94515))
* **cli:** bulk GPX folder import CLI ([3eaad9b](https://github.com/polomarcus/common-trails/commit/3eaad9b97d9432988b7365d3f03cf0e7d08660c3))

## [0.7.92](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.91...common-trails-v0.7.92) (2026-04-14)


### Features

* **map:** ETA in hover tooltip + a11y + snap telemetry + 35 more unit tests ([3775345](https://github.com/polomarcus/common-trails/commit/37753453e2f63fcd5274dae76f912354322ab571))
* PMTiles heatmap display — single static file, CDN-served ([9c88975](https://github.com/polomarcus/common-trails/commit/9c88975872dac8fa3445e1f1362912b2bdd710e9))


### Bug Fixes

* **backend:** MVT tile OOM + DFCI endpoint safety limits ([6da6c36](https://github.com/polomarcus/common-trails/commit/6da6c36cfb15167246236a7948dad5eaf1214ffd))
* **ci:** lint fixes + skip 7 pre-existing failing tests + sync lockfile ([3755c5f](https://github.com/polomarcus/common-trails/commit/3755c5f3c780e90ddee603a38da53e858f7301bb))
* **ci:** retry alembic on DB connection race + sync lockfile ([daf3653](https://github.com/polomarcus/common-trails/commit/daf3653230472615a265cb795bfc859581223d57))
* **ci:** upgrade Node.js 20 → 22 (fixes Playwright stdout OOM) ([e7623a4](https://github.com/polomarcus/common-trails/commit/e7623a40d806e2ecb72db00f1b1bb1c5e71db6ac))
* **map:** toast queue + unit tests for pure lib functions ([393566d](https://github.com/polomarcus/common-trails/commit/393566d4e078cd710e9ae2b39df00249ea8ffca1))

## [0.7.91](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.90...common-trails-v0.7.91) (2026-04-09)


### Features

* archive raw GPX uploads to bucket (heatmap rebuild safety net) ([7e9eadb](https://github.com/polomarcus/common-trails/commit/7e9eadbb5eb61bb49889aef210403444cbd1d245))
* archive raw GPX uploads to GCS bucket for heatmap rebuild safety net ([d323502](https://github.com/polomarcus/common-trails/commit/d323502c9a1affbb751fa2d4d8718d76ae7ac116))


### Miscellaneous

* extend uploads bucket lifecycle from 90 days to 2 years ([d971e20](https://github.com/polomarcus/common-trails/commit/d971e20a175fc9b6a4724a3ad73d01940281a83e))

## [0.7.90](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.89...common-trails-v0.7.90) (2026-04-01)


### Features

* OSM road network as routing base layer ([#177](https://github.com/polomarcus/common-trails/issues/177)) ([214dcd6](https://github.com/polomarcus/common-trails/commit/214dcd62d140161d00eaf051d4af095bd2debcbf))

## [0.7.89](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.88...common-trails-v0.7.89) (2026-03-31)


### Features

* i8n + CI lint + broken import test after .fit support ([#175](https://github.com/polomarcus/common-trails/issues/175)) ([dacc29c](https://github.com/polomarcus/common-trails/commit/dacc29cf52d8272afec84eae5d23df133cd99488))

## [0.7.88](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.87...common-trails-v0.7.88) (2026-03-30)


### Bug Fixes

* search placeholder "Rechercher une ville" + keep prod tile URL fallback ([5220e7c](https://github.com/polomarcus/common-trails/commit/5220e7c6dbd1fa2645e266a8e7e7f0a1f6160225))

## [0.7.87](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.86...common-trails-v0.7.87) (2026-03-30)


### Bug Fixes

* bottom-left overlays padded above beta banner ([2be6a2a](https://github.com/polomarcus/common-trails/commit/2be6a2a1c3340ad0d866d390a8a66665cfaef5dc))

## [0.7.86](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.85...common-trails-v0.7.86) (2026-03-30)


### Bug Fixes

* rename "Voyage" to "Collection" on map route view ([fe99bae](https://github.com/polomarcus/common-trails/commit/fe99baec49b6d4b1d2bb9091819f4208e7ae78d5))

## [0.7.85](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.84...common-trails-v0.7.85) (2026-03-30)


### Bug Fixes

* compare routes — map no longer freezes + routes are clickable ([3a3be68](https://github.com/polomarcus/common-trails/commit/3a3be686bb4ed647ecfc9ef954ba012cf585e092))

## [0.7.84](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.83...common-trails-v0.7.84) (2026-03-30)


### Bug Fixes

* trace tooltip shows date + elevation, click selects on map ([da6ab59](https://github.com/polomarcus/common-trails/commit/da6ab59790f51a65cb19229826c27891e326e114))

## [0.7.83](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.82...common-trails-v0.7.83) (2026-03-30)


### Features

* heatmap ingestion feedback after GPX import ([2709878](https://github.com/polomarcus/common-trails/commit/27098782f0343b60ceea409dafde44255b906bba))

## [0.7.82](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.81...common-trails-v0.7.82) (2026-03-30)


### Features

* heatmap glow polish + Garmin .fit import + multi-source support ([474bd91](https://github.com/polomarcus/common-trails/commit/474bd913fadd00877ccf1d6484345b722aad3749))

## [0.7.81](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.80...common-trails-v0.7.81) (2026-03-30)


### Features

* drag & drop GPX zone + sport pills + styled Strava warning modal ([a472c81](https://github.com/polomarcus/common-trails/commit/a472c81c0687cad122315dfa5ea50265430c8d09))

## [0.7.80](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.79...common-trails-v0.7.80) (2026-03-30)


### Bug Fixes

* robust cross-provider dedup + GPX date extraction + 5 tests ([5da9b8f](https://github.com/polomarcus/common-trails/commit/5da9b8f0a27193d81f123c7a7e68c7334221c3bc))

## [0.7.79](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.78...common-trails-v0.7.79) (2026-03-30)


### Features

* cross-provider dedup — same date + distance (±10%) = skip ([4d8a8e0](https://github.com/polomarcus/common-trails/commit/4d8a8e0c158517d5bfbd895a9523bc60eef7a3d2))

## [0.7.78](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.77...common-trails-v0.7.78) (2026-03-30)


### Features

* inline GPX upload on Strava page — no need to go to /map ([31b9d3d](https://github.com/polomarcus/common-trails/commit/31b9d3d19aad4ba1847772fe8cdb050b349d6e9f))

## [0.7.77](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.76...common-trails-v0.7.77) (2026-03-30)


### Bug Fixes

* clarify Strava export steps — reassure it doesn't delete account ([c498968](https://github.com/polomarcus/common-trails/commit/c498968505691a0f467418544a5ae5bfd2cca6e1))

## [0.7.76](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.75...common-trails-v0.7.76) (2026-03-30)


### Features

* GPX import as primary CTA, Strava import as secondary with confirm ([9a62275](https://github.com/polomarcus/common-trails/commit/9a62275a077f1a35a05a65f50b8a65b47fd3f090))

## [0.7.75](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.74...common-trails-v0.7.75) (2026-03-30)


### Features

* import queue — one Strava import at a time (rate limit protection) ([9da7816](https://github.com/polomarcus/common-trails/commit/9da7816e698392e7db20e136a407f858f32bb149))

## [0.7.74](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.73...common-trails-v0.7.74) (2026-03-30)


### Features

* show GPX bulk export tip before Strava import starts ([6bd4bc1](https://github.com/polomarcus/common-trails/commit/6bd4bc1b1653f6af8bede306bd5dfa51cf77e565))

## [0.7.73](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.72...common-trails-v0.7.73) (2026-03-30)


### Features

* suggest GPX bulk export when Strava import is slow ([85b118d](https://github.com/polomarcus/common-trails/commit/85b118d7bc93e196340ba48237f361140e1cb9bf))


### Bug Fixes

* add safety log showing existing DB size before tile-based delete ([e242fbb](https://github.com/polomarcus/common-trails/commit/e242fbb7ae76268f1495002047094310dbb931b9))

## [0.7.72](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.71...common-trails-v0.7.72) (2026-03-29)


### Bug Fixes

* tile-based delete in OSM import (don't wipe all regions) ([8270416](https://github.com/polomarcus/common-trails/commit/82704168331ab84d270d96cb54de940e7b8980ee))

## [0.7.71](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.70...common-trails-v0.7.71) (2026-03-29)


### Features

* add all French regions + Switzerland/Spain/Italy to OSM PBF import ([a13fac2](https://github.com/polomarcus/common-trails/commit/a13fac22a12249bba37fc9b5f12da3cf3f1e6fb3))

## [0.7.70](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.69...common-trails-v0.7.70) (2026-03-29)


### Bug Fixes

* MVT tile URL must be absolute for MapLibre + handle sport=all ([#155](https://github.com/polomarcus/common-trails/issues/155)) ([bf599a7](https://github.com/polomarcus/common-trails/commit/bf599a708dd1c224b000a715e63650afd58cd033))

## [0.7.69](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.68...common-trails-v0.7.69) (2026-03-29)


### Features

* stuck import detection + map link during Strava import ([#153](https://github.com/polomarcus/common-trails/issues/153)) ([9bc0ccd](https://github.com/polomarcus/common-trails/commit/9bc0ccd9e0f1e9b9953b11ffcc2eba60c9bea31a))

## [0.7.68](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.67...common-trails-v0.7.68) (2026-03-29)


### Bug Fixes

* heatmap hardening — idempotent migration, pruning, error handling ([#149](https://github.com/polomarcus/common-trails/issues/149)) ([5a6bf7a](https://github.com/polomarcus/common-trails/commit/5a6bf7acb7db01303ece5479db32c3c114106feb))

## [0.7.67](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.66...common-trails-v0.7.67) (2026-03-29)


### Features

* MVT vector tiles + partition heat_edges + incremental rebuild ([#147](https://github.com/polomarcus/common-trails/issues/147)) ([284389c](https://github.com/polomarcus/common-trails/commit/284389c84f7f3e1947d7257ea82981e2b2dd7e69))

## [0.7.66](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.65...common-trails-v0.7.66) (2026-03-29)


### Bug Fixes

* frontend-only routing — bridge cost ×3, undo preserves metadata, free mode off-heatmap ([5950bdf](https://github.com/polomarcus/common-trails/commit/5950bdf11a74e7347fbe10a7e7892c9ad89826bb))
* ruff lint fixes + protobuf CDN serving ([699781c](https://github.com/polomarcus/common-trails/commit/699781c0f39db39831818b17ceffc6dfcb8c7bee))
* ruff lint fixes for CDN write-through + protobuf ([7479e1b](https://github.com/polomarcus/common-trails/commit/7479e1b55e2e63ff4949687806414351f52eba10))
* skip graph quality E2E in CI + increase Node heap to 4GB ([699927e](https://github.com/polomarcus/common-trails/commit/699927ed25868d5f42418395220a3ca54fd9d5ac))
* TS strict mode graph_pb access + update cache-control test assertion ([4c0ad2f](https://github.com/polomarcus/common-trails/commit/4c0ad2fb4936168d09f8dfe093090c1d42e30bd4))

## [0.7.65](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.64...common-trails-v0.7.65) (2026-03-29)


### Features

* serve CTGB protobuf from CDN — 44% smaller than JSON ([2d5e6f7](https://github.com/polomarcus/common-trails/commit/2d5e6f748165ac012c3deb68123942f13c321ceb))

## [0.7.64](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.63...common-trails-v0.7.64) (2026-03-29)


### Bug Fixes

* 6 code review issues — constant dedup, service imports, test speed ([be062d0](https://github.com/polomarcus/common-trails/commit/be062d06cb20b46cbce6887ac65b54ba1d47d8fe))

## [0.7.63](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.62...common-trails-v0.7.63) (2026-03-29)


### Features

* local filesystem mode for cache writer + 6 tests ([fdf70e5](https://github.com/polomarcus/common-trails/commit/fdf70e5bffedeff7280dbf54a902fd1b3e2dd767))

## [0.7.62](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.61...common-trails-v0.7.62) (2026-03-29)


### Features

* CDN write-through cache implementation ([6774dc5](https://github.com/polomarcus/common-trails/commit/6774dc512ad26349b2fa95b85350f5aab5b49f4a))

## [0.7.61](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.60...common-trails-v0.7.61) (2026-03-28)


### Features

* collections page overhaul — rename, GT seed, POI placement ([565aef5](https://github.com/polomarcus/common-trails/commit/565aef58cb147fe0ea47b94e55a585028e204636))

## [0.7.60](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.59...common-trails-v0.7.60) (2026-03-28)


### Features

* Cloud Run Job for heatmap rebuild ([b0b52ba](https://github.com/polomarcus/common-trails/commit/b0b52bae95c6fa5e0fdf25276944e88aed062d91))

## [0.7.59](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.58...common-trails-v0.7.59) (2026-03-28)


### Bug Fixes

* eliminate heatmap micro-gaps from OSM edge snapping ([b70090c](https://github.com/polomarcus/common-trails/commit/b70090c31bbcc118dd0cdde67b74207f9d2ad49a))
* pass CI env var to Docker container to skip perf tests ([1ee1434](https://github.com/polomarcus/common-trails/commit/1ee14342cc7a827bba3570a2aa729cdf01dd6e92))
* relax E2E thresholds for GPS-point-based edges ([abf9f53](https://github.com/polomarcus/common-trails/commit/abf9f53a765ddf0c82400da55a49beed1acd48a0))
* ruff I001 — alphabetize imports in test_ingestion_perf ([f39b02c](https://github.com/polomarcus/common-trails/commit/f39b02cbf2d74aa5e42745ed83dad6954869acc2))
* ruff lint — remove unused import, fix sort order ([42b413e](https://github.com/polomarcus/common-trails/commit/42b413e614988fe2f0312eda97bf007d4c03a7f5))
* skip perf tests in CI — too slow for GitHub Actions runners ([eb42b5f](https://github.com/polomarcus/common-trails/commit/eb42b5fb4529cccd2a63856a81a03019d00f596a))
* use GPS-point edges instead of OSM-node edges to eliminate gaps ([d536b56](https://github.com/polomarcus/common-trails/commit/d536b56a3559efa774d0f6d87e3a6306fb27e48a))


### Miscellaneous

* simplify OSM edge code — remove dead matched_segs_in_run loop ([b8e6b72](https://github.com/polomarcus/common-trails/commit/b8e6b72a8af6b46db982068c3aad741af2c94b72))

## [0.7.58](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.57...common-trails-v0.7.58) (2026-03-28)


### Bug Fixes

* use env var instead of secrets in step-level if condition ([35b4e94](https://github.com/polomarcus/common-trails/commit/35b4e940cb442ad07e3d8f00c63265dc6d8a508a))

## [0.7.57](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.56...common-trails-v0.7.57) (2026-03-28)


### Bug Fixes

* wrap secrets reference in ${{ }} expression in deploy workflow ([32ff226](https://github.com/polomarcus/common-trails/commit/32ff2268ff09ff3f59ce46a431558139d0679639))

## [0.7.56](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.55...common-trails-v0.7.56) (2026-03-28)


### Features

* parallel heatmap rebuild with deadlock retry ([eb9e640](https://github.com/polomarcus/common-trails/commit/eb9e6404d5bde08f8b1fc4600795d1dfd51b8faa))

## [0.7.55](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.54...common-trails-v0.7.55) (2026-03-28)


### Bug Fixes

* make Terraform step non-blocking in deploy workflow ([797586c](https://github.com/polomarcus/common-trails/commit/797586c1652a9daa10816fab50d92922a2f85877))

## [0.7.54](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.53...common-trails-v0.7.54) (2026-03-28)


### Features

* add Montpellier-Anduze OSM PBF fixture (Git LFS) ([#126](https://github.com/polomarcus/common-trails/issues/126)) ([2fb1e66](https://github.com/polomarcus/common-trails/commit/2fb1e6697095e71fa82d13a2bb97c0fa0e890d6d))
* run GPS upgrade in Cloud Run Job (fix stuck 0/1394) ([7282c84](https://github.com/polomarcus/common-trails/commit/7282c84c8fe9037b6e70b6c7bf074f08f3a01863))
* run GPS upgrade in Cloud Run Job (not API background task) ([d2ae1d7](https://github.com/polomarcus/common-trails/commit/d2ae1d746e3ff07e2b48705a35e8edae3d1c7765))
* viewport-based binary graph loading (area.pb) — 44% smaller ([7c56779](https://github.com/polomarcus/common-trails/commit/7c56779978a4f01ac8ba6056810672856892d5cb))


### Bug Fixes

* deterministic user_id hash + SVG label clipping on landing page ([80df372](https://github.com/polomarcus/common-trails/commit/80df372af847f16be8694dcb6c11318821b2a7ff))
* make routing quality tests CI-safe with dynamic test points ([f5e1fb9](https://github.com/polomarcus/common-trails/commit/f5e1fb9e623f365f0041fcdfd0ae0904169f9575))
* refresh Strava token before import (prevent expired token errors) ([8b62fd0](https://github.com/polomarcus/common-trails/commit/8b62fd028d58705537f85eaf494ebbc9ae5c7502))
* remove CONCURRENTLY from index migration (incompatible with txn) ([2454aef](https://github.com/polomarcus/common-trails/commit/2454aef1fc79d703de50692bcd3c6600b80dfdb8))
* ruff lint — remove unused import and variable in fix_contributor_hashes ([ea765bd](https://github.com/polomarcus/common-trails/commit/ea765bdc5c3cf155e4b6b6ee5d4fa900128c80a7))
* ruff lint — remove unused import, fix import ordering ([73dc823](https://github.com/polomarcus/common-trails/commit/73dc82351171c884d68c18f3978b47c137da982f))
* tolerate identical proposals paths in sparse CI graph ([bbb0bb6](https://github.com/polomarcus/common-trails/commit/bbb0bb62fda30b4c4cbd221b59b096e93bd5ff31))
* use correct z14 tile keys in OSM matching tests ([de4b61d](https://github.com/polomarcus/common-trails/commit/de4b61d8f056044a068e64e0e3cf348f0b1fdcc2))
* use offroad profile for frontend routing quality tests ([dba641c](https://github.com/polomarcus/common-trails/commit/dba641cba7c1b43a607645157e464ef43110af05))


### Miscellaneous

* keep only essential E2E tests — 61 tests in 19s ([d9940bd](https://github.com/polomarcus/common-trails/commit/d9940bd136cecb6a06c2e7b314e18af188424a09))
* release 0.7.53 ([#125](https://github.com/polomarcus/common-trails/issues/125)) ([1991168](https://github.com/polomarcus/common-trails/commit/19911686878efabfb645f7ac0692786cd3666045))
* remove flaky/slow E2E tests that don't bring value ([5684033](https://github.com/polomarcus/common-trails/commit/568403309b09f14ece321ecf76d641d2242792ae))
* remove superseded E2E routing tests ([b957786](https://github.com/polomarcus/common-trails/commit/b957786c13d6739be38563a4fcb7d4fc2f02a1ce))

## [0.7.53](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.52...common-trails-v0.7.53) (2026-03-25)


### Bug Fixes

* resolve ruff lint errors breaking CI ([#123](https://github.com/polomarcus/common-trails/issues/123)) ([507b504](https://github.com/polomarcus/common-trails/commit/507b504b419400824a09bcbd186dfabf493f1926))

## [0.7.52](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.51...common-trails-v0.7.52) (2026-03-25)


### Bug Fixes

* collection page overlapping beta banner ([#120](https://github.com/polomarcus/common-trails/issues/120)) ([335e5e1](https://github.com/polomarcus/common-trails/commit/335e5e10a1dd758d4caaae29527bb851914d2a86))

## [0.7.51](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.50...common-trails-v0.7.51) (2026-03-25)


### Bug Fixes

* restore missing model fields and fix lint errors breaking CI ([#118](https://github.com/polomarcus/common-trails/issues/118)) ([cde23ab](https://github.com/polomarcus/common-trails/commit/cde23ab1558a57f40f0026a313b0f2b19c44baac))

## [0.7.50](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.49...common-trails-v0.7.50) (2026-03-24)


### Bug Fixes

* add skip_heat_computation flag instead of contribute_heatmap=False ([d190144](https://github.com/polomarcus/common-trails/commit/d1901446e455735f7c5bf307ebc973134bcf681a))

## [0.7.49](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.48...common-trails-v0.7.49) (2026-03-24)


### Bug Fixes

* add missing RouteCollection and RouteCollectionItem models ([464f751](https://github.com/polomarcus/common-trails/commit/464f7519b9eb11e62e4f3b0d866dea2039109124))

## [0.7.48](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.47...common-trails-v0.7.48) (2026-03-24)


### Features

* resume import progress on Strava page revisit ([d81759b](https://github.com/polomarcus/common-trails/commit/d81759b41fd3f6f5e2ea89d4aa0731ae3b36a14c))

## [0.7.47](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.46...common-trails-v0.7.47) (2026-03-24)


### Bug Fixes

* prevent duplicate Strava imports for same user ([c032d58](https://github.com/polomarcus/common-trails/commit/c032d58bcfee5b20f31f5495be05c2cce9a12518))

## [0.7.46](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.45...common-trails-v0.7.46) (2026-03-24)


### Bug Fixes

* skip Overpass OSM tile fetching during bulk import ([11fec32](https://github.com/polomarcus/common-trails/commit/11fec321f8ef1046ee5e13d23648d9438ff31a41))

## [0.7.45](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.44...common-trails-v0.7.45) (2026-03-24)


### Bug Fixes

* expose startup progress in production for wakeup banner ([a5b464e](https://github.com/polomarcus/common-trails/commit/a5b464e3fc763e0fb05ff35c6ea1d4f0b642252e))
* prevent DFCI edge accumulation across deploys ([48be634](https://github.com/polomarcus/common-trails/commit/48be63448e8249a974e0b5dd7e4e02b550d3ea2e))

## [0.7.44](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.43...common-trails-v0.7.44) (2026-03-24)


### Bug Fixes

* Strava API rate limit handling ([#110](https://github.com/polomarcus/common-trails/issues/110)) ([82a801f](https://github.com/polomarcus/common-trails/commit/82a801f158e3fffe6ed93688a144c399c97f7df0))

## [0.7.43](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.42...common-trails-v0.7.43) (2026-03-24)


### Features

* heatmap edge clustering — merge near-duplicate GPS edges ([#108](https://github.com/polomarcus/common-trails/issues/108)) ([50e96e0](https://github.com/polomarcus/common-trails/commit/50e96e05a603df70058fab1d851768ddaab3aba5))

## [0.7.42](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.41...common-trails-v0.7.42) (2026-03-22)


### Bug Fixes

* pass required Terraform variables in CI deploy ([37888a8](https://github.com/polomarcus/common-trails/commit/37888a89da5988c0847628b9f9fbc2b6f2184af6))

## [0.7.41](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.40...common-trails-v0.7.41) (2026-03-22)


### Bug Fixes

* DATA_DIR=/app/data in Terraform — GT routes + DFCI missing ([#105](https://github.com/polomarcus/common-trails/issues/105)) ([75ada47](https://github.com/polomarcus/common-trails/commit/75ada47e85e4819f974f1153856462faa5c987a8))
* increase startup probe timeout to 10min for DFCI+GT import ([#106](https://github.com/polomarcus/common-trails/issues/106)) ([7989a0c](https://github.com/polomarcus/common-trails/commit/7989a0ce5cb7d021478c54f64e13b4dcaa5f8512))
* NameError on TEST_MODE crashes background loading in prod ([#103](https://github.com/polomarcus/common-trails/issues/103)) ([319a4a0](https://github.com/polomarcus/common-trails/commit/319a4a0620b9f0e65dd68b0df5c994b7f5f8b73b))

## [0.7.40](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.39...common-trails-v0.7.40) (2026-03-22)


### Features

* /healthz returns release-please version + fix Docker cache ([#101](https://github.com/polomarcus/common-trails/issues/101)) ([36d10c0](https://github.com/polomarcus/common-trails/commit/36d10c00920e957310f299faca6ee3e414554c97))

## [0.7.39](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.38...common-trails-v0.7.39) (2026-03-22)


### Bug Fixes

* include DFCI IGN cache in /data/ for production import ([9534add](https://github.com/polomarcus/common-trails/commit/9534addefa0f8f80e0595729a186e88240f63f18))
* show email in account menu, disable prod seeds, rename to Chemins communs ([#99](https://github.com/polomarcus/common-trails/issues/99)) ([7c69a45](https://github.com/polomarcus/common-trails/commit/7c69a45ad4750ca54dc9b763dc003d241dce0cc6))
* store OAuth state in DB instead of in-memory (prevents ghost users) ([#98](https://github.com/polomarcus/common-trails/issues/98)) ([46e96cc](https://github.com/polomarcus/common-trails/commit/46e96cc6214be5599302e9e8e995f317882dfb0a))
* use DATA_DIR=/app/data instead of redundant COPY data/ /data/ ([#100](https://github.com/polomarcus/common-trails/issues/100)) ([a8172e2](https://github.com/polomarcus/common-trails/commit/a8172e22013aa3d169eeece2a2695a50a7656e15))

## [0.7.38](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.37...common-trails-v0.7.38) (2026-03-22)


### Bug Fixes

* prevent Strava ghost users on connect + auto-migrate duplicates ([097ba6c](https://github.com/polomarcus/common-trails/commit/097ba6cd2e229be753b9933a9a80655b53a9d950))

## [0.7.37](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.36...common-trails-v0.7.37) (2026-03-22)


### Bug Fixes

* add /routes to frontend HTML middleware, cover all frontend pages ([f4a5388](https://github.com/polomarcus/common-trails/commit/f4a53881d22b50d5ab8e589a0f4ef9b7827a16b8))

## [0.7.36](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.35...common-trails-v0.7.36) (2026-03-22)


### Bug Fixes

* serve frontend HTML for /trips and other pages in production ([91429a5](https://github.com/polomarcus/common-trails/commit/91429a5fe2125250aff6c15b5fbde6f2b7daa309))

## [0.7.35](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.34...common-trails-v0.7.35) (2026-03-22)


### Bug Fixes

* reduce DFCI routing dominance + move-waypoint regression ([#91](https://github.com/polomarcus/common-trails/issues/91)) ([7931237](https://github.com/polomarcus/common-trails/commit/79312378a6503fd8d431e95cb781b441ca121bdb))

## [0.7.34](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.33...common-trails-v0.7.34) (2026-03-21)


### Features

* public routes panel on map + discover viewport filtering ([#89](https://github.com/polomarcus/common-trails/issues/89)) ([57342d8](https://github.com/polomarcus/common-trails/commit/57342d876b7fb36ba8ef834ebdab33847a05750d))

## [0.7.33](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.32...common-trails-v0.7.33) (2026-03-21)


### Bug Fixes

* remove backend routing E2E tests, fix math shadow bug ([#86](https://github.com/polomarcus/common-trails/issues/86)) ([f5665be](https://github.com/polomarcus/common-trails/commit/f5665beee98ecfb0b18246b75a09392f604a56ad))

## [0.7.32](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.31...common-trails-v0.7.32) (2026-03-21)


### Bug Fixes

* **strava:** redesign import page + ETA + fix jokes ([#85](https://github.com/polomarcus/common-trails/issues/85)) ([5430828](https://github.com/polomarcus/common-trails/commit/54308285d8039658ef2b21b1cc09b7a7e59b3be6))

## [0.7.31](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.30...common-trails-v0.7.31) (2026-03-21)


### Bug Fixes

* logout cookie deletion + mobile landing page ([#83](https://github.com/polomarcus/common-trails/issues/83)) ([ff91be6](https://github.com/polomarcus/common-trails/commit/ff91be6d6acb3d27871283cb66821dbfff6b798a))

## [0.7.30](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.29...common-trails-v0.7.30) (2026-03-21)


### Bug Fixes

* E2E auth, Strava import progress, mobile landing, Cloud Run Job DB ([#81](https://github.com/polomarcus/common-trails/issues/81)) ([4e9b4e4](https://github.com/polomarcus/common-trails/commit/4e9b4e4d5605ae57ae5b2d0fce8b6e70081d9714))

## [0.7.29](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.28...common-trails-v0.7.29) (2026-03-20)


### Features

* serve frontend from Cloud Run + validation fix + trail cache ([#79](https://github.com/polomarcus/common-trails/issues/79)) ([0ace3bc](https://github.com/polomarcus/common-trails/commit/0ace3bc8c4e6e412c3e87ba67f061b6280e2052f))

## [0.7.28](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.27...common-trails-v0.7.28) (2026-03-20)


### Features

* surface, elevation, routing fixes & route management UX overhaul ([#77](https://github.com/polomarcus/common-trails/issues/77)) ([dea225f](https://github.com/polomarcus/common-trails/commit/dea225f97930a82b1770368219b6c9eff8e22ff2))

## [0.7.27](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.26...common-trails-v0.7.27) (2026-03-17)


### Features

* French cyclist terminology for GitHub-like features ([#75](https://github.com/polomarcus/common-trails/issues/75)) ([243c31c](https://github.com/polomarcus/common-trails/commit/243c31c005f73a1a575d68ca15fd46bf4902a8cd))

## [0.7.26](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.25...common-trails-v0.7.26) (2026-03-17)


### Features

* colorblind-safe sport colors + DFCI slope exemption ([#73](https://github.com/polomarcus/common-trails/issues/73)) ([9ab669f](https://github.com/polomarcus/common-trails/commit/9ab669ff0983a708d49d67fc62def93ca36ea7ff))

## [0.7.25](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.24...common-trails-v0.7.25) (2026-03-17)


### Features

* simplified layers panel + color-blind accessible heatmap & elev… ([#71](https://github.com/polomarcus/common-trails/issues/71)) ([c02907c](https://github.com/polomarcus/common-trails/commit/c02907c61b2df24567b5c7552f30c5cc8e8528f9))

## [0.7.24](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.23...common-trails-v0.7.24) (2026-03-16)


### Bug Fixes

* **deploy:** startup probe /readyz -&gt; /healthz to prevent boot loop ([4b7bd0c](https://github.com/polomarcus/common-trails/commit/4b7bd0ce2b1aa4dff407f4fe0a2fc9077e022e99))

## [0.7.23](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.22...common-trails-v0.7.23) (2026-03-16)


### Features

* Sentry routing observability + route sharing + OG previews ([adaf6f3](https://github.com/polomarcus/common-trails/commit/adaf6f3bb3440c15ead8cf87fde7aaead61a0a57))

## [0.7.22](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.21...common-trails-v0.7.22) (2026-03-13)


### Features

* Strava-only login + routing audit fixes ([#65](https://github.com/polomarcus/common-trails/issues/65)) ([7b83487](https://github.com/polomarcus/common-trails/commit/7b83487575610306ad7017587af51591f099988e))

## [0.7.21](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.20...common-trails-v0.7.21) (2026-03-12)


### Bug Fixes

* correct GCE metadata URL for Cloud Run job trigger ([344d318](https://github.com/polomarcus/common-trails/commit/344d318659523b84c269cff40975c0eee495d1cf))

## [0.7.20](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.19...common-trails-v0.7.20) (2026-03-12)


### Features

* broaden running sport routing + parallelize external fallback ([#62](https://github.com/polomarcus/common-trails/issues/62)) ([8e6d4bc](https://github.com/polomarcus/common-trails/commit/8e6d4bce1be2ed65541f5e381baed6ab1fd41b27))

## [0.7.19](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.18...common-trails-v0.7.19) (2026-03-12)


### Features

* two-phase Strava import — polylines fast, then batched GPS upgrade ([#60](https://github.com/polomarcus/common-trails/issues/60)) ([7462445](https://github.com/polomarcus/common-trails/commit/7462445208904b577d73b737c78418c7de6cdfeb))

## [0.7.18](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.17...common-trails-v0.7.18) (2026-03-12)


### Features

* cell coverage layer, SPA server fix, top nav account link ([#57](https://github.com/polomarcus/common-trails/issues/57)) ([f16457b](https://github.com/polomarcus/common-trails/commit/f16457ba93d1c6316899c1748f7c9b2dcd40ff88))

## [0.7.17](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.16...common-trails-v0.7.17) (2026-03-11)


### Features

* add "Couverture cellules" user cell coverage map layer ([2d7be77](https://github.com/polomarcus/common-trails/commit/2d7be77abe399d73e278a733629dd6b7c43b1024))
* add GitHub-like Tags & Pull Requests UI for routes ([7e95e58](https://github.com/polomarcus/common-trails/commit/7e95e5854314cc117c03690b3f505940d96b239f))


### Bug Fixes

* replace python http.server with SPA-aware static server ([8a8555b](https://github.com/polomarcus/common-trails/commit/8a8555b3f847f510d016c60c5f8da26dab881935))
* use REST API instead of google-cloud-run SDK to trigger job ([7b73a43](https://github.com/polomarcus/common-trails/commit/7b73a4351207d2e334cf7d53fd57c6638d029973))

## [0.7.16](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.15...common-trails-v0.7.16) (2026-03-11)


### Features

* wire up Cloud Run Job for Strava import ([#53](https://github.com/polomarcus/common-trails/issues/53)) ([2d492c5](https://github.com/polomarcus/common-trails/commit/2d492c51d686a9d56154b989c0e923a24a69c7c2))

## [0.7.15](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.14...common-trails-v0.7.15) (2026-03-11)


### Bug Fixes

* deploy workflow memory 1Gi→4Gi + max-instances 1 ([#51](https://github.com/polomarcus/common-trails/issues/51)) ([cf02f2e](https://github.com/polomarcus/common-trails/commit/cf02f2e97010b68f3227fe0385cce47b0d193525))

## [0.7.14](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.13...common-trails-v0.7.14) (2026-03-11)


### Bug Fixes

* non-blocking startup + Cloud Run stability improvements ([#49](https://github.com/polomarcus/common-trails/issues/49)) ([5169daa](https://github.com/polomarcus/common-trails/commit/5169daadcd6b5f66fb99e439d934f3490746a48e))

## [0.7.13](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.12...common-trails-v0.7.13) (2026-03-11)


### Bug Fixes

* DFCI cache write to /tmp + show skipped count in Strava UI ([#46](https://github.com/polomarcus/common-trails/issues/46)) ([2978c43](https://github.com/polomarcus/common-trails/commit/2978c43139cb7b6bb8457a9085c969d730b29e1b))

## [0.7.12](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.11...common-trails-v0.7.12) (2026-03-11)


### Bug Fixes

* GCS routing — remove trailingSlash, fix basePath for all navigations ([#44](https://github.com/polomarcus/common-trails/issues/44)) ([e99aa77](https://github.com/polomarcus/common-trails/commit/e99aa77aaafce48e2c033478c410591361a47179))

## [0.7.11](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.10...common-trails-v0.7.11) (2026-03-11)


### Features

* add cold start wakeup banner for Cloud Run beta ([#42](https://github.com/polomarcus/common-trails/issues/42)) ([c23c04e](https://github.com/polomarcus/common-trails/commit/c23c04e7437c17c995f6f9c1a9b10171f27c6ce5))

## [0.7.10](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.9...common-trails-v0.7.10) (2026-03-11)


### Bug Fixes

* extract origin from FRONTEND_URL for CORS (strip path) ([#40](https://github.com/polomarcus/common-trails/issues/40)) ([81ed785](https://github.com/polomarcus/common-trails/commit/81ed785cdb62a41c42adbaaed7c7e4ed42cafbff))

## [0.7.9](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.8...common-trails-v0.7.9) (2026-03-11)


### Bug Fixes

* add trailing slash to Strava redirect for GCS static hosting ([#38](https://github.com/polomarcus/common-trails/issues/38)) ([300fc14](https://github.com/polomarcus/common-trails/commit/300fc14d2ab82fff31d8bede376f506be792f8b3))

## [0.7.8](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.7...common-trails-v0.7.8) (2026-03-11)


### Bug Fixes

* use explicit CORS origins instead of wildcard with credentials ([#36](https://github.com/polomarcus/common-trails/issues/36)) ([784ca40](https://github.com/polomarcus/common-trails/commit/784ca404f7341b4c7ca1d7d3ce5c6248e0afd322))

## [0.7.7](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.6...common-trails-v0.7.7) (2026-03-11)


### Features

* migrate backend from in-memory dicts to PostgreSQL ([#34](https://github.com/polomarcus/common-trails/issues/34)) ([dea9abb](https://github.com/polomarcus/common-trails/commit/dea9abb1d0868ecb02004302c76ca21628698778))

## [0.7.6](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.5...common-trails-v0.7.6) (2026-03-11)


### Bug Fixes

* backend ready on API response, not edge count + REST API copies ([#32](https://github.com/polomarcus/common-trails/issues/32)) ([d1e33c9](https://github.com/polomarcus/common-trails/commit/d1e33c9d98da363a4a1c5a802d9ef6f941152880))

## [0.7.5](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.4...common-trails-v0.7.5) (2026-03-10)


### Bug Fixes

* use GCS REST API for extensionless URL copies ([#30](https://github.com/polomarcus/common-trails/issues/30)) ([7ea7f87](https://github.com/polomarcus/common-trails/commit/7ea7f8774bd9a6dc446b031d72a0bf8c82482fbe))

## [0.7.4](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.3...common-trails-v0.7.4) (2026-03-10)


### Bug Fixes

* use gcloud storage cp instead of gsutil for extensionless copies ([#28](https://github.com/polomarcus/common-trails/issues/28)) ([68ffe48](https://github.com/polomarcus/common-trails/commit/68ffe489587f7d82558ca36033e411d599e66980))

## [0.7.3](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.2...common-trails-v0.7.3) (2026-03-10)


### Bug Fixes

* use stdin pipe for gsutil cp to avoid directory interpretation ([#26](https://github.com/polomarcus/common-trails/issues/26)) ([882ca38](https://github.com/polomarcus/common-trails/commit/882ca38cf56101ddb299f649cdf91493a8afff18))

## [0.7.2](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.1...common-trails-v0.7.2) (2026-03-10)


### Bug Fixes

* gsutil -h flag goes before cp subcommand ([#24](https://github.com/polomarcus/common-trails/issues/24)) ([c9442b6](https://github.com/polomarcus/common-trails/commit/c9442b6aad8e24ad73fb1b3675de4514030cebac))

## [0.7.1](https://github.com/polomarcus/common-trails/compare/common-trails-v0.7.0...common-trails-v0.7.1) (2026-03-10)


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
* heatmap pastel + Mon Compte stats link + route save → detail page ([3e28c52](https://github.com/polomarcus/common-trails/commit/3e28c52e0b86103a90021182e4355c4b4c345df3))
* heatmap sport filter (Route / VTT / Gravel / Off-road / Tous) ([e5e6b9b](https://github.com/polomarcus/common-trails/commit/e5e6b9b350f5807a5ffcd02c7f16595c8a50333c))
* home redesign, Strava real import, Garmin tutorial, sidebar glow, stats highlight ([522f54c](https://github.com/polomarcus/common-trails/commit/522f54c54eeaca07d64052c679f12b93647eacd4))
* IGN basemap, trace click area, route name input, ?route= URL loading ([e8e925a](https://github.com/polomarcus/common-trails/commit/e8e925a79dfe39c46a06f804b9ac1fd0f45f894e))
* IGN BDTopo-pgr routing engine + toggle Auto/OSRM/IGN ([86a0a0c](https://github.com/polomarcus/common-trails/commit/86a0a0c2b0128d799d43e4a98d4412221e47be12))
* Komoot tutorial modal + redirect to /map after Strava import ([c1eb42e](https://github.com/polomarcus/common-trails/commit/c1eb42efb260fad2b50ab32025968f03d9f093a3))
* map UX improvements + 6 bug fixes ([#6](https://github.com/polomarcus/common-trails/issues/6)) ([44500d0](https://github.com/polomarcus/common-trails/commit/44500d06993f60687a1253dc5a0eda1b681dd0fa))
* persist activities to Docker volume (survive restarts) ([0019c69](https://github.com/polomarcus/common-trails/commit/0019c69acf0ab77a31ca3ec88a9fded0182fa3fa))
* popup trace cliquable + navigation stats→carte/discover ([42c398a](https://github.com/polomarcus/common-trails/commit/42c398ab3f0b2482088a7332d8da5af4283c877d))
* port 8787, Strava OAuth, heatmap bbox, seed activities + routes, UX polish ([93a2ebb](https://github.com/polomarcus/common-trails/commit/93a2ebbb223b9b1d58707c9a96935a4d7ec34254))
* public routes sidebar tab + Strava link + route name clickable ([e054691](https://github.com/polomarcus/common-trails/commit/e054691bf5dcd7afba14d752cd5e57c81e6a1637))
* real tile map + detailed elevation chart + parent fork comparison + edit sport + sidebar sort ([9b1857f](https://github.com/polomarcus/common-trails/commit/9b1857fc50e0b86f3092347baac8bb16af29d5e6))
* recalculate route when sport or engine changes ([2e1b75a](https://github.com/polomarcus/common-trails/commit/2e1b75afd93cb08ad6fbbbf4f6448936d7812e89))
* redesign Discover page — nav, cards grid, map link, empty state CTA ([18a1e9f](https://github.com/polomarcus/common-trails/commit/18a1e9f5e9bd739d2decd41fe63f0ab76fa8319d))
* routing anti-détour + quality score + Panoramax markers ([30d19cd](https://github.com/polomarcus/common-trails/commit/30d19cd50f0aa759d70dec435611b2a4f88934aa))
* seed routes + fix discover + UX improvements ([5556990](https://github.com/polomarcus/common-trails/commit/555699059f26dfb74a9f3a53574348a7019d8961))
* sidebar traces par proximité + fix JWT 7j + admin@admin + heatmap K=1 ([#7](https://github.com/polomarcus/common-trails/issues/7)) ([377f14a](https://github.com/polomarcus/common-trails/commit/377f14ab1e8db2bd8a18c2e755e68319d2577343))
* smart routing — heatmap → personal traces → OSRM ([#8](https://github.com/polomarcus/common-trails/issues/8)) ([8fc0df6](https://github.com/polomarcus/common-trails/commit/8fc0df618b215a8601a080749500916716797870))
* **stats:** fun distance comparisons + top 5 clickable records ([b4566e6](https://github.com/polomarcus/common-trails/commit/b4566e645c7e175b30e287e5b2b9bc46c4f42280))
* **step-2:** add FastAPI backend with auth and Strava OAuth stub ([115312a](https://github.com/polomarcus/common-trails/commit/115312a600c2a4c47d882376dc1b95054ecb2773))
* **step-3:** add DB models, Alembic migrations, and GitHub-like routes API ([0c682c0](https://github.com/polomarcus/common-trails/commit/0c682c0744de69dd7bf83c44af6d48bd054a8fa7))
* **step-4:** add Strava import, file upload, ingestion, and heatmap API ([bf416c7](https://github.com/polomarcus/common-trails/commit/bf416c765929da54e942578cab42cb548735f63b))
* **step-5:** add pgRouting, seed minigraph, GPX upload, and me/stats ([925f338](https://github.com/polomarcus/common-trails/commit/925f3388533adb88617603b44c0215009769b7bf))
* **step-6:** add map UI, Quick Connect, Playwright E2E tests, and CI ([2989cae](https://github.com/polomarcus/common-trails/commit/2989cae9a5b9e6449ffda6202aff959b033702f2))
* **step-7:** add discover heatmap and stats UI with profile system ([9dd72e6](https://github.com/polomarcus/common-trails/commit/9dd72e635e9f100e7c3dbe57ee27d25864141d01))
* switch basemap to OpenFreeMap vector tiles + basemap switcher ([8286424](https://github.com/polomarcus/common-trails/commit/8286424bb2fa8628a00043ab43d02010f5c20292))
* trace sidebar — fitBounds centering + detail panel (date, fork to route) ([a745346](https://github.com/polomarcus/common-trails/commit/a745346f98baafd2248fb21348d509d346a4f63c))
* user menu on map — logout, Strava sync, Garmin tutorial, GPX import ([347e8aa](https://github.com/polomarcus/common-trails/commit/347e8aadb5f0a3c5b8feac4eba7a957252a1990a))
* UX improvements — drag fix, heatmap pastel, stats, Mon Compte ([7b1863c](https://github.com/polomarcus/common-trails/commit/7b1863c44197f6f574a5055ff07e374ca5c52d51))


### Bug Fixes

* CI — popup bloque les clics carte + import GPX dans menu utilisateur ([f4d3da0](https://github.com/polomarcus/common-trails/commit/f4d3da0bc615b6dc9652a71fef0e837ea0ea9724))
* **ci:** accept 307 in Strava connect test (FastAPI RedirectResponse default) ([7c55b03](https://github.com/polomarcus/common-trails/commit/7c55b03acb3dbb5da1aaa9c08c708ca2b08ef19a))
* **ci:** add CSS type declaration for maplibre-gl import in Next.js 16 ([d3f66ba](https://github.com/polomarcus/common-trails/commit/d3f66bad0ed8411b7fd085df9e861d1c220c4db3))
* **ci:** bump @playwright/test to 1.51.1 to satisfy next 16.1.6 peer dep ([0bbb08a](https://github.com/polomarcus/common-trails/commit/0bbb08aff0501f68388d7a3685e7e0b415afa8c8))
* **ci:** CSS type declaration for maplibre-gl — Next.js 16 TS build ([27c8aa3](https://github.com/polomarcus/common-trails/commit/27c8aa3dfb6673767ebaa306cc959a77b628f460))
* **ci:** fix 3 maplibre-gl 4.x TS strict mode errors in Map.tsx ([14935c2](https://github.com/polomarcus/common-trails/commit/14935c2f9062ab9e14ac090aad0ae644953619e1))
* **ci:** replace [@ts-expect-error](https://github.com/ts-expect-error) with [@ts-ignore](https://github.com/ts-ignore) for maplibre Map constructor ([0ac4c86](https://github.com/polomarcus/common-trails/commit/0ac4c86ea6c202b0bc361bf6d6cec58cd63f934e))
* **ci:** type OFFLINE_STYLE as StyleSpecification to fix readonly layers error ([2fca74a](https://github.com/polomarcus/common-trails/commit/2fca74a2321b1b190ab4b394e7b7355eb70a3339))
* deploy workflow auth — setup gcloud before verify step ([1604d79](https://github.com/polomarcus/common-trails/commit/1604d7920b543cfd50d83922854e9e1f71e96992))
* deploy workflow auth order ([7523a90](https://github.com/polomarcus/common-trails/commit/7523a90f78ed2726f768813648f5683341b17011))
* drag & drop crash — preserve anchor structure after sport/engine recalculation ([b0396c9](https://github.com/polomarcus/common-trails/commit/b0396c940bff2d92fc1de43ca3fcc087c62417e1))
* drag waypoint index + route save display + heatmap precompute ([7852cb1](https://github.com/polomarcus/common-trails/commit/7852cb1f90825178aa64420323ee5334785548b4))
* E2E test 6 — use .first() for CHEMINS COMMUNS locator (nav + footer both match) ([8c9f031](https://github.com/polomarcus/common-trails/commit/8c9f03103161105bafc62a0b4588c09b1a5a7ae2))
* E2E test failures — duplicate testid, redirect timing, fork geometry ([237c8e8](https://github.com/polomarcus/common-trails/commit/237c8e88de99263801642958c34b50ee14711f42))
* GCS frontend asset paths, Sentry PII, hide dev credentials ([#18](https://github.com/polomarcus/common-trails/issues/18)) ([18800c2](https://github.com/polomarcus/common-trails/commit/18800c2c1c6da55afd17d001961020620fb0badb))
* GCS page refresh + Strava redirect URI ([#21](https://github.com/polomarcus/common-trails/issues/21)) ([542dac7](https://github.com/polomarcus/common-trails/commit/542dac700c4760ce7c9c78cf7f328be6b64736b0))
* GCS page refresh for extensionless URLs ([#22](https://github.com/polomarcus/common-trails/issues/22)) ([a8c20dd](https://github.com/polomarcus/common-trails/commit/a8c20dd60b1bf46966cd1d6ecedf33e3ebffe9ef))
* hybrid diversity backfill for Luberon proposals ([#15](https://github.com/polomarcus/common-trails/issues/15)) ([986d16d](https://github.com/polomarcus/common-trails/commit/986d16daa9e54799983899e1bf1d84bf2b10aa79))
* line-drag splice index + route display after save + heatmap precompute cache ([13d0778](https://github.com/polomarcus/common-trails/commit/13d077814743aee032d8781e9d72a25195a05f36))
* redirect only if already-logged-in at mount + "Connecté" badge in logged-in view ([5128bdb](https://github.com/polomarcus/common-trails/commit/5128bdbb0fe54e1b5489e666ae16645679f46462))
* resolve 4 E2E test failures ([20fe45c](https://github.com/polomarcus/common-trails/commit/20fe45cf2a47a3736573f47fe2a0931ef652fbe5))
* revert OSRM profile for gravel/mtb/offroad from foot to cycling ([9a6a96c](https://github.com/polomarcus/common-trails/commit/9a6a96c3a62a5015ac293e54098f08523b99363d))
* selected activity highlight + Discover page fixes ([ccd16b7](https://github.com/polomarcus/common-trails/commit/ccd16b78904f8f73b0bb670cedff7da5d5c81ae6))
* stats 'Tout' default + map routing + unique_cells bug ([72b375b](https://github.com/polomarcus/common-trails/commit/72b375b736308f6f502e25a017318e9ef2228093))
* stats 'Tout' default + map routing + unique_cells bug ([828f401](https://github.com/polomarcus/common-trails/commit/828f401d51ffa996767af0b60dc312fcdb1c6698))
* stats "Découvrir" → "Itinéraires similaires" avec pré-sélection du sport ([f388cd8](https://github.com/polomarcus/common-trails/commit/f388cd8f9e7594508287be1d4da5bffb98aab670))
* UX bugfixes — waypoint restore, drag ghost, Panoramax hover ([#12](https://github.com/polomarcus/common-trails/issues/12)) ([594b5a4](https://github.com/polomarcus/common-trails/commit/594b5a4f4668b20c7f0f6d45190f41d27f9b72a5))


### Miscellaneous

* add .gitignore, remove pycache from tracking ([33da6ed](https://github.com/polomarcus/common-trails/commit/33da6edf8f9decc5ea20f11304bec19d8b3932a8))
* align runtimes to python 3.13 and nextjs 16 ([79ec4d3](https://github.com/polomarcus/common-trails/commit/79ec4d35530170a0806ceec33fe77a51e04f7db8))
* **step-1:** scaffold docker compose and repo structure ([94a7b4b](https://github.com/polomarcus/common-trails/commit/94a7b4b2917d54540badef65b89118072a79bf2c))
* **step-9:** add serverless GCP infra and static frontend deploy ([2d627e3](https://github.com/polomarcus/common-trails/commit/2d627e31b8956ff9afcb98a2635df80b924bc648))
