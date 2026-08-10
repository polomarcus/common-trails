# Routing Cost Model — Reference

*This document specifies the exact formulas and constants used in edge cost computation. For architectural context, see [routing-architecture.md](routing-architecture.md).*

---

## Single-Edge Cost

```
cost = L × S × U × H × T × V
```

All factors are multiplicative and independently interpretable.

### L — Distance (meters)

Haversine distance between edge endpoints. For performance, the client uses a flat-earth approximation (< 0.3% error at mid-latitudes):

```
Δlat = (lat₂ - lat₁) × 111,320
Δlon = (lon₂ - lon₁) × 111,320 × cos(44°)
L = √(Δlat² + Δlon²)
```

### S — Slope Factor

Two models depending on elevation data availability:

**Exponential model** (when `hasElevation = true` — edge has ele1/ele2 data):

```
raw_penalty:
  |grade| < 5%  → 1.0
  |grade| < 8%  → 1.0 + (|grade| - 5) / 5 × 0.2     (linear: 1.0 → 1.12)
  |grade| ≥ 8%  → min(3.0, 1.12 × e^(0.08 × (|grade| - 8)))  (exponential from 1.12)
```

C0-continuous at 8% splice point: `slopeFactor(7.99) ≈ slopeFactor(8.01)`. This makes 12% grades dramatically more expensive than 3% grades, matching rider perception of fatigue.

**Piecewise linear fallback** (no elevation data — `ele1 == ele2 == 0`):

```
raw_penalty:
  |grade| < 5%  → 1.0
  |grade| < 10% → 1.0 + (|grade| - 5) / 5 × 0.2
  |grade| < 15% → 1.2 + (|grade| - 10) / 5 × 0.3
  |grade| ≥ 15% → min(2.0, 1.5 + (|grade| - 15) / 10 × 0.5)
```

**Common to both models:**

```
downhill adjustment:
  if grade < 0: |grade| × 0.7  (30% less penalty)

sport scaling:
  S = max(FLOOR, 1.0 + (raw_penalty - 1.0) × slope_penalty)
  FLOOR = 0.80
```

Sport `slope_penalty` values: road=1.0, gravel=0.8, mtb=0.6, offroad=0.5, running=0.9

### Elevation Encoding (CTGB binary format)

Edges are now 24 bytes (was 20). The last 4 bytes encode `ele1` (int16) and `ele2` (int16) in meters. Relative encoding: `ele1 = 0, ele2 = round(ele_delta_m)`. When `ele1 == ele2 == 0` and `slopeGrade == 0`, the edge has no elevation data.

### U — Surface Factor

| Surface | Idx | Road | Gravel | MTB | Off-road | Running |
|---------|-----|------|--------|-----|----------|---------|
| Asphalt | 0 | 1.0 | 1.1 | 1.6 | 2.0 | 1.0 |
| Gravel | 1 | 1.1 | 0.80 | 0.90 | 0.90 | 1.05 |
| Dirt | 2 | 1.3 | 1.0 | 0.90 | 0.90 | 1.0 |
| Rock | 3 | 1.6 | 1.2 | 1.0 | 1.0 | 1.2 |
| Unknown | 4 | 1.15 | 1.1 | 1.05 | 1.0 | 1.15 |

### H — Heatmap Factor

```
heat_score = min(1.0, log₂(1 + user_count) / 4.0)
H = 1.0 - heat_score × heat_max_bonus
```

Per-sport `heat_max_bonus`: road=0.15, gravel=0.25, mtb=0.35, offroad=0.35, running=0.20

Saturation table:

| user_count | heat_score | Comment |
|-----------|-----------|---------|
| 0 | 0.00 | No data |
| 1 | 0.25 | First evidence |
| 3 | 0.50 | Confirmed |
| 7 | 0.75 | Popular |
| 15+ | 1.00 | Saturated |

### T — Trail Factor

```
T = 1.0 - trail_score × trail_max_bonus
```

