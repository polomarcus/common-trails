"""Shared Strava utilities — sport mapping + polyline decoding.

Used by all four Strava ingest paths:
- `app/api/integrations_strava.py:_run_strava_import` (in-process bulk)
- `app/jobs/import_strava.py:run_import` (Cloud Run Job)
- `app/jobs/resync_strava.py:_resync_user` (monthly cron — being decommissioned)
- `app/api/internal_strava_webhook.py:_ingest_strava_activity` (push)

## Heatmap-pollution defense (see [[feedback_skip_beats_pollute_heatmap]])

Three states distinguished by the map:

- **mapped sport string** (e.g. "mtb") — ingest with that sport
- **None as VALUE in the dict** (key present) — Yoga / Workout / Virtual
  / Indoor / Swim / Ski / etc. The CALLER MUST SKIP — never fall back
  to a default sport, that's how Zwift's Watopia coords end up on the
  community road heatmap. Use `classify_strava_sport_or_skip(value)`
  which returns `None` for skip *and* unknown future types.
- **key absent** (dict.get returns None) — future Strava type we
  haven't seen. Conservatively treated as skip via the helper.

Bias: missing a real ride is private + fixable by user re-uploading;
polluting the heatmap is community-wide + propagated through PMTiles
cache. We err on the side of "miss it" every time.
"""

import unicodedata

# ── SINGLE SOURCE OF TRUTH for sport classification ──────────────────
#
# Every intake (CLI bulk import, UI GPX/ZIP upload, Strava webhook /
# bulk / resync, FIT cascade) routes through `classify_sport` so the
# SAME activity is classified identically no matter how it arrives.
# Previously the gravel/mtb name-keyword refinement (#430) lived ONLY
# in `scripts/import_strava_export.py`, so the UI-upload CSV path and
# the Strava live-sync path classified a generic "Ride" as `road` even
# when the name said "Gravel paradise" / "VTT" — starving gravel/mtb
# heat. The lookup map + name refinement now live here, used everywhere.
#
# The map below is the *complete* current catalog of Strava Activity Type
# values (snapshot 2026, English locale — Strava uses English regardless
# of UI locale). The keys cover BOTH the API PascalCase form
# ("MountainBikeRide", from the live Strava paths) AND the bulk-export
# `activities.csv` human-readable form ("Mountain Bike Ride") because
# the same classifier serves both — keys are matched case-insensitively
# with non-alphanumerics stripped (see `_norm_type`). Unknown future
# types resolve to None (skip), never to a default sport.
_STRAVA_SPORT_MAP: dict[str, str | None] = {
    # ── Cycling family (mappable) ──────────────────────────────────────
    "ride": "road",
    "ebikeride": "road",
    "roadride": "road",
    "gravelride": "gravel",
    "mountainbikeride": "mtb",
    "mountainbiking": "mtb",
    "emountainbikeride": "mtb",
    "velomobile": "road",
    # ── Running family (mappable) ──────────────────────────────────────
    "run": "running",
    "trailrun": "running",
    "hike": "running",
    "walk": "running",
    # ── Indoor / simulated GPS — explicit SKIP (heatmap-pollution) ────
    # Zwift uses fictional Watopia coordinates (~lat -11.6, lon 166.9).
    # Treadmill / indoor have no real GPS or locked-to-start coords.
    # All map to None so the caller short-circuits.
    "virtualride": None,
    "virtualrun": None,
    "treadmillrun": None,
    "indoorcycling": None,
    "indoorrunning": None,
    # ── Out of scope (don't classify; caller must skip, not fall through) ─
    "swim": None,
    "rowing": None,
    "kayaking": None,
    "standuppaddling": None,
    "alpineski": None,
    "backcountryski": None,
    "nordicski": None,
    "snowshoe": None,
    "snowboard": None,
    "iceskate": None,
    "inlineskate": None,
    "yoga": None,
    "crossfit": None,
    "weighttraining": None,
    "workout": None,
    "surfing": None,
    "kitesurf": None,
    "windsurf": None,
    "rockclimbing": None,
    "golf": None,
    "wheelchair": None,
    "handcycle": None,
    "elliptical": None,
    "stairstepper": None,
    # ── Localized Strava labels (French UI) ────────────────────────────
    # The reference export is English, but a bulk export / hand-made CSV
    # can carry the labels the French Strava UI displays. Keys are already
    # in `_norm_type` form (lower-case, accents folded, non-alphanumerics
    # stripped). Anything not listed stays unknown → skip.
    "velo": "road",
    "veloderoute": "road",
    "veloelectrique": "road",
    "vae": "road",
    "velogravel": "gravel",
    "gravel": "gravel",
    "vtt": "mtb",
    "velotoutterrain": "mtb",
    "vttelectrique": "mtb",
    "vtae": "mtb",
    "courseapied": "running",
    "trail": "running",
    "randonnee": "running",
    "marche": "running",
    "velovirtuel": None,
    "courseapiedvirtuelle": None,
    "natation": None,
    "skialpin": None,
    "skiderandonnee": None,
    "skidefond": None,
    "raquettes": None,
    "entrainement": None,
    "musculation": None,
    "aviron": None,
    "kayak": None,
    "canoe": None,
    "surf": None,
    "escalade": None,
    "elliptique": None,
    "patinageaglace": None,
    "roller": None,
    "fauteuilroulant": None,
}

