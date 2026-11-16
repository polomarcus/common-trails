"""Garmin Connect's own record of the activities of an "Export all" archive.

A Garmin "Export all data" archive ships the activity files (``.fit``) inside
an inner ``DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_*.zip`` AND, next
to it, ``DI_CONNECT/DI-Connect-Fitness/<account>_<n>_summarizedActivities.json``:
Garmin Connect's summary of every activity, with the TYPE and NAME the rider
sees in Connect. Those can differ from the FIT file's own: the FIT carries the
device PROFILE it was recorded with (very often the generic "Bike" profile →
``cycling/generic`` → road by default) and the parser can only name it after
the sport ("Cycling"), while the rider may have re-typed the ride in Connect
afterwards (``gravel_cycling``, ``mountain_biking``…) and named it. That edit
lives ONLY in this summary — reading it is how a Garmin import gets the rider's
classification, exactly as ``activities.csv`` does for a Strava export.

The join key: the FIT files are named ``<account>_<N>.fit`` where ``N`` is NOT
the Connect ``activityId``, so a member is matched to a summary row by START
TIME — the FIT session ``start_time`` (UTC) against the row's
``beginTimestamp`` (epoch ms, UTC), within ``MATCH_TOLERANCE_S``, nearest row
wins and an ambiguous tie gives no match. Many FIT files have no row at all
(a real export: 652 rows for 6,275 FIT files) → they import exactly as before.

Best-effort by construction: a missing / malformed / truncated / huge summary
never fails an import — it only means less information. Each part is
STREAM-decoded one row at a time (a heavy account's part can weigh hundreds of
MB) and the index keeps only ``timestamp → (sport hint, name)``.
"""
from __future__ import annotations

import bisect
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services import gpx as gpx_service
from app.services.strava_utils import GENERIC_ROAD, _norm_type, is_default_sport, refine_sport_by_name

log = logging.getLogger(__name__)

# Basename suffix of a summary part (``<account>_0_summarizedActivities.json``,
# ``…_1_…``), matched case-insensitively at any depth of the archive.
SUMMARY_SUFFIX = "summarizedactivities.json"
# The array of rows inside a part: ``[{"summarizedActivitiesExport": [ … ]}]``.
_ROWS_KEY = '"summarizedActivitiesExport"'

# FIT session start vs Connect ``beginTimestamp``: identical on a real export;
# the tolerance absorbs a re-encoded / re-uploaded file.
MATCH_TOLERANCE_S = 60.0

# Bounds — the index must stay small whatever the archive holds.
_MAX_PARTS = 50
_MAX_ROWS = 100_000
_MAX_PART_CHARS = 1024 * 1024 * 1024  # decoded text read per part
_MAX_ROW_CHARS = 1024 * 1024  # one row is ~10 KB; bigger = malformed
_CHUNK_CHARS = 64 * 1024
_NAME_MAX = 255  # activities.name is VARCHAR(255)

# ── Connect activity type → our sport ───────────────────────────────────────
#
# Connect ``activityType`` keys are the same vocabulary Garmin writes in a GPX
# ``<type>``, so the SSOT is ``gpx.gpx_track_type_skip_or_sport`` (road_biking,
# gravel_cycling, mountain_biking, cyclocross, running, hiking… and the
# indoor/virtual/swim/strength skip set). Overlaid here:
#   * the bare "a bike ride" types — the SSOT reads "cycling" as no information,
#     and e_bike_fitness (Garmin's plain "E-Bike") as road; for a Connect summary
#     both are a GENERIC ride, road only by default (``sport_is_default``), that
#     the ride's name may refine (gravel / vtt / mtb…);
#   * explicit disciplines the GPX table does not list.
_CONNECT_GENERIC_TYPES: frozenset[str] = frozenset(
    _norm_type(t) for t in ("cycling", "e_bike_fitness", "biking", "bike")
)
_CONNECT_EXTRA_TYPES: dict[str, str] = {
    _norm_type(k): v
    for k, v in {
        "enduro_mtb": "mtb",
        "e_enduro_mtb": "mtb",
        "e_bike_road": "road",
        "ultra_run": "running",
    }.items()
}