Trail scores: GT=1.0, DFCI=1.0, EV=0.9, GR=0.8, GRP=0.8, PR=0.5, none=0.0

Per-sport `trail_max_bonus`: road=0.10, gravel=0.35, mtb=0.30, offroad=0.30, running=0.15

Road profiles exclude hiking trails (GR, GRP, GT, PR, DFCI) from bonus eligibility.

### V — Unvalidated Penalty

```
V = 1.15  if user_count == 0 AND trail_type == none
V = 1.0   otherwise
```

---

## Cost Caching

During graph building, the cost multiplier (S × U × H × T × V, excluding distance) is cached by a composite key:

```
key = "slopeBucket{±}:surfIdx:hwIdx:trIdx:ucBucket"
```

Slope is bucketed to 1% precision; user count is capped at 20 (log₂ saturates beyond). This avoids redundant computation across the ~3M edges in a typical graph, reducing merge time by ~60%.

---

## Intent Strength Override

```
blended_cost = edge.cost × (1 - intent) + edge.dist × intent
```

| intent | Behavior |
|--------|----------|
| 0.0 | Full cost model (heatmap/trail optimization) |
| 0.5 | Balanced — moderate bonus attenuation |
| 1.0 | Pure distance — user is forcing a specific path |

Side effects of intent:
- VIP snap bias reduced: `vipBias × (1 - 0.8 × intent)`
- Detour cap tightened: `baseDetour - intent × 0.15` (client) / `baseDetour × (1 + 0.2 × intent)` (server)
- Trail bonus preserved at 50% even at max intent

---

## Corridor Penalty (Multi-Proposal)

When computing diverse alternatives, edges near previous routes receive multiplicative penalties:

```
effective_cost = base_cost × corridor_penalty
```

| Proximity | Penalty | Computation |
|-----------|---------|-------------|
| Both endpoints on path | 5.0 | O(1) Set lookup |
| One endpoint on path | 5.0^0.6 ≈ 2.6 | O(1) Set lookup |
| Midpoint < 200m | 5.0 | Spatial grid + haversine |
| Midpoint 200-500m | 5.0^0.6 ≈ 2.6 | Distance decay |
| Midpoint 500-1000m | 5.0^0.3 ≈ 1.6 | Distance decay |
| Midpoint > 1000m | 1.0 | No penalty |

Penalty base 5.0 and distance thresholds are tuned for graphs with ~1km average edge length. For proposal C, the maximum penalty from A and B is used.

---

## Direction Factor (MTB only)

```
D = direction_penalty  if going against dominant direction
D = 1.0               otherwise

Conditions:
  forward_count + backward_count ≥ 3  (minimum data)
  max(fwd, bwd) / total ≥ 0.75        (75% directional dominance)
  direction_penalty = 2.5
```

This captures one-way MTB trails (e.g., downhill-only) where riding against traffic is dangerous or prohibited.

---

## Detour Caps

| Sport | Client max | Server max | With off-heatmap |
|-------|-----------|-----------|-----------------|
| Road | 1.8× | 1.15× | +1.0 |
| Gravel | 2.0× | 1.20× | +1.0 |
| MTB | 2.5× | 1.30× | +1.0 |
| Off-road | 3.0× | 1.35× | +1.0 |
| Running | 2.0× | 1.20× | +1.0 |

Absolute ceiling: 5.0× (never exceeded regardless of other factors).

Client caps are wider because the client graph may have connectivity gaps requiring detours. Server caps are tighter because the server graph includes the full road network.

---

## Enum Indices

```
Surface: asphalt=0, gravel=1, dirt=2, rock=3, unknown=4
Highway: residential=0, tertiary=1, secondary=2, primary=3,
         unclassified=4, track=5, path=6, cycleway=7,
         steps=8, service=9, unknown=10
Trail:   none=0, DFCI=1, GR=2, GRP=3, GT=4, PR=5, EV=6
```
