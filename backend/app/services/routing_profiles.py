"""Routing profiles and unified cost model.

Cost formula: cost(edge) = L × S × U × A × H × T × D
  L = length in meters (haversine)
  S = slope factor (grade-dependent penalty)
  U = surface factor (profile-specific multiplier)
  A = access factor (hard exclusion for incompatible highways)
  H = heat factor (community heatmap bonus, 0-1 heat_score)
  T = trail factor (marked trail bonus)
  D = direction factor (penalty for going against dominant trace direction, MTB only)

Pure functions, zero external dependencies.
"""
import math
from dataclasses import dataclass

INF = float("inf")


@dataclass(frozen=True)
class RoutingProfile:
    name: str
    slope_penalty: float
    surface_factors: dict[str, float]
    surface_default: float
    excluded_highways: frozenset[str]
    heat_max_bonus: float
    trail_max_bonus: float
    detour_limit: float
    # Direction awareness: penalise routing against dominant trace direction.
    # Only meaningful for MTB where segments can be downhill-only.
    direction_penalty: float = 1.0      # max cost multiplier when going against flow (1.0 = disabled)
    direction_threshold: float = 0.75   # directional ratio above which penalty kicks in
    direction_min_passes: int = 3       # minimum total passes before enabling penalty
    # Trail types that get NO bonus for this profile (e.g. road shouldn't follow hiking trails)
    excluded_trail_types: frozenset[str] = frozenset()


PROFILES: dict[str, RoutingProfile] = {
    "road": RoutingProfile(
        name="road",
        slope_penalty=1.0,
        surface_factors={
            "asphalt": 1.0,
            "gravel": 1.1,
            "dirt": 1.3,
            "rock": 1.6,
            "unknown": 1.15,
        },
        surface_default=1.15,
        excluded_highways=frozenset({"motorway", "motorway_link", "trunk", "trunk_link", "steps"}),
        heat_max_bonus=0.15,
        trail_max_bonus=0.10,
        detour_limit=1.15,
        # Road cyclists should never route through hiking/offroad trails (only EV is fine)
        excluded_trail_types=frozenset({"DFCI", "GR", "GRP", "GT", "PR"}),
    ),
    "gravel": RoutingProfile(
        name="gravel",
        slope_penalty=0.8,
        surface_factors={
            "asphalt": 1.1,
            "gravel": 0.80,
            "dirt": 1.0,
            "rock": 1.2,
            "unknown": 1.1,
        },
        surface_default=1.1,
        excluded_highways=frozenset({"motorway", "motorway_link", "trunk", "trunk_link", "steps"}),
        heat_max_bonus=0.25,
        trail_max_bonus=0.35,    # DFCI supreme: 0.65 factor (beats heat 0.75)
        detour_limit=1.20,
    ),
    "mtb": RoutingProfile(
        name="mtb",
        slope_penalty=0.6,
        surface_factors={
            "asphalt": 1.6,      # strong asphalt penalty
            "gravel": 0.90,
            "dirt": 0.90,        # bonus dirt
            "rock": 1.0,
            "unknown": 1.05,     # mtb tolerates unknown surfaces
        },
        surface_default=1.05,
        excluded_highways=frozenset({"motorway", "motorway_link", "trunk", "trunk_link", "steps"}),
        heat_max_bonus=0.35,     # strong heatmap bonus
        trail_max_bonus=0.30,    # trail bonus: DFCI 0.60×0.30=18% discount, GT 1.0×0.30=30%
        detour_limit=1.30,
        direction_penalty=2.5,   # going against dominant flow is strongly penalised
        direction_threshold=0.75,
        direction_min_passes=3,
    ),
    "offroad": RoutingProfile(
        name="offroad",
        slope_penalty=0.5,
        surface_factors={
            "asphalt": 2.0,      # strong asphalt penalty
            "gravel": 0.90,
            "dirt": 0.90,        # bonus dirt
            "rock": 1.0,
            "unknown": 1.0,      # offroad expects unknown surfaces
        },
        surface_default=1.0,
        excluded_highways=frozenset({"motorway", "motorway_link", "trunk", "trunk_link", "steps"}),
        heat_max_bonus=0.35,     # strong heatmap bonus
        trail_max_bonus=0.30,    # trail bonus: DFCI 0.60×0.30=18% discount, GT 1.0×0.30=30%
        detour_limit=1.35,
    ),
    "running": RoutingProfile(
        name="running",
        slope_penalty=0.9,
        surface_factors={
            "asphalt": 1.0,
            "gravel": 1.05,
            "dirt": 1.0,
            "rock": 1.2,
            "unknown": 1.15,
        },
        surface_default=1.15,
        excluded_highways=frozenset({"motorway", "motorway_link", "trunk", "trunk_link", "steps"}),
        heat_max_bonus=0.20,
        trail_max_bonus=0.15,
        detour_limit=1.20,
    ),
}