@dataclass(frozen=True, slots=True)
class ConnectActivity:
    """What Garmin Connect says about one activity.

    * ``sport`` — an EXPLICIT discipline from the Connect type (road_biking,
      gravel_cycling, running…): the rider's latest word, it beats the FIT
      device profile.
    * ``hint`` — a bare bike type ("cycling"): ``GENERIC_ROAD``, or gravel/mtb
      when the NAME says so. A low-rank candidate of the sport cascade: the
      FIT's own discipline still decides when it has one.
    * ``skip`` — the Connect type is out of scope (indoor / virtual / swim…).
    * ``name`` — the Connect activity name.
    """

    sport: str | None = None
    hint: str | None = None
    skip: bool = False
    name: str | None = None


def classify_connect_type(activity_type: str | None, name: str | None = None) -> ConnectActivity:
    """Map a Connect ``activityType`` (+ the activity name) to a
    :class:`ConnectActivity` (``name`` not filled in). Unknown types
    (``other``, ``tennis_v2``…) carry no information. Never raises."""
    if not isinstance(activity_type, str) or not activity_type.strip():
        return ConnectActivity()
    key = _norm_type(activity_type)
    if key in _CONNECT_GENERIC_TYPES:
        return ConnectActivity(hint=refine_sport_by_name(name) or GENERIC_ROAD)
    if key in _CONNECT_EXTRA_TYPES:
        return ConnectActivity(sport=_CONNECT_EXTRA_TYPES[key])
    sport, skip = gpx_service.gpx_track_type_skip_or_sport(activity_type)
    if skip:
        return ConnectActivity(skip=True)
    if sport is None:
        return ConnectActivity()
    # A generic marker cannot come out of the Garmin keys, but the SSOT also
    # knows Strava's ("Ride"…): never let one pose as an explicit type.
    if is_default_sport(sport):
        return ConnectActivity(hint=refine_sport_by_name(name) or GENERIC_ROAD)
    return ConnectActivity(sport=str(sport))


# ── Index ───────────────────────────────────────────────────────────────────


