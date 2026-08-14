"""GPX parsing and export utilities."""
import gzip
import hashlib
import io
import json
import os
import zipfile

# Harden the stdlib XML parsers BEFORE importing gpxpy. gpxpy uses
# `xml.etree.ElementTree` under the hood, which is DTD-vulnerable per
# Python's own docs (billion-laughs entity expansion can OOM the
# worker on a tiny crafted GPX). `defusedxml.defuse_stdlib()`
# monkey-patches the stdlib modules so any consumer — including
# third-party libraries we don't control — gets the hardened parsers.
# Side effect: external entity expansion is disabled. Real GPX files
# don't use DTDs / external entities, so no legitimate file is
# affected. Audit 2026-05-29 GPX-S2.3.
import defusedxml

defusedxml.defuse_stdlib()

import gpxpy  # noqa: E402  — must follow defuse_stdlib()
import gpxpy.gpx  # noqa: E402
import numpy as np  # noqa: E402

# ── Elevation smoothing constants ──────────────────────────────────────────
#
# Garmin / Wahoo barometric altimeters drift ±2 m at 1 Hz sampling on a
# flat ride — naive monotonic-sum D+ accumulates 200-400 m of phantom
# elevation over 50 km. Strava-class smoothing combines:
#
#   1. Rolling median (window=11 samples ≈ 11 s on a 1 Hz GPS) to kill
#      single-point outliers without blunting genuine climbs.
#   2. Noise threshold (3 m) — only accumulate gains ≥ this delta from the
#      last baseline. Filters out the residual ±1-2 m wobble.
#
# Applied to the AGGREGATE elevation_gain_m only. The stored `coords` keep
# the raw <ele> values per Crouzet methodology (project_crouzet_methodology) —
# the renderer plots reality, the analytics gives an honest estimate.
_ELE_SMOOTH_WINDOW = 11
_ELE_NOISE_THRESHOLD_M = 3.0


def _smoothed_elevation_gain(elevations: list[float | None]) -> float:
    """Return D+ from a noise-filtered elevation series.

    Drops None entries (gaps in <ele>) and applies rolling-median + noise
    threshold to the contiguous remainder. Returns 0.0 if fewer than 3
    non-None points remain.
    """
    valid = [e for e in elevations if e is not None]
    if len(valid) < 3:
        return 0.0
    arr = np.asarray(valid, dtype=float)
    n = len(arr)
    if n >= _ELE_SMOOTH_WINDOW:
        half = _ELE_SMOOTH_WINDOW // 2
        smoothed = np.empty(n, dtype=float)
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            smoothed[i] = np.median(arr[lo:hi])
    else:
        # too few points to median-smooth; fall back to noise threshold only
        smoothed = arr
    gain = 0.0
    baseline = smoothed[0]
    for ele in smoothed[1:]:
        delta = ele - baseline
        if delta >= _ELE_NOISE_THRESHOLD_M:
            gain += delta
            baseline = ele
        elif delta <= -_ELE_NOISE_THRESHOLD_M:
            baseline = ele
    return float(gain)


# Mapping of GPX `<trk><type>` values → our `sport` enum.
#
# Garmin Connect exports use granular types (`mountain_biking`,
# `gravel_cycling`, `road_cycling`, …) — those map 1:1. Strava exports
# collapse all cycling variants to plain `cycling` (we return None for
# those, and the caller falls back to the form-supplied `sport`). See
# `.claude/agents/ingest-pipeline.md` § GPX sport classification.
#
# Maintenance: keep this aligned with `sport_enum` in `db/models.py`.
# Unknown / non-cycling-non-running types return None so the caller's
# fallback path stays authoritative.


# ── Strava bulk-export activities.csv → sport mapping ────────────────────
#
# Strava archives ship with an `activities.csv` at root whose
# "Activity Type" column carries the sport label the user picked, and an
# "Activity Name" column whose text is the real gravel/mtb signal
# (Activity Type collapses nearly all cycling to "Ride" → road).
#
# Classification is delegated to the SINGLE SOURCE OF TRUTH
# `app.services.strava_utils.classify_sport` so the CSV path, the CLI
# bulk importer, and the four Strava live-sync paths all agree on the
# same (type, name) → sport mapping. The old local
# `STRAVA_CSV_TYPE_TO_SPORT` map was deleted to remove the drift that
# let a "Gravel paradise" Ride classify as road on this path while the
# CLI classified it as gravel.
from app.services.strava_utils import classify_sport as _classify_sport


def classify_sport_from_strava_csv_type(
    activity_type: str | None,
    activity_name: str | None = None,
) -> str | None:
    """Map a Strava `activities.csv` "Activity Type" cell (+ optional
    "Activity Name" for the gravel/mtb name refinement) to our sport
    enum, or None if the type is out-of-scope. Delegates to the SSOT
    `strava_utils.classify_sport`. Case-insensitive. Never raises.
    """
    return _classify_sport(activity_type, activity_name)


