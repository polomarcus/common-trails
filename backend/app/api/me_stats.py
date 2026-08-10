"""Personal stats API — private, never exposes raw activity data.

GET /me/stats — personal distance, D+, activity count, coverage
GET /me/personal-bests — Strava-style personal records
"""
import logging
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import AuthenticatedUser, get_current_user
from app.config import expand_sport
from app.services import ingest as ingest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])

MONTH_NAMES_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

STRAVA_SPORT_LABELS: dict[str, str] = {
    "road": "Ride",
    "gravel": "Gravel Ride",
    "mtb": "Mountain Bike Ride",
    "offroad": "Off-road",
    "running": "Run",
}


class StatsResponse(BaseModel):
    activity_count: int
    total_distance_m: float
    total_elevation_gain_m: float
    unique_cells: int
    sport: str | None
    pending_gps_upgrade: int = 0  # Strava activities still on low-res polyline


class StatsBlock(BaseModel):
    activity_count: int
    total_distance_m: float
    total_elevation_gain_m: float
    unique_cells: int


class StatsBySportResponse(BaseModel):
    """One-shot per-sport + totals payload. Replaces N round-trips.

    Frontend stats page used to fire 6 ``/me/stats?sport=X`` calls in
    parallel — each opens a DB session for a full table scan. This
    collapses into a single GROUP BY query.
    """
    total: StatsBlock
    road: StatsBlock
    gravel: StatsBlock
    mtb: StatsBlock
    offroad: StatsBlock
    running: StatsBlock
    pending_gps_upgrade: int = 0