def _epoch_seconds(value) -> float | None:
    """Epoch seconds of a parsed start time (naive = UTC, as both FIT readers
    return it), or None."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    try:
        return value.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


class ConnectIndex:
    """Sorted ``start (epoch s) → ConnectActivity`` index of the summary rows."""

    __slots__ = ("_ts", "_rows")

    def __init__(self, entries=()) -> None:
        ordered = sorted(entries, key=lambda e: e[0])
        self._ts: list[float] = [ts for ts, _row in ordered]
        self._rows: list[ConnectActivity] = [row for _ts, row in ordered]

    def __len__(self) -> int:
        return len(self._ts)

    def lookup(self, start, *, tolerance_s: float = MATCH_TOLERANCE_S) -> ConnectActivity | None:
        """The row whose start is nearest ``start`` within ``tolerance_s``;
        None when there is none, or when several DIFFERENT rows are equally
        near (a multisport parent + its leg, two uploads…) — never guess."""
        t = _epoch_seconds(start)
        if t is None or not self._ts:
            return None
        lo = bisect.bisect_left(self._ts, t - tolerance_s)
        hi = bisect.bisect_right(self._ts, t + tolerance_s)
        if lo >= hi:
            return None
        best = min(abs(self._ts[k] - t) for k in range(lo, hi))
        hits = {self._rows[k] for k in range(lo, hi) if abs(self._ts[k] - t) == best}
        return hits.pop() if len(hits) == 1 else None


def _row_start_seconds(row: dict) -> float | None:
    """A row's start in epoch seconds (``beginTimestamp``, else
    ``startTimeGmt``, both epoch ms UTC), or None."""
    for key in ("beginTimestamp", "startTimeGmt"):
        ms = row.get(key)
        # bool is an int: never a timestamp. Plausible epoch-ms window
        # (2000 → 2100); anything else is junk.
        if (
            isinstance(ms, (int, float)) and not isinstance(ms, bool)
            and 946_684_800_000 <= ms <= 4_102_444_800_000
        ):
            return ms / 1000.0
    return None


def _row_activity(row: dict) -> ConnectActivity:
    a_type = row.get("activityType")
    if isinstance(a_type, dict):  # the Connect API shape: {"typeKey": …}
        a_type = a_type.get("typeKey")
    name = row.get("name")
    name = (name.strip()[:_NAME_MAX] or None) if isinstance(name, str) else None
    info = classify_connect_type(a_type if isinstance(a_type, str) else None, name)
    return ConnectActivity(sport=info.sport, hint=info.hint, skip=info.skip, name=name)


class _MalformedSummary(ValueError):
    pass


def iter_summary_rows(fh):
    """Yield each row (a dict) of every ``summarizedActivitiesExport`` array of
    a summary part, read from the text stream ``fh``, decoding ONE row at a
    time: memory stays at one chunk + one row whatever the part's size.

    Raises :class:`_MalformedSummary` on malformed / truncated / oversize
    content — after having yielded every complete row before it."""
    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    total = 0
    eof = False

    def fill() -> bool:
        nonlocal buf, pos, total, eof
        if eof:
            return False
        chunk = fh.read(_CHUNK_CHARS)
        if not chunk:
            eof = True
            return False
        total += len(chunk)
        if total > _MAX_PART_CHARS:
            raise _MalformedSummary("summary part too large")
        buf = buf[pos:] + chunk
        pos = 0
        return True

    def next_char(skip: str) -> str | None:
        """Advance past the characters in ``skip``; the next char, or None at EOF."""
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in skip:
                pos += 1
            if pos < len(buf):
                return buf[pos]
            if not fill():
                return None

    while True:
        # Find the next rows array (the key may straddle two chunks).
        i = buf.find(_ROWS_KEY, pos)
        if i < 0:
            pos = max(pos, len(buf) - len(_ROWS_KEY))
            if not fill():
                return
            continue
        pos = i + len(_ROWS_KEY)
        if next_char(" \t\r\n") != ":":
            raise _MalformedSummary("expected ':' after the rows key")
        pos += 1
        if next_char(" \t\r\n") != "[":
            raise _MalformedSummary("expected the rows array")
        pos += 1
        while True:
            c = next_char(" \t\r\n,")
            if c is None:
                raise _MalformedSummary("truncated rows array")
            if c == "]":
                pos += 1
                break
            try:
                obj, end = decoder.raw_decode(buf, pos)
            except json.JSONDecodeError:
                # Incomplete row in the buffer → read on; a row that never
                # completes is malformed.
                if len(buf) - pos > _MAX_ROW_CHARS or not fill():
                    raise _MalformedSummary("malformed row") from None
                continue
            pos = end
            if isinstance(obj, dict):
                yield obj


def summary_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The summary parts of an archive (macOS junk excluded), in name order."""
    parts = [
        info for info in zf.infolist()
        if info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower().endswith(SUMMARY_SUFFIX)
        and not gpx_service.is_junk_zip_member(info.filename)
        and not info.is_dir()
    ]
    return sorted(parts, key=lambda i: i.filename)[:_MAX_PARTS]


def build_index(zf: zipfile.ZipFile) -> ConnectIndex:
    """Index every summary part of an open archive. Never raises: an
    unreadable part is skipped (its complete rows before the fault are kept);
    no summary at all → an empty index (every lookup → None)."""
    try:
        parts = summary_members(zf)
    except Exception:  # noqa: BLE001 — a hint must never break an import
        return ConnectIndex()
    entries: list[tuple[float, ConnectActivity]] = []
    for info in parts:
        if len(entries) >= _MAX_ROWS:
            break
        try:
            with zf.open(info) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace") as fh:
                for row in iter_summary_rows(fh):
                    ts = _row_start_seconds(row)
                    if ts is None:
                        continue
                    entries.append((ts, _row_activity(row)))
                    if len(entries) >= _MAX_ROWS:
                        log.warning("garmin connect summary: > %d rows — ignoring the rest", _MAX_ROWS)
                        break
        except Exception as exc:  # noqa: BLE001 — malformed part: keep what was read
            log.warning("garmin connect summary part unreadable — partly ignored: %s", exc)
    if parts:
        log.info("garmin connect summary: %d activities indexed from %d part(s)", len(entries), len(parts))
    return ConnectIndex(entries)