def _normalize_archive_path(path: str) -> str:
    """Canonical lookup key for matching CSV `Filename` to ZIP member.

    Strava archives are normally lowercase + `activities/<id>.gpx`, but
    a user who decompresses + re-zips on a case-preserving filesystem
    (macOS, Windows) can end up with `Activities/100.GPX` in the CSV
    and `activities/100.gpx` in the ZIP (or vice versa). The lookup is
    keyed on the normalized form so the skip-gate fires correctly even
    after hand-editing.

    Normalization steps:
      * strip leading/trailing whitespace
      * strip leading `./`, `/` (some tools prepend these)
      * lowercase
      * strip the `.gz` suffix so an older `.gpx.gz` archive whose CSV
        still says `activities/100.gpx.gz` matches the decompressed
        `activities/100.gpx` we end up reading.
    """
    s = path.strip().lstrip("/").removeprefix("./")
    s = s.lower()
    if s.endswith(".gz"):
        s = s[:-3]
    return s


def parse_strava_activities_csv(csv_bytes: bytes) -> dict[str, str | None]:
    """Parse `activities.csv` from a Strava bulk-export ZIP.

    Returns ``{filename → sport-or-None}`` with three meaningful states:

      * **value = our sport enum string** ("road" / "mtb" / …) — the
        Activity Type maps to a heatmap-relevant sport. Use it.
      * **value = None** — the row exists but the Activity Type is
        out-of-scope ("Yoga", "Workout", "Alpine Ski", …) OR is an
        unknown future Strava type. The CALLER MUST SKIP THIS FILE,
        not fall back to a form-supplied sport. Doing so would pollute
        the wrong heatmap with e.g. an indoor-yoga GPS scribble.
      * **key absent** — the file isn't in the CSV at all. Fall back
        to the in-GPX <trk><type> hint or form-supplied sport.

    The skip-on-unknown rule deliberately errs on the side of "miss an
    import" rather than "pollute the heatmap" — bias chosen because the
    heatmap is community-shared (one bad upload affects everyone) while
    a missed import is private and trivially recoverable (re-upload
    after picking a sport explicitly).

    Resilient to UTF-8 BOM (Strava emits one), missing/renamed columns
    (returns empty dict, no raise), malformed CSV (same), and rows with
    no Filename (skipped). Empty bytes → empty dict.
    """
    if not csv_bytes:
        return {}
    import csv as _csv
    import io as _io

    out: dict[str, str | None] = {}
    try:
        # `utf-8-sig` strips an optional leading BOM; falls back to
        # plain utf-8 transparently when no BOM is present.
        text = csv_bytes.decode("utf-8-sig", errors="replace")
        reader = _csv.DictReader(_io.StringIO(text))
        for row in reader:
            filename = (row.get("Filename") or "").strip()
            activity_type = (row.get("Activity Type") or "").strip()
            # The Activity Name is the real gravel/mtb signal: Strava
            # collapses nearly all cycling to "Ride" (→ road) so the
            # name keyword ("Gravel ...", "VTT ...") is what upgrades a
            # generic Ride to gravel/mtb. Pass it to the SSOT classifier.
            activity_name = (row.get("Activity Name") or "").strip()
            if not filename:
                continue
            # Store every row regardless — out-of-scope rows store None
            # so the caller distinguishes "skip this" from "unknown,
            # fall through" (key absent).
            out[filename] = classify_sport_from_strava_csv_type(
                activity_type, activity_name,
            )
    except Exception:
        return out
    return out


GPX_TRACK_TYPE_TO_SPORT: dict[str, str | None] = {
    # ── Garmin Connect cycling family ──────────────────────────────────
    "mountain_biking": "mtb",
    "mountain biking": "mtb",
    "gravel_cycling": "gravel",
    "gravel cycling": "gravel",
    "road_cycling": "road",
    "road cycling": "road",
    "cyclocross": "gravel",
    "bmx": "mtb",
    # ── Strava-export variants (PascalCase lower-cased + underscore). ─
    # Strava emits these as the GPX `<trk><type>` when re-exporting,
    # NOT just in the activities.csv. Without explicit entries here a
    # `gravel_ride` GPX would fall through to the form-supplied sport.
    "gravel_ride": "gravel",
    "mountain_bike_ride": "mtb",
    "mountainbikeride": "mtb",
    "e_bike_ride": "road",  # e-bike still rolls on road geometry
    "ebikeride": "road",
    "e_mountain_bike_ride": "mtb",
    "emountainbikeride": "mtb",
    # ── Garmin / Strava generic — too coarse, defer to caller ─────────
    "cycling": None,
    "ride": None,
    # ── Running family ────────────────────────────────────────────────
    "running": "running",
    "trail_running": "running",
    "trail running": "running",
    "trail_run": "running",
    "track_running": "running",
    "run": "running",
    # ── Hike / walk: no dedicated sport, map to nearest ───────────────
    "walking": "running",
    "hiking": "running",
    "hike": "running",
    "walk": "running",
}