# ── Official trail classification scoring ────────────────────────────────────
TRAIL_SCORES: dict[str, float] = {
    "GT": 1.0,     # Grande Traversée VTT — top priority for offroad/MTB
    "DFCI": 1.0,   # Pistes forestières DFCI — user overrides via dense waypoints
    "EV": 0.90,    # EuroVelo
    "GR": 0.80,    # Grande Randonnée — same as GR de Pays
    "GRP": 0.80,   # GR de Pays
    "PR": 0.50,    # Promenade et Randonnée — lowest official trail priority
}


def compute_trail_score(trail_type: str | None) -> float:
    """Score an official trail type (0-1). Unknown named trail = 0.5."""
    if not trail_type:
        return 0.0
    return TRAIL_SCORES.get(trail_type.upper(), 0.5)


_FLOOR = 0.80
_TRAIL_FLOOR = 0.65  # trails can give up to 35% discount (GT at max)


def get_profile(sport: str) -> RoutingProfile:
    return PROFILES.get(sport, PROFILES["road"])


def slope_factor(grade_pct: float, penalty: float, has_elevation: bool = False) -> float:
    """Slope penalty: 1.0 to 3.0, scaled by penalty param.

    When has_elevation=True: exponential model, C0-continuous at 8%.
    When has_elevation=False: piecewise linear fallback.
    Downhill uses abs(grade) × 0.7.
    """
    if penalty <= 0:
        return 1.0
    g = abs(grade_pct)
    if grade_pct < 0:
        g *= 0.7
    if not has_elevation:
        # Piecewise linear fallback for edges without elevation data
        if g < 5:
            raw = 1.0
        elif g < 10:
            raw = 1.0 + (g - 5) / 5 * 0.2  # 1.0 → 1.2
        elif g < 15:
            raw = 1.2 + (g - 10) / 5 * 0.3  # 1.2 → 1.5
        else:
            raw = 1.5 + (g - 15) / 10 * 0.5  # 1.5 → 2.0, capped
            raw = min(raw, 2.0)
    else:
        # Exponential slope factor (C0-continuous at 8% splice)
        import math
        if g < 5:
            raw = 1.0
        elif g < 8:
            raw = 1.0 + (g - 5) / 5 * 0.2  # linear: 1.0 → 1.12 at 8%
        else:
            raw = min(3.0, 1.12 * math.exp(0.08 * (g - 8)))  # exponential from 1.12
    # Scale penalty contribution: factor = 1.0 + (raw - 1.0) * penalty
    return max(_FLOOR, 1.0 + (raw - 1.0) * penalty)


def surface_factor(surface_type: str, profile: RoutingProfile) -> float:
    return max(_FLOOR, profile.surface_factors.get(surface_type, profile.surface_default))


def access_factor(highway_type: str, profile: RoutingProfile) -> float:
    if highway_type in profile.excluded_highways:
        return INF
    return 1.0


