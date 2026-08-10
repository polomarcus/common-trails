#!/usr/bin/env python3
"""Generate golden cost model test data for cross-language parity tests.

Imports the backend routing_profiles module and computes expected costs for
a comprehensive set of edge scenarios. Output is written to tests/golden-cost-model.json
and consumed by both backend (pytest) and frontend (tsx) parity tests.

Usage:
    cd backend && python -m scripts.generate_golden_costs
    # or from repo root:
    PYTHONPATH=backend python scripts/generate_golden_costs.py
"""

import json
import math
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.routing_profiles import (
    PROFILES,
    TRAIL_SCORES,
    compute_heat_score,
    compute_trail_score,
    edge_cost,
    get_profile,
    heat_factor,
    slope_factor,
    surface_factor,
    trail_factor,
)

INF = float("inf")

# ── Scenario definitions ─────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "flat_asphalt_road_no_heat",
        "description": "Baseline: flat asphalt road, no heatmap, no trail",
        "sport": "road",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "asphalt",
        "highway": "residential",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "flat_dirt_road",
        "description": "Dirt surface penalty on road profile",
        "sport": "road",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "flat_dirt_mtb",
        "description": "Dirt surface bonus on MTB profile",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "steep_asphalt_road",
        "description": "12% slope penalty on road profile (penalty=1.0)",
        "sport": "road",
        "length_m": 100.0,
        "grade_pct": 12.0,
        "surface": "asphalt",
        "highway": "secondary",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "steep_asphalt_mtb",
        "description": "12% slope penalty on MTB profile (penalty=0.6)",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 12.0,
        "surface": "asphalt",
        "highway": "secondary",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "downhill_dirt_gravel",
        "description": "Downhill -8% on gravel profile (abs*0.7 discount)",
        "sport": "gravel",
        "length_m": 100.0,
        "grade_pct": -8.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "popular_gravel_road",
        "description": "Heatmap bonus: 15 users on gravel",
        "sport": "gravel",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "gravel",
        "highway": "track",
        "user_count": 15,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "popular_dirt_offroad",
        "description": "Heatmap bonus: 10 users on offroad dirt",
        "sport": "offroad",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 10,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "dfci_trail_mtb",
        "description": "DFCI trail bonus on MTB (trail_score=1.0)",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "DFCI",
    },
    {
        "name": "dfci_trail_gravel",
        "description": "DFCI trail bonus on gravel (trail_score=1.0)",
        "sport": "gravel",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "DFCI",
    },
    {
        "name": "gr_trail_mtb",
        "description": "GR trail bonus on MTB (trail_score=0.80)",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "path",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "GR",
    },
    {
        "name": "pr_trail_running",
        "description": "PR trail bonus on running (trail_score=0.50)",
        "sport": "running",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "path",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "PR",
    },
    {
        "name": "popular_dfci_mtb",
        "description": "Heat + trail stacked: 10 users + DFCI on MTB",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 10,
        "on_trail": True,
        "trail_type": "DFCI",
    },
    {
        "name": "steep_popular_gravel",
        "description": "Slope + heat stacked: 8% grade + 20 users on gravel",
        "sport": "gravel",
        "length_m": 100.0,
        "grade_pct": 8.0,
        "surface": "gravel",
        "highway": "track",
        "user_count": 20,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "unknown_surface_offroad",
        "description": "Unknown surface tolerance on offroad (factor=1.0)",
        "sport": "offroad",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "unknown",
        "highway": "track",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "asphalt_penalty_offroad",
        "description": "Asphalt avoidance on offroad (factor=2.0)",
        "sport": "offroad",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "asphalt",
        "highway": "secondary",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "very_steep_road",
        "description": "20% slope capped at factor 2.0 on road",
        "sport": "road",
        "length_m": 100.0,
        "grade_pct": 20.0,
        "surface": "asphalt",
        "highway": "residential",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "excluded_highway_no_heat",
        "description": "Steps excluded on road with no heatmap → Infinity",
        "sport": "road",
        "length_m": 100.0,
        "grade_pct": 0.0,
        "surface": "asphalt",
        "highway": "steps",
        "user_count": 0,
        "on_trail": False,
        "trail_type": "none",
    },
    {
        "name": "dfci_8pct_mtb",
        "description": "DFCI 8% slope exempted (< 10%) → slope factor = 1.0",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 8.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "DFCI",
    },
    {
        "name": "dfci_12pct_mtb",
        "description": "DFCI 12% slope NOT exempted (>= 10%) → slope factor > 1.0",
        "sport": "mtb",
        "length_m": 100.0,
        "grade_pct": 12.0,
        "surface": "dirt",
        "highway": "track",
        "user_count": 0,
        "on_trail": True,
        "trail_type": "DFCI",
    },
]