# Explicit skip set — known out-of-scope types that MUST NOT fall
# through to a form-supplied sport. Treadmill / indoor / virtual GPS
# data is locked to a single point or fictional coordinates (Zwift
# Watopia ~lat -11.6, lon 166.9 — South Pacific). Mapping any of
# these to "running" or "road" would pin the heatmap on a treadmill
# or a fake island. See [[feedback_skip_beats_pollute_heatmap]].
#
# Includes sports we simply don't model — swim/ski/etc. — to avoid
# the user dropping a yoga.gpx with sport=mtb selected in the form
# and polluting the MTB heatmap.
GPX_TRACK_TYPE_SKIP: set[str] = {
    # Indoor / simulated GPS — Zwift, treadmill, etc.
    "treadmill_running",
    "treadmill",
    "virtual_ride",
    "virtual ride",
    "virtual_run",
    "virtual run",
    "indoor_cycling",
    "indoor cycling",
    "indoor_running",
    "stationary_cycling",
    "trainer",
    "spinning",
    # Snow sports — wrong heatmap entirely
    "snowshoeing",
    "alpine_skiing",
    "alpine skiing",
    "downhill_skiing",
    "cross_country_skiing",
    "nordic_skiing",
    "backcountry_skiing",
    "snowboarding",
    "ice_skating",
    "ice skating",
    "inline_skating",
    # Water sports — global ocean heatmap nope
    "swimming",
    "open_water_swimming",
    "lap_swimming",
    "rowing",
    "kayaking",
    "canoeing",
    "stand_up_paddling",
    "sup",
    "surfing",
    "kitesurfing",
    "windsurfing",
    "sailing",
    # Strength / studio — GPS scribble of someone moving around a room
    "yoga",
    "pilates",
    "crossfit",
    "weight_training",
    "strength_training",
    "workout",
    "elliptical",
    "stair_stepper",
    "stair_climbing",
    "rock_climbing",
    "climbing",
    "bouldering",
    "golf",
    # Wheelchair / handcycle — distinct sport that doesn't fit our
    # cycling/running enum; revisit if we add a dedicated bucket
    "wheelchair",
    "handcycle",
    "handcycling",
    # ── Motorised — NOT cycling, would pollute road heatmap at 90km/h ──
    # The "skip-beats-pollute" review specifically flagged motorcycle GPS
    # as a heatmap pollution vector — a 90 km/h motorcycle ride uploaded
    # as `sport=road` would skew the popularity scoring on every road
    # segment it touches. Same risk class as Zwift Watopia coords.
    "motorcycling",
    "motorbike",
    "motorcycle",
    "moto",
    "scooter",
    "e_scooter",
    "electric_scooter",
    "motor_vehicle",
    "atv",
    "quad",
    "snowmobile",
    "driving",
    "car",
    # Skateboarding — could land on cycling heatmap but is a different
    # mode of transport with very different popularity dynamics
    "skateboarding",
    "skateboard",
}


def classify_sport_from_gpx_type(track_type: str | None) -> str | None:
    """Map a GPX `<trk><type>` string to our sport enum, or None if the
    type is generic / unknown.

    Case-insensitive lookup. Returns None when:
    - track_type is None or empty
    - it's a generic value like `cycling` (Strava export — caller must
      fall back to form-supplied sport or activities.csv lookup)
    - it's a sport we don't model (e.g. swimming, kayaking)

    The caller is responsible for the fallback. This function never
    raises.

    **DEPRECATED**: prefer ``gpx_track_type_skip_or_sport`` which
    distinguishes "in-skip-set" from "fall-through" — falling through
    on a yoga GPX pollutes the heatmap when the user picked MTB on
    the form.
    """
    if not track_type:
        return None
    return GPX_TRACK_TYPE_TO_SPORT.get(track_type.lower().strip())


def gpx_track_type_skip_or_sport(track_type: str | None) -> tuple[str | None, bool]:
    """Three-state classifier for GPX `<trk><type>`.

    Returns ``(sport, should_skip)``:
      * ``(sport_str, False)`` — mapped sport, ingest with it.
      * ``(None, True)`` — type is explicitly out-of-scope (yoga,
        swim, ski, treadmill, virtual ride…). Caller MUST skip the
        file. Falling back to a form-supplied sport would pollute the
        heatmap.
      * ``(None, False)`` — no information (generic "cycling",
        unknown type, or empty). Caller falls through to the next
        cascade layer (FIT field, activities.csv hint, form sport).

    Case-insensitive, whitespace-stripped match. Never raises.

    Uses the same skip-beats-pollute bias as the Strava-CSV path
    (`classify_strava_sport_or_skip` in `strava_utils.py`): if a hint
    is ambiguous fall through, if it's *explicitly* out-of-scope
    skip — never let form-sport take over on an out-of-scope file.
    """
    if not track_type:
        return None, False
    key = track_type.lower().strip()
    if key in GPX_TRACK_TYPE_SKIP:
        return None, True
    return GPX_TRACK_TYPE_TO_SPORT.get(key), False


