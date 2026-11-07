"""FIT file parser — converts Garmin .fit files to activity dicts.

Uses fitparse library to extract GPS coordinates, elevation, distance,
and activity metadata from binary FIT files (Garmin/Wahoo/etc).
"""
import hashlib
import io
import json
import logging
from datetime import datetime

from app.services.gpx import GPX_TRACK_TYPE_SKIP, _coord_is_valid

log = logging.getLogger(__name__)

# FIT-native out-of-scope sport values (fitparse decodes the `sport`/`sub_sport`
# enum to these strings). Mirrors the GPX skip-list philosophy ("skip beats
# pollute" — a wrong-sport trace on the SHARED heatmap is worse than a missed
# personal import). GPX_TRACK_TYPE_SKIP already covers swimming/skiing/rowing/
# kayaking/sailing/surf/skating/etc.; these are the FIT-specific extras. Default
# is still ACCEPT (unknown sport → the caller's sport cascade wins), same as GPX.
_FIT_EXTRA_SKIP: set[str] = {
    "training",            # generic indoor workout
    "fitness_equipment",   # treadmill / trainer / elliptical
    "motorcycling",
    "driving",
    "boating",
    "transition",          # triathlon transition — GPS scribble in a paddock
    "golf",
    "horseback_riding",
    "hunting",
    "fishing",
    "tactical",
    "sky_diving",
    "water_skiing",
    "wakeboarding",
    "stand_up_paddleboarding",
    "paddling",
    "rafting",
    "snowmobiling",
    "flying",
    # ── Indoor / virtual (data audit C2, 2026-11) ───────────────────────────
    # Exact fitparse `sub_sport` strings (fitparse 1.2.0 profile). Zwift writes
    # sport=cycling + sub_sport=virtual_activity; turbo trainers / bike-park
    # spin bikes write sub_sport=spin. Rouvy / Kinomap / FulGaz replay REAL
    # roads with real coordinates, so no bbox can catch them — the sub_sport
    # is the only signal. The GPX list only had "spinning" (not "spin") and no
    # "virtual_activity", so these reached the community heatmap as `road`.
    "virtual_activity",
    "spin",
    "indoor_cycling",
    "indoor_running",
    "indoor_walking",
    "indoor_rowing",
    "indoor_skiing",
    "treadmill",
    "elliptical",
    "stair_climbing",
    "cardio_training",
    "strength_training",
    "flexibility_training",
    # Motorised sub-sports
    "motocross",
    "atv",
    # Handcycle — skipped on every other path (Strava "Handcycle", GPX
    # "handcycling"); the FIT spelling is "hand_cycling".
    "hand_cycling",
}

# FIT ``file_id.manufacturer`` values (fitparse profile strings) that only ever
# produce SIMULATED rides — the recording app is the virtual platform itself.
# Belt-and-braces for files whose sport/sub_sport is a plain "cycling".
# Hardware trainer brands (tacx, elite, wahoo_fitness, saris, kinetic) are NOT
# listed: they also make outdoor head units / apps.
_FIT_VIRTUAL_MANUFACTURERS: set[str] = {
    "zwift",
    "trainer_road",
    "the_sufferfest",
    "virtualtraining",
    "bkool",
}


def _fit_skip_reason(fit) -> str | None:
    """Return a human skip reason if the FIT session sport/sub_sport is
    out-of-scope for a cycling/running/walking heatmap, else None. Checked
    against BOTH the GPX skip-list and the FIT-native extras, plus the
    ``file_id`` manufacturer (virtual-only platforms such as Zwift)."""
    for file_id in fit.get_messages("file_id"):
        maker = file_id.get_value("manufacturer")
        if maker and str(maker).lower() in _FIT_VIRTUAL_MANUFACTURERS:
            return f"FIT recorded by virtual platform '{str(maker).lower()}' — not a real outdoor ride"
    for session in fit.get_messages("session"):
        for field in ("sport", "sub_sport"):
            val = session.get_value(field)
            if not val:
                continue
            low = str(val).lower()
            if low in GPX_TRACK_TYPE_SKIP or low in _FIT_EXTRA_SKIP:
                return f"FIT sport '{low}' is out of scope for the community heatmap"
    return None


