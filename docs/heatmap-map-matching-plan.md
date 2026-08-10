# Plan: map-matching at ingest — SUPERSEDED

> **Status (2026-06):** This plan proposed an external **Valhalla** HMM map-matcher
> at ingest. That path was implemented behind `MAP_MATCHER_ENABLED` but **never
> deployed** (no Valhalla service in prod), and has since been **removed**.
>
> The map-matcher today is the **in-process Viterbi trajectory HMM**
> (`SPATIAL_HMM_ENABLED`, default ON) layered on the per-point spatial matcher in
> `backend/app/services/ingest.py` (`_match_to_osm`). It snaps GPS points to the
> OSM network in-process, with arc-length bucketing (`_snap_along_segment`, 1 m)
> for cross-rider K-anonymity at the producer layer. Edges carry
> `match_source = 'spatial'` or `'grid_fallback'`.
>
> See `docs/heatmap-pipeline.md` for the current Stage-1 (map-matching) behaviour.

The original Valhalla design is preserved in git history (pre-2026-06) for
reference. It is no longer the intended direction: an in-process matcher avoids
the operational cost of a separate tile-building service for a single-region,
early-stage deployment.