def _coord_is_valid(lat: float, lon: float, ele: float | None) -> bool:
    """Reject out-of-range coordinates that would corrupt downstream cell/edge
    indexing. Corrupt firmware data has been observed in real-world Strava
    exports (cycling activity with lat=0 lon=0, treadmill activity with
    elevation=10000 m, etc.). Out-of-range points are silently dropped at
    the parser inner loop; surface `invalid_point_count` on the return dict
    so the caller can surface it in the UI later if needed.
    """
    if not (-90.0 <= lat <= 90.0):
        return False
    if not (-180.0 <= lon <= 180.0):
        return False
    # -500 covers Dead Sea / Death Valley; 9000 covers Everest summit + buffer.
    return not (ele is not None and not (-500.0 <= ele <= 9000.0))


def parse_gpx(content: bytes) -> dict:
    """Parse GPX bytes, return internal activity dict.

    Drops out-of-range coordinates (lat/lon/elevation outside plausible
    ranges) silently and exposes the count via `invalid_point_count` on
    the return dict.
    """
    gpx = gpxpy.parse(io.BytesIO(content))

    coords: list[list[float]] = []
    distance_m = 0.0
    elevations: list[float | None] = []  # for post-pass smoothed D+
    prev_point = None
    all_have_elevation = True  # flips false on first point without <ele>
    invalid_point_count = 0

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if not _coord_is_valid(point.latitude, point.longitude, point.elevation):
                    invalid_point_count += 1
                    continue
                if point.elevation is not None:
                    coords.append([point.longitude, point.latitude, point.elevation])
                    elevations.append(point.elevation)
                else:
                    coords.append([point.longitude, point.latitude])
                    elevations.append(None)
                    all_have_elevation = False
                if prev_point is not None:
                    distance_m += point.distance_3d(prev_point) or point.distance_2d(prev_point) or 0.0
                prev_point = point

    # Compute D+ from the smoothed elevation series (Strava-style: rolling
    # median + 3 m noise threshold). The previous monotonic sum-on-positive-
    # delta over raw GPX inflated D+ by 200-400 m on 50 km flat rides because
    # the 1 Hz baro samples wobble ±2 m every second.
    elevation_gain_m = _smoothed_elevation_gain(elevations)

    # Coordinates must be uniformly 2D OR uniformly 3D — never mixed.
    # Mixed shapes were the reason every backend loop had to use
    # ``point[0]/point[1]`` indexing instead of unpacking; flattening
    # to 2D when any point lacks elevation gives downstream code a
    # consistent contract. Only emit 3D when EVERY point has <ele>.
    if not all_have_elevation:
        coords = [[c[0], c[1]] for c in coords]

    geojson = {"type": "LineString", "coordinates": coords} if coords else None

    name = None
    track_type: str | None = None
    for track in gpx.tracks:
        if track.name:
            name = track.name
        if track.type and track_type is None:
            track_type = track.type
        if name and track_type:
            break
    # Resolve sport from the GPX `<trk><type>` if the source was granular
    # enough (Garmin Connect: `mountain_biking`, `gravel_cycling`, …).
    # Strava export collapses to `cycling` → sport_from_gpx is None and the
    # caller's form-supplied sport stays authoritative.
    #
    # Three-state classifier: in addition to `sport_from_gpx`, we stamp
    # `skip_reason` when the type is *explicitly* out-of-scope (yoga,
    # swim, ski, treadmill, virtual ride). The caller already short-
    # circuits on `skip_reason` (added for the CSV path in PR #339) so
    # extending the GPX path to skip too is just stamping the same key.
    sport_from_gpx, _gpx_should_skip = gpx_track_type_skip_or_sport(track_type)

    # Extract activity date from first trackpoint or GPX metadata.
    # Reject implausible timestamps (corrupt FIT firmware can emit
    # 1970-01-01 or 2099-01-01); leaving them as-is breaks the
    # cross-provider dedup query in ingest_activity.
    import datetime as _dt
    _now = _dt.datetime.now(_dt.UTC)
    _min_year = 2000
    _max_year = _now.year + 1

    def _plausible(t) -> bool:
        if t is None:
            return False
        try:
            return _min_year <= t.year <= _max_year
        except Exception:
            return False

    activity_date = None
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if _plausible(point.time):
                    activity_date = point.time
                    break
            if activity_date:
                break
        if activity_date:
            break
    if not activity_date and _plausible(gpx.time):
        activity_date = gpx.time

    return {
        "name": name or gpx.name or "Unnamed track",
        "geometry_geojson": json.dumps(geojson) if geojson else None,
        "distance_m": distance_m or None,
        "elevation_gain_m": elevation_gain_m or None,
        "file_hash": hashlib.sha256(content).hexdigest(),
        "coord_count": len(coords),
        "invalid_point_count": invalid_point_count,
        "activity_date": activity_date,
        # The raw `<trk><type>` value, for callers that want to do their
        # own classification (e.g. log unknown values). May be None.
        "track_type": track_type,
        # Resolved sport, or None if the GPX type was too coarse / unknown
        # to map. Callers that already received a form-supplied sport
        # should treat this as: "if non-None, prefer it; otherwise keep
        # the form value."
        "sport_from_gpx": sport_from_gpx,
        # Optional skip-reason — set when `<trk><type>` is *explicitly*
        # out-of-scope (yoga, swim, ski, treadmill, virtual ride…).
        # `imports.py` + `gpx_upload.py` short-circuit on this BEFORE
        # the sport cascade so a `yoga.gpx` uploaded with `sport=mtb`
        # selected on the form doesn't pollute the MTB heatmap.
        #
        # `skip_code` is a stable enum for the frontend to localise +
        # match on (don't string-match the French/English `skip_reason`).
        # `skip_track_type` is the raw `<trk><type>` value, bound to
        # 64 chars + control-char-stripped so a malformed GPX with a
        # multi-MB or newline-containing type field can't blow up
        # downstream rendering / log lines.
        **(
            {
                "skip_reason": (
                    f"GPX <trk><type>={(_sanitize_track_type(track_type))!r} "
                    "is out-of-scope (yoga/swim/ski/virtual/indoor/motorised)"
                ),
                "skip_code": "GPX_TYPE_OUT_OF_SCOPE",
                "skip_track_type": _sanitize_track_type(track_type),
            }
            if _gpx_should_skip
            else {}
        ),
    }


