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


class _TolerantFitMessage:
    """fitparse-compatible view (``get_value``) of a fitdecode data message."""

    __slots__ = ("_msg",)

    def __init__(self, msg):
        self._msg = msg

    def get_value(self, name):
        val = self._msg.get_value(name, fallback=None)
        # fitdecode yields tz-aware UTC datetimes; fitparse (primary path)
        # yields naive UTC — keep the parse_fit output contract identical.
        if isinstance(val, datetime) and val.tzinfo is not None:
            from datetime import UTC

            val = val.astimezone(UTC).replace(tzinfo=None)
        return val


class _TolerantFitFile:
    """Minimal fitparse.FitFile stand-in (``get_messages``) backed by fitdecode.

    Fallback ONLY: fitparse 1.2.0 (unmaintained) aborts the WHOLE file with
    ``FitParseError: Invalid field size 1 for type 'uint32' (expected a
    multiple of 4)`` when a definition message declares a field whose size is
    not a multiple of its base type — recent Garmin firmwares do this, usually
    in undocumented messages unrelated to GPS. The FIT spec says to read such a
    field as raw bytes; fitdecode (maintained fork of fitparse, same profile
    strings) does exactly that with ``error_handling=IGNORE`` (the other
    IGNORE relaxations only touch developer-field metadata). CRC is still
    enforced, and truncated/corrupt files still raise.
    """

    def __init__(self, content: bytes):
        import fitdecode

        by_name: dict[str, list[_TolerantFitMessage]] = {}
        with fitdecode.FitReader(
            io.BytesIO(content),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.IGNORE,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage):
                    by_name.setdefault(frame.name, []).append(_TolerantFitMessage(frame))
        self._by_name = by_name

    def get_messages(self, name):
        return iter(self._by_name.get(name, ()))


def _open_fit(content: bytes):
    """Fully parse FIT bytes with fitparse; on ``FitParseError`` retry with the
    tolerant fitdecode reader. If the fallback fails too, the ORIGINAL fitparse
    error is re-raised, so callers (drain → member ``failed``, upload → 422)
    see exactly the exceptions they did before."""
    import fitparse

    fit = fitparse.FitFile(io.BytesIO(content))
    try:
        fit.parse()  # eager: surface mid-file errors here, not mid-loop
        return fit
    except fitparse.FitParseError as exc:
        try:
            tolerant = _TolerantFitFile(content)
        except Exception:
            raise exc from None
        log.info("fitparse rejected FIT (%s); recovered with fitdecode", exc)
        return tolerant


def parse_fit(content: bytes) -> dict:
    """Parse FIT bytes, return internal activity dict (same format as parse_gpx)."""
    fit = _open_fit(content)

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


def _infer_sport(fit) -> str | None:
    """Infer sport type from the FIT session sport field.

    Returns None when the device session carries NO sport field — so the
    caller's cascade (CSV / Activity Name hint, form-supplied sport) can
    win instead of being shadowed by a hard "road" default. A FIT with an
    explicit sport still maps as before. See the `/imports/files` cascade
    in `app/api/imports.py` and the CLI cascade in
    `scripts/import_strava_export.py`.
    """
    for session in fit.get_messages("session"):
        sport = session.get_value("sport")
        if sport:
            sport_lower = str(sport).lower()
            if sport_lower in ("cycling", "road_cycling"):
                return "road"
            if sport_lower in ("gravel_cycling",):
                return "gravel"
            if sport_lower in ("mountain_biking", "mtb"):
                return "mtb"
            if sport_lower in ("running", "trail_running"):
                return "running"
            if "cycling" in sport_lower:
                return "gravel"  # default cycling → gravel
            if "running" in sport_lower or "trail" in sport_lower:
                return "running"
    return None  # no FIT sport field — let the caller's cascade decide