def effective_user_count(user_count: int, pass_count: int) -> int:
    """Bake pass_count into heat strength — mirror of the serialized formula.

    graph_tiles.py serializes ``GREATEST(uc, LEAST(15, uc + (pass_count+2)/3))``
    as the wire user_count so the WASM cost model (which only sees a uint8
    user_count per edge) rewards repeat-ridden corridors. The backend cascade
    reads raw heat_edges rows instead, so it applies the same formula here at
    cost time: +1 per ~3 passes (integer division), saturating at 15, never
    below the raw user_count. On single-user data (user_count 1-3, pass_count
    measured up to 38) this is the difference between a 6-9% heat discount
    and a corridor-dominating one (routing-quality audit 2026-07-09).
    Keep the two formulas in lockstep (fix/routing-clean-layer).
    """
    if user_count <= 0:
        return 0
    return max(user_count, min(15, user_count + (pass_count + 2) // 3))


def compute_heat_score(user_count: int) -> float:
    """Compute heat score (0-1) from user count.

    Logarithmic saturation: 0→0.0, 1→0.2, 3→0.4, 7→0.6, 15→0.8, 31+→1.0.
    """
    if user_count <= 0:
        return 0.0
    return min(1.0, math.log2(1 + user_count) / 5.0)


def heat_factor(heat_score: float, max_bonus: float) -> float:
    """Community heatmap bonus: higher heat_score → lower cost.

    Takes pre-computed heat_score (0-1) and max_bonus from profile.
    Returns multiplier in [1-max_bonus, 1.0].
    """
    if max_bonus <= 0 or heat_score <= 0:
        return 1.0
    return max(1.0 - max_bonus, 1.0 - heat_score * max_bonus)


def trail_factor(on_trail: bool, trail_score: float, max_bonus: float) -> float:
    if not on_trail or max_bonus <= 0:
        return 1.0
    return max(_TRAIL_FLOOR, 1.0 - trail_score * max_bonus)


def direction_factor(
    going_canonical: bool,
    forward_count: int,
    backward_count: int,
    profile: RoutingProfile,
) -> float:
    """Penalty for routing against the dominant GPS-trace direction (MTB only).

    Returns 1.0 when:
    - direction_penalty is disabled (≤ 1.0, all profiles except MTB)
    - not enough traces to determine direction (< direction_min_passes)
    - traffic is too balanced to infer a dominant direction (majority < direction_threshold)
    - going with the dominant flow

    Returns up to direction_penalty when going strongly against the dominant flow.
    This models downhill-only MTB segments: if 90% of riders go A→B,
    routing B→A is penalised so the planner avoids that direction.
    """
    if profile.direction_penalty <= 1.0:
        return 1.0
    total = forward_count + backward_count
    if total < profile.direction_min_passes:
        return 1.0
    ratio = forward_count / total  # fraction going in canonical direction [0..1]
    # Dominance of the majority direction (always ≥ 0.5)
    majority_dominance = max(ratio, 1.0 - ratio)
    if majority_dominance < profile.direction_threshold:
        return 1.0  # traffic is too balanced — no dominant direction
    # Determine whether this traversal goes with or against the majority
    going_with_majority = (going_canonical and ratio >= 0.5) or (not going_canonical and ratio < 0.5)
    if going_with_majority:
        return 1.0  # going with the flow — no penalty
    # Scale penalty linearly: direction_threshold → 1.0 .. 1.0 → direction_penalty
    scale = (majority_dominance - profile.direction_threshold) / (1.0 - profile.direction_threshold)
    return 1.0 + scale * (profile.direction_penalty - 1.0)


def apply_intent_override(
    profile: RoutingProfile, intent_strength: float,
) -> RoutingProfile:
    """Adjust profile based on user intent strength (waypoint density).

    intent_strength ∈ [0, 1]:
      0   = full smart optimization (default)
      0.5 = moderate relaxation (surface caps kick in)
      1.0 = maximum trust in user waypoints

    Hard exclusions (motorway, steps, etc.) are NEVER overridden.
    """
    if intent_strength <= 0:
        return profile
    intent_strength = min(1.0, intent_strength)

    # Cap surface penalties when intent is strong
    if intent_strength > 0.5:
        sf = {k: min(v, 1.3) for k, v in profile.surface_factors.items()}
        sd = min(profile.surface_default, 1.3)
    else:
        sf = dict(profile.surface_factors)
        sd = profile.surface_default

    return RoutingProfile(
        name=profile.name,
        slope_penalty=profile.slope_penalty,
        surface_factors=sf,
        surface_default=sd,
        excluded_highways=profile.excluded_highways,  # NEVER override
        heat_max_bonus=profile.heat_max_bonus * (1 - intent_strength),
        # Preserve 50% of trail bonus even at max intent — trails are always valuable
        trail_max_bonus=profile.trail_max_bonus * (1 - intent_strength * 0.5),
        detour_limit=profile.detour_limit * (1 + 0.2 * intent_strength),
        direction_penalty=profile.direction_penalty,
        direction_threshold=profile.direction_threshold,
        direction_min_passes=profile.direction_min_passes,
        excluded_trail_types=profile.excluded_trail_types,  # always preserve
    )


def make_proposal_variants(
    sport: str,
) -> list[tuple[str, str, "RoutingProfile"]]:
    """Return 3 diverse routing profile variants for proposal generation.

    All 3 variants use heatmap data (the best signal we have), but with
    different trade-offs to produce diverse corridors:
      A — popularity: base profile (strongest heatmap + trails)
      B — direct: heatmap-assisted but tight detour → most direct heatmap path
      C — explorer: moderate heatmap, wider detour → discovers alternative corridors
    """
    base = get_profile(sport)

    # A — Populaire: unchanged base profile (strongest heatmap bonus)
    a = base

    # B — Direct: still uses heatmap but with tight detour → shortest heatmap path
    b = RoutingProfile(
        name=base.name,
        slope_penalty=base.slope_penalty * 0.5,   # tolerate slopes to stay direct
        surface_factors=dict(base.surface_factors),
        surface_default=base.surface_default,
        excluded_highways=base.excluded_highways,
        heat_max_bonus=base.heat_max_bonus * 0.5,  # reduced heatmap influence
        trail_max_bonus=base.trail_max_bonus * 0.3, # reduced trail influence
        detour_limit=1.10,             # tight detour → stays direct
        direction_penalty=base.direction_penalty,
        direction_threshold=base.direction_threshold,
        direction_min_passes=base.direction_min_passes,
        excluded_trail_types=base.excluded_trail_types,
    )

    # C — Explorer: moderate heatmap, wider detour → finds different corridors
    # Combined with corridor penalties this produces genuinely different routes
    c = RoutingProfile(
        name=base.name,
        slope_penalty=base.slope_penalty * 0.3,    # very slope-tolerant → climbs OK
        surface_factors=dict(base.surface_factors),
        surface_default=base.surface_default,
        excluded_highways=base.excluded_highways,
        heat_max_bonus=base.heat_max_bonus * 0.8,  # still uses heatmap
        trail_max_bonus=base.trail_max_bonus * 1.2, # boost trails → offbeat routes
        detour_limit=round(base.detour_limit * 1.40, 2),  # generous detour for exploration
        direction_penalty=base.direction_penalty,
        direction_threshold=base.direction_threshold,
        direction_min_passes=base.direction_min_passes,
        excluded_trail_types=base.excluded_trail_types,
    )

    return [
        ("popularity", "Meilleur communautaire", a),
        ("direct", "Plus direct (heatmap)", b),
        ("explorer", "Explorateur", c),
    ]


def make_fallback_variants(
    sport: str,
) -> list[tuple[str, str, "RoutingProfile"]]:
    """Return 3 alternative profiles for diversity fallback.

    Used when primary proposal variants produce overlapping results.
    Returns [(key, label_fr, RoutingProfile), ...] for:
      trail_biased — bonus trails/sentiers, wider detour
      low_slope — minimal slope penalty, tight detour
      exploratory — neutral (no heat/trail bonuses)
    """
    base = get_profile(sport)

    trail_biased = RoutingProfile(
        name=base.name,
        slope_penalty=base.slope_penalty,
        surface_factors=dict(base.surface_factors),
        surface_default=base.surface_default,
        excluded_highways=base.excluded_highways,
        heat_max_bonus=0.0,           # ignore heatmap — focus on trails
        trail_max_bonus=0.50,          # strong trail bonus
        detour_limit=round(base.detour_limit * 1.40, 2),  # allow 40% extra detour for trails
        direction_penalty=base.direction_penalty,
        direction_threshold=base.direction_threshold,
        direction_min_passes=base.direction_min_passes,
        excluded_trail_types=base.excluded_trail_types,
    )

    low_slope = RoutingProfile(
        name=base.name,
        slope_penalty=0.2,             # very low slope penalty
        surface_factors=dict(base.surface_factors),
        surface_default=base.surface_default,
        excluded_highways=base.excluded_highways,
        heat_max_bonus=0.0,            # no heatmap influence
        trail_max_bonus=0.0,
        detour_limit=round(base.detour_limit * 0.90, 2),  # tight detour → forces direct flat path
        direction_penalty=base.direction_penalty,
        direction_threshold=base.direction_threshold,
        direction_min_passes=base.direction_min_passes,
        excluded_trail_types=base.excluded_trail_types,
    )

    exploratory = RoutingProfile(
        name=base.name,
        slope_penalty=base.slope_penalty,
        surface_factors={s: 1.0 for s in base.surface_factors},  # neutral surface cost
        surface_default=1.0,
        excluded_highways=base.excluded_highways,
        heat_max_bonus=0.0,            # no heatmap
        trail_max_bonus=0.0,           # no trail bonus
        detour_limit=round(base.detour_limit * 1.30, 2),  # allow wider exploration
        direction_penalty=base.direction_penalty,
        direction_threshold=base.direction_threshold,
        direction_min_passes=base.direction_min_passes,
        excluded_trail_types=base.excluded_trail_types,
    )

    return [
        ("trail_biased", "Sentiers", trail_biased),
        ("low_slope", "Plat", low_slope),
        ("exploratory", "Exploration", exploratory),
    ]


def edge_cost(
    length_m: float,
    grade_pct: float,
    surface: str,
    highway: str,
    user_count: int,
    pass_count: int,
    on_trail: bool,
    trail_score: float,
    profile: RoutingProfile,
    trail_type: str = "",
) -> float:
    """Unified cost: L × S × U × A × H × T."""
    # DFCI <10% slope exemption: forestry roads are well-graded, slope data is noisy
    s = 1.0 if trail_type.upper() == "DFCI" and abs(grade_pct) < 10 else slope_factor(grade_pct, profile.slope_penalty)
    u = surface_factor(surface, profile)
    a = access_factor(highway, profile)
    if a == INF:
        return INF
    hs = compute_heat_score(effective_user_count(user_count, pass_count))
    h = heat_factor(hs, profile.heat_max_bonus)
    # Excluded trail types get no bonus (e.g. road shouldn't follow hiking trails)
    tt_upper = trail_type.upper() if trail_type else ""
    if tt_upper and tt_upper in profile.excluded_trail_types:
        t = 1.0  # no trail bonus for excluded trail types
    else:
        t = trail_factor(on_trail, trail_score, profile.trail_max_bonus)
    return length_m * s * u * a * h * t


def edge_cost_debug(
    length_m: float,
    grade_pct: float,
    surface: str,
    highway: str,
    user_count: int,
    pass_count: int,
    on_trail: bool,
    trail_score: float,
    profile: RoutingProfile,
    trail_type: str = "",
) -> dict:
    """Like edge_cost but returns per-factor breakdown for debugging."""
    s = 1.0 if trail_type.upper() == "DFCI" and abs(grade_pct) < 10 else slope_factor(grade_pct, profile.slope_penalty)
    u = surface_factor(surface, profile)
    a = access_factor(highway, profile)
    hs = compute_heat_score(effective_user_count(user_count, pass_count))
    h = heat_factor(hs, profile.heat_max_bonus)
    tt_upper = trail_type.upper() if trail_type else ""
    if tt_upper and tt_upper in profile.excluded_trail_types:
        t = 1.0
    else:
        t = trail_factor(on_trail, trail_score, profile.trail_max_bonus)
    final = INF if a == INF else length_m * s * u * a * h * t
    return {
        "L": round(length_m, 2),
        "S": round(s, 4),
        "U": round(u, 4),
        "A": "INF" if a == INF else round(a, 4),
        "H": round(h, 4),
        "heat_score": round(hs, 4),
        "T": round(t, 4),
        "final_cost": "INF" if final == INF else round(final, 2),
        "surface_type": surface,
        "slope_grade": round(grade_pct, 2),
        "highway_type": highway,
        "user_count": user_count,
        "on_trail": on_trail,
    }