def _sanitize_track_type(value: str | None) -> str:
    """Bound + scrub a raw `<trk><type>` for safe logging / rendering.

    Strips control chars (newlines / NUL / etc.) and caps at 64 chars.
    The classifier itself doesn't care because it already
    lowercased+stripped before lookup, but the value is what we put
    into error messages and Sentry tags."""
    if not value:
        return ""
    # Drop non-printable control chars (newlines, NUL, etc.).
    cleaned = "".join(c for c in value if c.isprintable() and c != "\t")
    return cleaned[:64]


# ── Zip-bomb defence ──────────────────────────────────────────────────
#
# A 50 MB zip of null-padded XML can decompress to multi-GB and OOM the
# Cloud Run worker (1 GiB memory). Caps applied at three layers:
#
#   1. MAX_ZIP_MEMBERS: hard cap on the number of INGESTIBLE files
#      (.gpx/.fit ± .gz — see is_ingestible_zip_member) inside the zip,
#      INCLUDING files found by recursing one level into nested zips
#      (Garmin "Export All" nests all activities in an inner zip). A
#      Strava export is ~1400-3000 activities, but a REAL Garmin "Export
#      All" from a multi-year user is 6000+ .fit (measured: Paul's = 6277),
#      so the cap is 20000 — well above any real personal history, still a
#      zip-bomb backstop. Media/photos are never read, so they don't count.
#   2. MAX_ZIP_TOTAL_UNCOMPRESSED: sum of INGESTIBLE members'
#      uncompressed sizes. Media members are excluded for the same
#      never-read reason (a real export is dominated by photos/videos,
#      which pushed legitimate archives over the old all-members cap).
#      Default 4 GB: MAX_ARCHIVE_UPLOAD_BYTES advertises up to a 2 GB
#      *compressed* archive, and GPX text legitimately compresses ~2-4x,
#      so the decompressed-total ceiling must sit above the compressed
#      one. RAM safety does NOT depend on this cap — members are read
#      ONE at a time under the per-member cap (3); this total cap only
#      bounds aggregate work per archive.
#   3. MAX_ZIP_MEMBER_UNCOMPRESSED: per-member cap matching the
#      single-file MAX_GPX_SIZE so one zip entry can't masquerade as
#      a giant GPX. This is the cap that actually guards every
#      `zf.read()`, unchanged and NOT relaxed.
#
# Member names are also rejected if they contain `..` or start with `/`
# to prevent path-traversal pollution in downstream archival.
# 1 + 2 are env-tunable (like MAX_ARCHIVE_UPLOAD_BYTES) so ops can admit
# a genuinely huge export without a deploy.
MAX_ZIP_MEMBERS = int(os.environ.get("MAX_ZIP_MEMBERS", "20000"))
MAX_ZIP_TOTAL_UNCOMPRESSED = int(
    os.environ.get("MAX_ZIP_TOTAL_UNCOMPRESSED", str(4 * 1024 * 1024 * 1024))
)  # 4 GB
MAX_ZIP_MEMBER_UNCOMPRESSED = int(
    os.environ.get("MAX_GPX_SIZE_BYTES", str(25 * 1024 * 1024))
)  # 25 MB (matches MAX_GPX_SIZE)