def parse_fit(content: bytes) -> dict:
    """Parse FIT bytes, return internal activity dict (same format as parse_gpx)."""
    import fitparse

    fit = fitparse.FitFile(io.BytesIO(content))

    coords: list[list[float]] = []
    distance_m = 0.0
    elevation_gain_m = 0.0
    prev_ele = None
    activity_date = None
    name = None
    invalid_point_count = 0

    # Extract records
    for record in fit.get_messages("record"):
        lat = record.get_value("position_lat")
        lon = record.get_value("position_long")
        ele = record.get_value("enhanced_altitude") or record.get_value("altitude")

        if lat is None or lon is None:
            continue

        # FIT uses semicircles (2^31 = 180°)
        lat_deg = lat * (180.0 / 2**31)
        lon_deg = lon * (180.0 / 2**31)

        # Drop out-of-range / (0,0) / NaN coords BEFORE they corrupt the
        # geometry + PostGIS dual-write (a no-fix (0,0) at recording start is
        # common on Garmin; NaN fails the range checks). Mirrors the GPX parser's
        # _coord_is_valid guard, which FIT previously lacked.
        if not _coord_is_valid(lat_deg, lon_deg, ele):
            invalid_point_count += 1
            continue

        if ele is not None:
            coords.append([lon_deg, lat_deg, ele])
            if prev_ele is not None and ele > prev_ele:
                elevation_gain_m += ele - prev_ele
            prev_ele = ele
        else:
            coords.append([lon_deg, lat_deg])

        # Get activity date from first record with timestamp
        if activity_date is None:
            ts = record.get_value("timestamp")
            if ts and isinstance(ts, datetime):
                activity_date = ts

    # Get distance from session summary
    for session in fit.get_messages("session"):
        d = session.get_value("total_distance")
        if d:
            distance_m = d
        n = session.get_value("sport")
        if n:
            name = n.capitalize()
        if not activity_date:
            ts = session.get_value("start_time")
            if ts and isinstance(ts, datetime):
                activity_date = ts

    # If no elevation data found, strip any partial 3rd element
    has_elevation = any(len(c) > 2 for c in coords)
    if not has_elevation:
        coords = [[c[0], c[1]] for c in coords]

    geojson = {"type": "LineString", "coordinates": coords} if coords else None

    # Infer sport from FIT sport field
    sport = _infer_sport(fit)

    result = {
        "name": name or "Garmin Activity",
        "geometry_geojson": json.dumps(geojson) if geojson else None,
        "distance_m": distance_m or None,
        "elevation_gain_m": elevation_gain_m or None,
        "file_hash": hashlib.sha256(content).hexdigest(),
        "coord_count": len(coords),
        "invalid_point_count": invalid_point_count,
        "activity_date": activity_date,
        "sport": sport,
    }

    # Out-of-scope FIT sport (swim/ski/indoor/motorised/…) → SKIP, never pollute
    # the shared heatmap. The GPX path stamps skip_reason/skip_code the same way;
    # every ingest caller (drain, /imports/files, /gpx/upload) short-circuits on
    # skip_reason. FIT previously had NO skip → a Garmin swim/virtual .fit fell
    # through to the form/fallback sport and landed on the road heatmap.
    skip = _fit_skip_reason(fit)
    if skip:
        result["skip_reason"] = skip
        result["skip_code"] = "GPX_TYPE_OUT_OF_SCOPE"
    return result


# FIT ``sub_sport`` (fitparse profile strings) → our sport, for the cycling
# family (sport=cycling / e_biking). Garmin's MTB / gravel / e-MTB activity
# profiles all write sport=cycling and carry the real discipline ONLY in
# sub_sport — reading `sport` alone filed every MTB / gravel ride as road.
_FIT_CYCLING_SUB_SPORT: dict[str, str] = {
    "mountain": "mtb",
    "downhill": "mtb",
    "e_bike_mountain": "mtb",
    "bmx": "mtb",
    "gravel_cycling": "gravel",
    "cyclocross": "gravel",
    "mixed_surface": "gravel",
    "road": "road",
    "commuting": "road",
    "e_bike_fitness": "road",  # Garmin's OUTDOOR e-bike profile
    "recumbent": "road",
    "track_cycling": "road",
    "track": "road",
    "street": "road",
}
# FIT sports whose session maps to the running layer whatever the sub_sport
# (indoor / treadmill sub-sports are skipped upstream by `_fit_skip_reason`).
# Hike / walk → running, same as the Strava catalog ("Hike"/"Walk") and the
# GPX map ("hiking"/"walking").
_FIT_RUNNING_SPORTS: frozenset[str] = frozenset({"running", "walking", "hiking"})


def _fit_sport_from_values(sport, sub_sport) -> str | None:
    """Map one FIT session's (sport, sub_sport) to our sport enum, or None
    when the pair carries no reliable information (generic / multisport /
    mountaineering / an enum value fitparse doesn't know and returns as a
    bare int). None NEVER becomes a default sport: the caller's cascade
    (activities.csv, user choice) decides, else the file is skipped."""
    if not sport or isinstance(sport, int):
        return None
    s = str(sport).lower()
    sub = None if sub_sport is None else str(sub_sport).lower()
    if s in ("cycling", "e_biking"):
        if sub is None or sub in ("generic", "all"):
            # Generic "Bike" profile: road — the same reading as Strava's
            # generic "Ride" (`classify_sport`). A more specific source (CSV
            # "Gravel Ride", the user's explicit choice) still refines it via
            # `resolve_sport_cascade`.
            return "road"
        if sub_sport is not None and isinstance(sub_sport, int):
            return None  # sub-sport newer than the fitparse profile → unknown
        return _FIT_CYCLING_SUB_SPORT.get(sub)
    if s in _FIT_RUNNING_SPORTS:
        return "running"
    return None


def _infer_sport(fit) -> str | None:
    """Infer our sport from the FIT session ``sport`` + ``sub_sport``.

    Returns None when the session carries no usable sport (absent, generic,
    or not a cycling/running/walking discipline we model) — the caller's
    cascade (`strava_utils.resolve_sport_cascade`: activities.csv, the
    user's explicit choice) decides, and with nothing left the file is
    SKIPPED, never defaulted.
    """
    for session in fit.get_messages("session"):
        sport = session.get_value("sport")
        if sport:
            return _fit_sport_from_values(sport, session.get_value("sub_sport"))
    return None  # no FIT sport field — let the caller's cascade decide