# Generic cycling sports whose name can be refined into gravel/mtb.
# These resolve to "road" by type alone; if the activity NAME carries a
# gravel/mtb keyword, the refinement upgrades them. Running stays
# running — name-keyword refinement is cycling-only.
_NAME_REFINABLE: frozenset[str] = frozenset({"road"})


def _norm_type(activity_type: str) -> str:
    """Normalize a Strava Activity Type to the map key form.

    Lower-cases, folds accents and strips every non-alphanumeric char so
    the API "MountainBikeRide", the CSV "Mountain Bike Ride", hand-edited
    "mountain_bike_ride" / "E-Bike Ride" and a localized "Vélo électrique"
    all collapse to a stable ASCII key.
    """
    folded = unicodedata.normalize("NFKD", activity_type.lower())
    return "".join(c for c in folded if c.isascii() and c.isalnum())


def lookup_activity_type(activity_type: str | None) -> tuple[str | None, bool]:
    """Raw type lookup in the SSOT map, WITHOUT name refinement.

    Returns ``(sport, known)``: ``known`` is True when the type is in the
    catalog (``sport`` is then the mapped sport, or None for an explicit
    out-of-scope type the caller must skip); ``(None, False)`` means the
    type is unknown — no information.
    """
    if not activity_type:
        return None, False
    key = _norm_type(activity_type)
    if key in _STRAVA_SPORT_MAP:
        return _STRAVA_SPORT_MAP[key], True
    return None, False


# Sports a generic "road" classification may be refined into by a more
# specific, lower-priority source (see `resolve_sport_cascade`).
_SPECIFIC_CYCLING: frozenset[str] = frozenset({"gravel", "mtb"})


def resolve_sport_cascade(*candidates: str | None) -> str | None:
    """Pick the sport of an imported file from candidates ordered by
    authority (most authoritative first), e.g. ``(activities.csv sport,
    FIT/GPX sport, user-chosen sport)``.

    * The first candidate that is a valid sport wins…
    * …except a generic ``road`` (Strava "Ride", FIT cycling/generic):
      a LATER candidate that is gravel/mtb is more specific and wins
      (the CSV says "Ride" but the Garmin FIT says sub_sport=mountain;
      the user picked "gravel" for a FIT recorded on the generic Bike
      profile).
    * No valid candidate → None: the caller MUST skip the file. There is
      no default sport — an unknown sport never lands on a heatmap layer.
    """
    from app.config import VALID_SPORTS

    valid = [c for c in candidates if c in VALID_SPORTS]
    if not valid:
        return None
    first = valid[0]
    if first == "road":
        for later in valid[1:]:
            if later in _SPECIFIC_CYCLING:
                return later
    return first


def classify_sport(
    activity_type: str | None,
    activity_name: str | None = None,
) -> str | None:
    """SINGLE SOURCE OF TRUTH: map a Strava-style activity to our sport
    enum, or return None if the activity must be skipped.

    None means: do NOT ingest. Caller MUST short-circuit BEFORE any
    sport-fallback logic — falling back to a default pollutes the
    community heatmap (e.g. Zwift's Watopia coords on the road heatmap).
    Anything not in `_STRAVA_SPORT_MAP` (including future Strava types
    we haven't seen) returns None — safe-by-default.

    Resolution order:
      1. Type lookup. Fine sub-types win (GravelRide→gravel,
         MountainBikeRide→mtb); generic Ride/EBikeRide→road.
      2. Out-of-scope / unknown type → None (skip-beats-pollute).
      3. Name refinement (#430): when the TYPE resolved to a generic
         cycling sport ("road") AND `activity_name` is provided, a name
         containing "gravel" → gravel, or "vtt"/"mtb"/"mountain bike"/
         "mountain" → mtb. Strava bulk-export Activity Type is coarse —
         gravel/MTB rides are nearly all just "Ride" → road; the real
         signal is the Activity NAME. See
         project_single_user_routing_2026_06_14.

    Case-insensitive, whitespace/punctuation-tolerant. Never raises.
    """
    sport, _known = lookup_activity_type(activity_type)
    if sport in _NAME_REFINABLE and activity_name:
        n = activity_name.lower()
        if "gravel" in n:
            return "gravel"
        if "vtt" in n or "mtb" in n or "mountain bike" in n or "mountain" in n:
            return "mtb"
    return sport


# Public alias kept for backwards compatibility with existing call
# sites that import `STRAVA_SPORT_MAP` directly.
STRAVA_SPORT_MAP: dict[str, str | None] = _STRAVA_SPORT_MAP


def classify_strava_sport_or_skip(
    activity_type: str | None,
    activity_name: str | None = None,
) -> str | None:
    """Back-compat alias for `classify_sport`.

    Kept so existing call sites keep working; new code should call
    `classify_sport` directly. Now accepts the optional `activity_name`
    so the four Strava live-sync paths can pass it through for the #430
    name refinement.
    """
    return classify_sport(activity_type, activity_name)


def decode_polyline(encoded: str | None) -> list[list[float]]:
    """Decode Google encoded polyline → [[lon, lat], ...] (GeoJSON order)."""
    if not encoded:
        return []
    index, lat, lng = 0, 0, 0
    result: list[list[float]] = []
    while index < len(encoded):
        shift, result_val = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result_val |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result_val >> 1) if result_val & 1 else result_val >> 1
        lat += dlat

        shift, result_val = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result_val |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result_val >> 1) if result_val & 1 else result_val >> 1
        lng += dlng

        result.append([lng / 1e5, lat / 1e5])
    return result