# Single-file GPX caps. These live here (the parser layer) rather than
# in the HTTP handler because every entry point that calls `parse_gpx`
# must enforce them — HTTP upload, ZIP/bulk import, CLI bulk folder,
# Strava webhook path. Importing from `app.api.gpx_upload` was the
# wrong layer order (CLI → API) AND pulled the FastAPI router into
# any caller; the CLI bulk-import Cloud Run Job had no reason to.
# Audit 2026-05-27 PR #347 review S2.
# 25 MB (was 10 MB — real Garmin exports of a single long ride routinely run
# 12-20 MB thanks to 1 s sampling + fat TrackPointExtension; 10 MB 413'd them).
# Safely under Cloud Run's 32 MiB request-body cap; the 100k coord cap below is
# the actual dense-coordinate DoS defence, independent of byte size.
MAX_GPX_SIZE = int(os.environ.get("MAX_GPX_SIZE_BYTES", str(25 * 1024 * 1024)))
MAX_GPX_COORDS = 100_000  # dense-coord DoS defence (a small GPX can still pack 5M+ points)


class ZipBombError(ValueError):
    """Zip violates one of the defensive caps in parse_zip_of_gpx."""


def is_junk_zip_member(name: str) -> bool:
    """macOS Archive Utility artefacts inside a re-zipped export.

    A user who unzips their Strava export and right-click → Compress the
    folder gets a ``__MACOSX/`` mirror tree plus AppleDouble ``._*`` files
    (resource-fork metadata, NOT real GPX/FIT). They can DOUBLE the member
    count of a legitimate archive, so they must not count against
    MAX_ZIP_MEMBERS nor reach the GPX/FIT parser. Junk members are never
    read, so excluding them from the decompress-total guard is safe.
    """
    parts = name.replace("\\", "/").split("/")
    return "__MACOSX" in parts[:-1] or parts[-1].startswith("._") or parts[-1] == "__MACOSX"


def is_ingestible_zip_member(name: str) -> bool:
    """True iff this zip member would actually be read + parsed by an
    archive drain: ``.gpx`` / ``.fit``, optionally gzipped.

    SSOT for BOTH zip paths (``parse_zip_of_gpx`` here and
    ``archive_intake.iter_zip_members``) AND for the zip-bomb caps: a real
    Strava export ships thousands of ``media/*.jpg`` (photos/videos) that
    are NEVER ``zf.read()``, so counting them against MAX_ZIP_MEMBERS /
    MAX_ZIP_TOTAL_UNCOMPRESSED rejected legitimate archives (prod archive
    ``f1ed34db``, 2026-07: 2813 activities + ~3400 media members).
    Excluding never-read members does not weaken the zip-bomb posture —
    every actual read is still bounded by MAX_ZIP_MEMBER_UNCOMPRESSED +
    the streaming gunzip budget.
    """
    low = (name or "").lower()
    return low.endswith((".gpx", ".gpx.gz", ".fit", ".fit.gz"))