@router.get("/me/stats_by_sport", response_model=StatsBySportResponse)
async def me_stats_by_sport(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> StatsBySportResponse:
    """Return totals + per-sport breakdown in a single round-trip.

    Replaces the N-per-sport frontend pattern at ``/stats``. Uses one
    GROUP BY query against ``activities`` + one against ``activity_cells``
    rather than scanning the table N+1 times.
    """
    user_id = current_user.user_id
    blocks = ingest_service.get_user_activities_stats_by_sport(user_id)
    pending = ingest_service.get_pending_gps_upgrade_count(user_id)
    return StatsBySportResponse(
        total=StatsBlock(**blocks["total"]),
        road=StatsBlock(**blocks["road"]),
        gravel=StatsBlock(**blocks["gravel"]),
        mtb=StatsBlock(**blocks["mtb"]),
        offroad=StatsBlock(**blocks["offroad"]),
        running=StatsBlock(**blocks["running"]),
        pending_gps_upgrade=pending,
    )


@router.get("/me/stats", response_model=StatsResponse)
async def me_stats(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    sport: str | None = Query(None, description="road|gravel|mtb|offroad"),
    period: str | None = Query(None, description="30d|1y|all (filtering not yet implemented at service level)"),
) -> StatsResponse:
    """Return personal cycling stats (private).

    - sport=offroad: aggregates gravel + mtb
    - period: future filter (placeholder — returns all data for now)
    """
    user_id = current_user.user_id

    sports = expand_sport(sport) if sport else [None]
    if len(sports) > 1:
        # Aggregate multiple sub-sports (e.g. offroad → gravel + mtb)
        combined = {"activity_count": 0, "total_distance_m": 0, "total_elevation_gain_m": 0, "unique_cells": 0}
        for s in sports:
            st = ingest_service.get_user_stats(user_id, sport=s)
            for k in combined:
                combined[k] += st[k]
        return StatsResponse(**combined, sport=sport)

    stats = ingest_service.get_user_stats(user_id, sport=sport)
    pending = ingest_service.get_pending_gps_upgrade_count(user_id)
    return StatsResponse(
        activity_count=stats["activity_count"],
        total_distance_m=stats["total_distance_m"],
        total_elevation_gain_m=stats["total_elevation_gain_m"],
        unique_cells=stats["unique_cells"],
        sport=sport,
        pending_gps_upgrade=pending,
    )


# ── Personal Bests ──────────────────────────────────────────────────────────

class PersonalBestRecord(BaseModel):
    label: str
    sport: str
    value: float
    unit: str
    formatted: str
    category: str  # "longest" | "most_elevation" | "fastest"
    activity_id: str | None = None


class BestYear(BaseModel):
    year: int
    distance_km: float


class BestMonth(BaseModel):
    year: int
    month: int
    month_name: str
    distance_km: float


class PersonalBestsResponse(BaseModel):
    total_activities: int
    total_distance_km: float
    best_year: BestYear | None = None
    best_month: BestMonth | None = None
    records: list[PersonalBestRecord]


@router.get("/me/personal-bests", response_model=PersonalBestsResponse)
async def me_personal_bests(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> PersonalBestsResponse:
    """Return Strava-style personal bests computed from user activities."""
    user_id = current_user.user_id
    # Use the metadata-only projection — personal bests never need the
    # LineString geometry, just distance/elevation/date/sport/name.
    all_acts = ingest_service.get_user_activities_meta(user_id)

    total_activities = len(all_acts)
    total_distance_km = sum(a.get("distance_m") or 0 for a in all_acts) / 1000.0

    # ── Best year / best month ──
    year_km: dict[int, float] = defaultdict(float)
    month_km: dict[tuple[int, int], float] = defaultdict(float)
    for a in all_acts:
        date_str = a.get("activity_date")
        dist = a.get("distance_m") or 0
        if date_str and dist:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(date_str)
                year_km[dt.year] += dist / 1000.0
                month_km[(dt.year, dt.month)] += dist / 1000.0
            except (ValueError, TypeError):
                logger.warning("Skipping activity with unparseable date: %s", date_str)

    best_year = None
    if year_km:
        by = max(year_km, key=year_km.get)  # type: ignore[arg-type]
        best_year = BestYear(year=by, distance_km=round(year_km[by], 1))

    best_month = None
    if month_km:
        bm = max(month_km, key=month_km.get)  # type: ignore[arg-type]
        best_month = BestMonth(
            year=bm[0],
            month=bm[1],
            month_name=MONTH_NAMES_FR[bm[1] - 1] if 1 <= bm[1] <= 12 else str(bm[1]),
            distance_km=round(month_km[bm], 1),
        )

    records: list[PersonalBestRecord] = []

    # ── Longest activity per sport ──
    by_sport: dict[str, list[dict]] = defaultdict(list)
    for a in all_acts:
        by_sport[a.get("sport", "road")].append(a)

    for sport, acts in by_sport.items():
        longest = max(acts, key=lambda x: x.get("distance_m") or 0)
        dist_km = (longest.get("distance_m") or 0) / 1000.0
        if dist_km > 0:
            records.append(PersonalBestRecord(
                label=longest.get("name") or "Activité sans nom",
                sport=sport,
                value=round(dist_km, 1),
                unit="km",
                formatted=f"{dist_km:.1f} km",
                category="longest",
                activity_id=longest.get("id"),
            ))

    # ── Most elevation gain (overall) ──
    acts_with_elev = [a for a in all_acts if (a.get("elevation_gain_m") or 0) > 0]
    if acts_with_elev:
        top_elev = max(acts_with_elev, key=lambda x: x.get("elevation_gain_m") or 0)
        elev = top_elev.get("elevation_gain_m") or 0
        records.append(PersonalBestRecord(
            label=top_elev.get("name") or "Activité sans nom",
            sport=top_elev.get("sport", "road"),
            value=round(elev, 1),
            unit="m D+",
            formatted=f"{elev:.1f} m D+",
            category="most_elevation",
            activity_id=top_elev.get("id"),
        ))

    # ── Fastest average speed per sport ──
    for sport, acts in by_sport.items():
        acts_with_time = [
            a for a in acts
            if (a.get("moving_time") or 0) > 0 and (a.get("distance_m") or 0) > 0
        ]
        if acts_with_time:
            def _avg_speed(a: dict) -> float:
                return (a["distance_m"] / a["moving_time"]) * 3.6
            fastest = max(acts_with_time, key=_avg_speed)
            speed = _avg_speed(fastest)
            records.append(PersonalBestRecord(
                label=fastest.get("name") or "Activité sans nom",
                sport=sport,
                value=round(speed, 2),
                unit="km/h",
                formatted=f"{speed:.2f} km/h",
                category="fastest",
                activity_id=fastest.get("id"),
            ))

    return PersonalBestsResponse(
        total_activities=total_activities,
        total_distance_km=round(total_distance_km, 1),
        best_year=best_year,
        best_month=best_month,
        records=records,
    )