def compute_expected(scenario: dict) -> dict:
    """Compute expected cost and per-factor breakdown for a scenario."""
    profile = get_profile(scenario["sport"])
    length_m = scenario["length_m"]
    grade_pct = scenario["grade_pct"]
    surface = scenario["surface"]
    highway = scenario["highway"]
    user_count = scenario["user_count"]
    on_trail = scenario["on_trail"]
    trail_type = scenario["trail_type"]

    trail_score = compute_trail_score(trail_type)

    cost = edge_cost(
        length_m=length_m,
        grade_pct=grade_pct,
        surface=surface,
        highway=highway,
        user_count=user_count,
        pass_count=0,
        on_trail=on_trail,
        trail_score=trail_score,
        profile=profile,
        trail_type=trail_type,
    )

    # Per-factor breakdown for debugging
    if trail_type.upper() == "DFCI" and abs(grade_pct) < 10:
        s = 1.0
    else:
        s = slope_factor(grade_pct, profile.slope_penalty)
    u = surface_factor(surface, profile)
    hs = compute_heat_score(user_count)
    h = heat_factor(hs, profile.heat_max_bonus)
    t = trail_factor(on_trail, trail_score, profile.trail_max_bonus)

    result = {
        **scenario,
        "trail_score": trail_score,
        "factors": {
            "slope": round(s, 6),
            "surface": round(u, 6),
            "heat_score": round(hs, 6),
            "heat": round(h, 6),
            "trail": round(t, 6),
        },
    }

    if cost == INF:
        result["expected_cost"] = "Infinity"
    else:
        result["expected_cost"] = round(cost, 6)

    return result


def main():
    output = {
        "version": 1,
        "description": "Golden cost model test data — generated by scripts/generate_golden_costs.py",
        "tolerance_pct": 1.0,
        "intentional_differences": [
            {
                "name": "excluded_highway_heatmap_override",
                "description": "Frontend returns distanceM * 2.5 when userCount > 0 on excluded highways; backend returns Infinity",
            },
            {
                "name": "direction_factor",
                "description": "Backend has direction_factor (MTB only); frontend does not implement it",
            },
        ],
        "scenarios": [],
    }

    for scenario in SCENARIOS:
        result = compute_expected(scenario)
        output["scenarios"].append(result)

    # Write to tests/golden-cost-model.json (repo root for frontend)
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    out_paths = [
        os.path.join(repo_root, "tests", "golden-cost-model.json"),
        os.path.join(repo_root, "backend", "tests", "golden-cost-model.json"),
    ]
    for out_path in out_paths:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)
            f.write("\n")

    print(f"Generated {len(output['scenarios'])} scenarios → {', '.join(out_paths)}")

    # Print summary
    for s in output["scenarios"]:
        cost_str = s["expected_cost"] if s["expected_cost"] == "Infinity" else f"{s['expected_cost']:.2f}"
        print(f"  {s['name']:30s} {s['sport']:8s} → {cost_str}")


if __name__ == "__main__":
    main()
