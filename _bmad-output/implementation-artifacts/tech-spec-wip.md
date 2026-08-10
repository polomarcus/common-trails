---
title: 'CTGB Forward Compat + Version-Aware Tile Cache + Adaptive Tile Splitting'
slug: 'ctgb-v2-tile-cache-splitting'
created: '2026-03-31'
status: 'in-progress'
stepsCompleted: [1]
tech_stack: []
files_to_modify: []
code_patterns: []
test_patterns: []
---

# Tech-Spec: CTGB Forward Compat + Version-Aware Tile Cache + Adaptive Tile Splitting

**Created:** 2026-03-31

## Overview

### Problem Statement

Three Kleppmann-identified gaps in the routing tile pipeline threaten data integrity and scalability:

1. **CTGB has no forward compatibility.** The binary format (v1, 20 bytes/edge) hard-rejects any version != 1 and has no field-tag system. Adding fields (elevation, one-way flags, surface confidence) requires a breaking change that leaves deployed clients unable to read cached tiles during rollout. The upcoming CH+elevation spec already plans expanding to 24 bytes, making this urgent.

2. **IndexedDB ignores manifest version.** Tile cache uses 24h TTL, but the CDN manifest carries a `version` field. After a graph rebuild, clients serve stale tiles from IndexedDB for up to 24 hours — the two invalidation strategies conflict. There is no end-to-end version enforcement.

3. **z14 tiles have geographic hot spots.** Cycling data is extremely skewed: dense regions (Ardèche, Vaucluse, Luberon) may have 10-50x more edges per tile than flat plains. A single hot tile dominates fetch + parse time, while empty tiles waste HTTP requests.

### Solution

1. **CTGB v2 header:** Add `edge_size` field (uint16) at bytes [6-7] (currently reserved). Old v1 decoders skip the reserved bytes but fail on edge count math — so we bump version to 2. New decoders read `edge_size` from header and can skip unknown trailing bytes per edge. This makes adding future fields (elevation, one-way, confidence) non-breaking.

2. **Version-aware IndexedDB cache:** Store manifest `version` alongside each cached tile entry. On cache read, reject tiles whose stored version doesn't match the current manifest. Also enforce monotonic reads (refuse manifests older than last-seen). This eliminates stale-tile windows completely.

3. **Adaptive tile splitting:** When a z14 tile exceeds a configurable edge threshold (e.g., 5000 edges), the backend splits it into 4 z15 sub-tiles. The manifest declares which z14 tiles are split. The client checks the manifest before fetching and requests z15 children instead of the z14 parent when split.

### Scope

**In Scope:**
- CTGB v2 header with `edge_size` field; backward-compatible decoder that reads v1 (20) and v2 (variable)
- Backend encoder updated to write v2 header with configurable edge size
- IndexedDB `CachedEntry` extended with `version` field; version check on read
- Monotonic manifest reads (client rejects older-than-last-seen manifests)
- Backend tile endpoint: detect dense tiles, split to z15
- Manifest schema: `split_tiles` map declaring which z14 tiles have z15 children
- Frontend tile loader: check manifest split map, fetch z15 children when applicable

**Out of Scope:**
- Actually adding new edge fields (elevation, one-way) — that's the CH+elevation spec
- Protobuf migration (CTGB is sufficient with forward compat)
- Dynamic zoom levels beyond z15 (z15 sub-tiles are the single split level)
- Manifest format versioning (keep loose JSON, add fields additively)

## Context for Development

### Codebase Patterns

(To be filled in Step 2)

### Files to Reference

| File | Purpose |
| ---- | ------- |

(To be filled in Step 2)

### Technical Decisions

- CTGB v2 uses `edge_size` in reserved header bytes rather than switching to protobuf — preserves parse speed advantage (5x vs JSON) while gaining forward compat.
- IndexedDB stores manifest version per-tile (not full-store flush) to avoid cold-cache storms after rebuilds when most tiles haven't changed.
- z15 splitting is declared in manifest (not auto-detected by client) to keep tile loading logic simple and deterministic.

## Implementation Plan

### Tasks

(To be generated in Step 3)

### Acceptance Criteria

(To be generated in Step 3)

## Additional Context

### Dependencies

- The CH+elevation spec (`tech-spec-ch-elevation-routing.md`) depends on CTGB v2 being in place first — it will be the first consumer of the `edge_size` extensibility.

### Testing Strategy

(To be generated in Step 3)

### Notes

- Inspired by Martin Kleppmann's "Designing Data-Intensive Applications" analysis of encoding evolution (Ch.4), storage engine versioning (Ch.3), and partitioning hot spots (Ch.6).