def parse_zip_of_gpx(content: bytes) -> list[dict]:
    """Extract and parse all .gpx files from a ZIP archive.

    Defends against zip-bomb / malicious uploads via three caps and a
    member-name allowlist. See module-level constants for thresholds
    and rationale.

    Error surfacing:
      * Archive-level violations — ``ZipBombError`` raised: member
        count cap exceeded, total-uncompressed cap exceeded, or ANY
        member with an unsafe path (``../`` / leading ``/``). Unsafe
        paths are an attacker signal and reject the whole archive
        (caller turns it into HTTP 413). Treated uniformly regardless
        of where the offending member sits relative to
        ``activities.csv`` in the entry order.
      * Per-member problems — appended to the returned list as
        ``{"source_file": str, "error": str}`` entries: per-member
        size cap, gzip decompress overflow, parse failure, dense-coord
        cap. These are "this one file is broken, ingest the rest"
        cases — common when a user's bulk export has one corrupted
        activity among many.
    """
    results = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # Junk members (__MACOSX/ mirror, AppleDouble ._*) are dropped
            # BEFORE any cap: a re-zipped export doubles its entry count
            # with junk and a legitimate archive would trip the member cap.
            infos = [i for i in zf.infolist() if not is_junk_zip_member(i.filename)]
            # Caps count INGESTIBLE members only (see is_ingestible_zip_member):
            # media members are never read, so they can neither bomb us nor
            # legitimately consume the budget. The error messages report the
            # ingestible-only numbers.
            ingestible = [i for i in infos if is_ingestible_zip_member(i.filename)]
            if len(ingestible) > MAX_ZIP_MEMBERS:
                raise ZipBombError(
                    f"ZIP has too many members ({len(ingestible)} > {MAX_ZIP_MEMBERS}); "
                    "split or filter before uploading.",
                )
            total_uncompressed = sum(i.file_size for i in ingestible)
            if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED:
                raise ZipBombError(
                    f"ZIP decompresses to {total_uncompressed:,} bytes "
                    f"(> {MAX_ZIP_TOTAL_UNCOMPRESSED:,}); possible zip-bomb.",
                )

            # Strava bulk export ships an `activities.csv` at root. If
            # present, parse it once and use it to override the in-GPX
            # <trk><type> hint per file. The CSV is authoritative
            # because Strava strips the granular sub-type when writing
            # the GPX itself (collapsing everything to "cycling").
            #
            # Two hardening rules vs the naïve `.endswith("activities.csv")`
            # match:
            #   (a) Root-only — Strava puts it at the ZIP root. Matching
            #       `evil/activities.csv` or `not_activities.csv` lets a
            #       crafted / hand-edited archive shadow the real CSV with
            #       attacker-chosen sport classifications.
            #   (b) Same path-traversal guard as the second loop — a CSV
            #       member named `../../activities.csv` shouldn't be
            #       trusted even though `zf.read` doesn't follow paths
            #       to disk.
            #
            # The keys are stored lower-cased + stripped so the GPX
            # lookup below survives case-preserving filesystems and
            # hand-edited archives (`Activities/100.GPX` vs
            # `activities/100.gpx`).
            sport_by_filename: dict[str, str | None] = {}
            for info in infos:
                if info.filename.startswith("/") or ".." in info.filename.split("/"):
                    # Same guard as the GPX loop; rejecting up front
                    # rather than partway through.
                    raise ZipBombError(f"ZIP member has unsafe path: {info.filename!r}")
                if info.filename.lower() == "activities.csv" and info.file_size <= MAX_ZIP_MEMBER_UNCOMPRESSED:
                    try:
                        csv_bytes = zf.read(info.filename)
                        raw_map = parse_strava_activities_csv(csv_bytes)
                        sport_by_filename = {
                            _normalize_archive_path(k): v for k, v in raw_map.items()
                        }
                    except Exception:
                        # Bad CSV (truncated, encoding issue, etc.) is
                        # not fatal — the caller falls back to the
                        # in-GPX hint + form-supplied sport.
                        sport_by_filename = {}
                    break  # there's only ever one activities.csv at root

            for info in infos:
                name = info.filename
                # Per-member size cap — pre-PR this `raise`d
                # ZipBombError mid-loop, unwinding the WHOLE archive
                # even if 1398 prior members already parsed
                # successfully (frontend sees a single 413 with zero
                # results). The common cause is ONE oversize activity
                # in a bulk export — UX win to isolate it and ingest
                # the rest. Reported per-member like the downstream
                # parse / coord-cap errors. Audit 2026-05-29 GPX-S2.6.
                if info.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED:
                    results.append({
                        "source_file": name,
                        "error": (
                            f"ZIP member is {info.file_size:,} bytes "
                            f"(> {MAX_ZIP_MEMBER_UNCOMPRESSED:,}); "
                            "reject as zip-bomb."
                        ),
                    })
                    continue
                # Path-traversal / absolute-path: this is an attacker
                # signal, not a "user has one bad GPX" case. Reject
                # the WHOLE archive (raise → caller maps to 413). Must
                # behave identically to the first-loop guard at L669
                # so the same byte sequence cannot yield 413 OR 202+
                # errors depending on whether `activities.csv` came
                # first in entry order. PR #359 review S2.
                if name.startswith("/") or ".." in name.split("/"):
                    raise ZipBombError(f"ZIP member has unsafe path: {name!r}")
                # Accept `.gpx`, `.gpx.gz`, `.fit`, and `.fit.gz` — Strava
                # bulk exports post-2022 ship gzipped members
                # (`activities/<id>.gpx.gz` or `.fit.gz`). Pre-fix the
                # gzipped variants silently fell past this filter and the
                # entire archive landed at `imported=0`. Audit 2026-05-29
                # GPX-S1.1.
                lower = name.lower()
                if not is_ingestible_zip_member(name):
                    continue
                is_fit = lower.endswith(".fit") or lower.endswith(".fit.gz")
                try:
                    member_bytes = zf.read(name)
                    # Decompress STREAMING with a hard read budget if the
                    # member is gzipped. `gzip.decompress(...)` allocates
                    # the full decompressed buffer in one shot — a 10 MB
                    # gzipped payload of repeated bytes can expand to
                    # ~10 GB at ratio 1000:1, OOM-killing the worker
                    # BEFORE any size check runs (the `MAX_ZIP_MEMBER_
                    # UNCOMPRESSED=10 MB` cap above only bounds the
                    # zip-side compressed size, not the gunzip output).
                    # `GzipFile.read(N+1)` reads at most N+1 bytes — if
                    # we get back > N, we know the decompressed payload
                    # exceeds `MAX_GPX_SIZE` and we reject without
                    # finishing decompression. Audit 2026-05-29 GPX-S1.1
                    # PR #356 review S1.
                    if lower.endswith(".gz"):
                        try:
                            with gzip.GzipFile(
                                fileobj=io.BytesIO(member_bytes),
                            ) as gz:
                                member_bytes = gz.read(MAX_GPX_SIZE + 1)
                        except (OSError, EOFError) as exc:
                            results.append({"source_file": name, "error": f"gzip decompress failed: {exc}"})
                            continue
                        if len(member_bytes) > MAX_GPX_SIZE:
                            results.append({
                                "source_file": name,
                                "error": f"decompressed size > {MAX_GPX_SIZE} bytes "
                                         "(streaming cap)",
                            })
                            continue
                    if is_fit:
                        # FIT parser availability check has to wrap the
                        # CALL site, not the import. `fit_parser.py`'s
                        # `import fitparse` lives inside `parse_fit`
                        # itself (line 17), so the wrapper module import
                        # always succeeds — the ImportError only fires
                        # at parse time. Audit PR #356 review S2.
                        from app.services.fit_parser import parse_fit
                        try:
                            parsed = parse_fit(member_bytes)
                        except ImportError as exc:
                            results.append({
                                "source_file": name,
                                "error": f"FIT support not installed (fitparse): {exc}",
                            })
                            continue
                    else:
                        parsed = parse_gpx(member_bytes)
                    parsed["source_file"] = name
                    # Dense-coordinate DoS cap — the single-file path
                    # (gpx_upload.py:89) and the folder CLI both reject
                    # any trace with coord_count > MAX_GPX_COORDS, but the
                    # zip path never did: a <10 MB member can still pack
                    # millions of points and inflate ingest CPU/RAM.
                    # Reported per-member (skip this one, ingest the rest)
                    # like the size / parse errors above, rather than
                    # unwinding the whole archive. Audit 2026-06 GPX-S2.
                    coord_count = parsed.get("coord_count", 0)
                    if coord_count > MAX_GPX_COORDS:
                        results.append({
                            "source_file": name,
                            "error": (
                                f"GPX has too many coordinates "
                                f"({coord_count} > {MAX_GPX_COORDS}); "
                                "split or simplify the trace"
                            ),
                        })
                        continue
                    # CSV-derived sport / skip resolution. Lookups are
                    # done on the *normalized* path (lower-case, stripped
                    # of leading slash and whitespace) so a hand-edited
                    # or case-mismatched archive doesn't silently drop
                    # the file past the skip-gate.
                    #
                    # Three CSV outcomes:
                    #   1. Mapped sport string → stamp sport_from_csv
                    #      (cascade ranks it above the in-GPX hint).
                    #   2. None (key present, out-of-scope or unknown
                    #      Activity Type like Yoga/Workout/Ski or any
                    #      indoor / virtual ride) → stamp skip_reason;
                    #      the caller must drop the file to avoid
                    #      heatmap pollution.
                    #   3. Key absent → no CSV hint at all, cascade
                    #      falls back to in-GPX <trk><type> + form.
                    lookup = _normalize_archive_path(name)
                    if lookup in sport_by_filename:
                        csv_value = sport_by_filename[lookup]
                        # `is not None` is intentional — must not coerce
                        # truthiness so a future "" or 0 in the map
                        # doesn't silently fall through to form-sport.
                        if csv_value is not None:
                            parsed["sport_from_csv"] = csv_value
                        else:
                            parsed["skip_reason"] = (
                                "Strava activities.csv: out-of-scope activity type "
                                "(yoga/workout/swim/ski/virtual/indoor/etc.) — "
                                "not ingestible into a heatmap sport"
                            )
                    results.append(parsed)
                except Exception as exc:
                    results.append({"source_file": name, "error": str(exc)})
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file") from exc
    return results


def coords_to_gpx(coords: list[list[float]], name: str = "route") -> str:
    """Convert a list of [lon, lat] coordinates to GPX string."""
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

    for point in coords:
        lon, lat = point[0], point[1]  # supports both 2D [lon,lat] and 3D [lon,lat,ele]
        ele = point[2] if len(point) > 2 else None
        segment.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon, elevation=ele))

    return gpx.to_xml()


def geojson_to_gpx(geojson_str: str | None, name: str = "route") -> str:
    """Convert a GeoJSON LineString to GPX."""
    if not geojson_str:
        return coords_to_gpx([], name)
    try:
        geojson = json.loads(geojson_str)
        coords = geojson.get("coordinates", [])
    except Exception:
        coords = []
    return coords_to_gpx(coords, name)
