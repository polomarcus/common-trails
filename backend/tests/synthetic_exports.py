"""Deterministic SYNTHETIC bulk exports (Strava + Garmin-like) for the
full-import regression net.

The repo is public: no real user export may ever be committed. This module
rebuilds, byte-for-byte deterministically and at test time, a Strava bulk
export and a Garmin "Export All Data"-like zip that reproduce every trap a
real end-to-end import has hit so far:

Strava (``build_strava_export``)
  * everything nested under a top folder ``export_123/`` (macOS
    right-click → Compress of the unzipped export) + ``__MACOSX/`` junk;
  * ``activities.csv`` with the real Strava header row — including its
    DUPLICATED ``Elapsed Time`` / ``Distance`` columns — out-of-scope types
    (Workout, Swim, Surf, Weight Training), Ride / Run / Hike / E-Bike Ride /
    Mountain Bike Ride rows, a row with an empty ``Filename`` and a
    200 000-char ``Activity Description`` (> csv's default field limit);
  * ``activities/*.gpx``, ``*.fit.gz`` and ``*.fit`` members, one FIT with the
    "Invalid field size 1 for type uint32" definition that only the fitdecode
    fallback reads, one truncated (corrupt) FIT;
  * ``routes/*.gpx`` planned routes (untimed, one typed ``running``);
  * a byte-duplicate activity, a track with no geometry, an
    ``activities/`` file the CSV does not list, a ``media/`` jpg.

Garmin-like (``build_garmin_export``)
  * no activities.csv; FIT files carrying sport / sub_sport inside a NESTED
    ``UploadedFiles_*.zip`` (+ a top-level GPX typed ``road_biking``), a
    virtual ride, a FIT with no usable sport, non-ingestible JSON, junk.

Deterministic for a given interpreter (fixed zip timestamps, gzip mtime=0);
the deflate bytes may differ across zlib builds, the content never does.

Each builder returns ``(zip_bytes, expected)``: ``expected`` is the exact
outcome the drain must produce (counts, sport per imported activity keyed by
its start timestamp, members that must never become an activity).

STDLIB ONLY on purpose — the Playwright spec and ``make e2e-import-real``
run it with whatever ``python3`` the host has::

    python3 backend/tests/synthetic_exports.py strava /tmp/strava.zip
    python3 backend/tests/synthetic_exports.py garmin /tmp/garmin.zip

(prints the expected drain outcome as one JSON line).

Coordinates are synthetic straight-ish lines on the Aubrac plateau (no real
ride, no real name).
"""

from __future__ import annotations

import gzip
import io
import json
import struct
import sys
import zipfile
from datetime import datetime, timedelta, timezone

# Not ``datetime.UTC`` (3.11+): the host's python3 may be older (macOS 3.9).
_UTC = timezone.utc  # noqa: UP017

# Fixed zip entry timestamp → byte-identical output on every run.
_ZIP_DATE = (2024, 1, 1, 0, 0, 0)
# Neutral synthetic area (Aubrac plateau), well away from the Hérault /
# Montpellier fixtures used elsewhere in the suite.
_LAT0, _LON0 = 44.62, 2.98
N_POINTS = 30


# ── GPX ──────────────────────────────────────────────────────────────────


def make_gpx(
    name: str,
    idx: int,
    *,
    start: datetime | None,
    trk_type: str | None = None,
    n: int = N_POINTS,
) -> bytes:
    """A small GPX track. ``idx`` shifts it so every activity has its own
    geometry (no accidental near-dup merge). ``start=None`` → untimed (a
    Strava planned route). ``n=0`` → a track with no point at all."""
    lat0 = _LAT0 + idx * 0.01
    lon0 = _LON0 + idx * 0.01
    pts = []
    for i in range(n):
        lat = lat0 + i * 0.0007
        lon = lon0 + i * 0.0009 + (0.0002 if i % 2 else 0.0)
        ele = 1200 + (i % 7)
        time = ""
        if start is not None:
            ts = (start + timedelta(seconds=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            time = f"<time>{ts}</time>"
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{ele}</ele>{time}</trkpt>')
    type_xml = f"<type>{trk_type}</type>" if trk_type else ""
    meta = ""
    if start is not None:
        meta = f"<metadata><time>{start.strftime('%Y-%m-%dT%H:%M:%SZ')}</time></metadata>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx creator="synthetic" version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"{meta}<trk><name>{name}</name>{type_xml}<trkseg>{''.join(pts)}</trkseg></trk></gpx>\n"
    )
    return xml.encode()


# ── FIT (hand-encoded: header + definition/data messages + CRC) ─────────

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)
_SEMI = 2**31 / 180.0
_UINT32 = 0x86
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=_UTC)

# fitparse profile enum values
SPORT = {"generic": 0, "running": 1, "cycling": 2, "swimming": 5, "training": 10, "hiking": 17}
SUB_SPORT = {
    "generic": 0, "road": 7, "mountain": 8, "strength_training": 20,
    "gravel_cycling": 46, "virtual_activity": 58,
}


def _crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _definition(local: int, global_num: int, fields: list) -> bytes:
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
    for num, size, base in fields:
        out += struct.pack("<BBB", num, size, base)
    return out


def make_fit(
    idx: int,
    *,
    start: datetime,
    sport: str,
    sub_sport: str = "generic",
    missized: bool = False,
    n: int = N_POINTS,
) -> bytes:
    """A minimal activity FIT: file_id + ``n`` GPS records + one session.

    ``missized=True`` adds a manufacturer-specific message whose definition
    declares a uint32 field of size 1 — fitparse 1.2.0 rejects the WHOLE file
    with "Invalid field size 1 for type 'uint32'"; only the fitdecode fallback
    reads it (the prod failure that dropped every .fit.gz of a real export).
    """
    t0 = int((start - _FIT_EPOCH).total_seconds())
    body = _definition(0, 0, [(0, 1, 0x00), (1, 2, 0x84)])  # file_id
    body += struct.pack("<BBH", 0, 4, 1)  # type=activity, manufacturer=garmin
    if missized:
        body += _definition(3, 0xFF00, [(0, 1, _UINT32), (1, 2, 0x84)])
        body += struct.pack("<BBH", 3, 7, 42)
    body += _definition(1, 20, [(0, 4, 0x85), (1, 4, 0x85), (253, 4, _UINT32), (2, 2, 0x84)])
    lat0 = _LAT0 - 0.2 + idx * 0.01
    lon0 = _LON0 - 0.2 + idx * 0.01
    for i in range(n):
        lat, lon, alt_m = lat0 + i * 0.0008, lon0 + i * 0.0006, 1100 + (i % 5)
        body += struct.pack(
            "<BiiIH", 1, int(lat * _SEMI), int(lon * _SEMI), t0 + 10 * i, (alt_m + 500) * 5
        )
    # session: sport, sub_sport, total_distance (cm), start_time
    body += _definition(2, 18, [(5, 1, 0x00), (6, 1, 0x00), (9, 4, _UINT32), (2, 4, _UINT32)])
    body += struct.pack("<BBBII", 2, SPORT[sport], SUB_SPORT[sub_sport], 250_000, t0)

    header = struct.pack("<BBHI4s", 14, 0x10, 2093, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + body
    return data + struct.pack("<H", _crc(data))


# ── Zip helpers ─────────────────────────────────────────────────────────


def _write(zf: zipfile.ZipFile, name: str, data: bytes | str) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def _ts(day: int) -> datetime:
    """Deterministic start time of synthetic activity #day (one per day)."""
    return datetime(2024, 3, 1, 7, 30, tzinfo=_UTC) + timedelta(days=day)


def _key(dt: datetime) -> str:
    """Key used in ``expected['sports_by_start']`` (naive UTC, minute)."""
    return dt.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M")


# ── Strava bulk export ──────────────────────────────────────────────────

# The real Strava header row (abridged at the end), duplicated columns included.
STRAVA_CSV_HEADER = [
    "Activity ID", "Activity Date", "Activity Name", "Activity Type",
    "Activity Description", "Elapsed Time", "Distance", "Max Heart Rate",
    "Relative Effort", "Commute", "Activity Private Note", "Activity Gear",
    "Filename", "Athlete Weight", "Bike Weight", "Elapsed Time", "Moving Time",
    "Distance", "Max Speed", "Average Speed", "Elevation Gain", "Elevation Loss",
    "Media",
]

STRAVA_ROOT = "export_123/"


def _csv_cell(v: str) -> str:
    if any(c in v for c in ',"\n'):
        return '"' + v.replace('"', '""') + '"'
    return v


def build_strava_export() -> tuple[bytes, dict]:
    """Synthetic Strava bulk export → ``(zip_bytes, expected)``."""
    root = STRAVA_ROOT
    # (activity_id, type, name, filename, member bytes or None, outcome, sport/reason)
    rows: list[tuple] = []
    members: dict[str, bytes] = {}

    def add(aid, a_type, name, filename, data, outcome, detail, *, desc=""):
        rows.append((aid, a_type, name, filename, desc))
        if filename and data is not None:
            members[filename] = data
        if filename:
            expect[filename] = (outcome, detail)

    expect: dict[str, tuple[str, str | None]] = {}
    gpx_ride = make_gpx("Morning Ride", 1, start=_ts(1), trk_type="cycling")

    # Imported, one per sport path.
    add(1001, "Ride", "Morning Ride", "activities/1001.gpx", gpx_ride, "imported", "road")
    # CSV "Ride" + name keyword → gravel; valid FIT (fitparse path), gzipped.
    add(1002, "Ride", "Gravel du dimanche", "activities/1002.fit.gz",
        _gz(make_fit(2, start=_ts(2), sport="cycling")), "imported", "gravel")
    # The prod FIT failure: mis-sized uint32 → fitdecode fallback.
    add(1003, "Mountain Bike Ride", "Sortie du soir", "activities/1003.fit.gz",
        _gz(make_fit(3, start=_ts(3), sport="cycling", sub_sport="mountain", missized=True)),
        "imported", "mtb")
    add(1004, "Run", "Footing", "activities/1004.gpx",
        make_gpx("Footing", 4, start=_ts(4), trk_type="running"), "imported", "running")
    # Plain (not gzipped) .fit.
    add(1005, "Hike", "Randonnee", "activities/1005.fit",
        make_fit(5, start=_ts(5), sport="hiking"), "imported", "running")
    add(1006, "E-Bike Ride", "Balade", "activities/1006.gpx",
        make_gpx("Balade", 6, start=_ts(6), trk_type="cycling"), "imported", "road")
    # 200k-char description (> csv default field_size_limit 131072).
    add(1011, "Ride", "Sortie longue", "activities/1011.gpx",
        make_gpx("Sortie longue", 11, start=_ts(11), trk_type="cycling"),
        "imported", "road", desc="Tres longue description. " * 8000)

    # Out-of-scope CSV types → skipped, bytes never parsed.
    add(1007, "Workout", "Renfo", "activities/1007.fit.gz",
        _gz(make_fit(7, start=_ts(7), sport="training", sub_sport="strength_training")),
        "skipped", "out_of_scope")
    add(1008, "Swim", "Piscine", "activities/1008.fit.gz",
        _gz(make_fit(8, start=_ts(8), sport="swimming")), "skipped", "out_of_scope")
    add(1009, "Surf", "Session", "activities/1009.gpx",
        make_gpx("Session", 9, start=_ts(9)), "skipped", "out_of_scope")
    # No file at all (Strava leaves Filename empty for manual entries).
    add(1010, "Weight Training", "Muscu", "", None, None, None)
    # Byte-identical copy of 1001 → file_hash dedup.
    add(1012, "Ride", "Morning Ride", "activities/1012.gpx", gpx_ride, "skipped", "duplicate")
    # A track with no point → husk guard.
    add(1013, "Ride", "Sans trace", "activities/1013.gpx",
        make_gpx("Sans trace", 13, start=_ts(13), n=0), "skipped", "no_geometry")
    # Truncated FIT → member failed, archive still done.
    add(1014, "Ride", "Fichier abime", "activities/1014.fit.gz",
        _gz(make_fit(14, start=_ts(14), sport="cycling")[:120]), "failed", None)

    header = ",".join(STRAVA_CSV_HEADER) + "\n"
    lines = []
    for aid, a_type, name, filename, desc in rows:
        date = _ts(aid - 1000).strftime("%b %-d, %Y, %-I:%M:%S %p")
        cells = {
            "Activity ID": str(aid), "Activity Date": date, "Activity Name": name,
            "Activity Type": a_type, "Activity Description": desc, "Filename": filename,
            "Elapsed Time": "3600", "Distance": "25.3", "Commute": "false",
        }
        lines.append(",".join(_csv_cell(cells.get(h, "")) for h in STRAVA_CSV_HEADER))
    csv_text = header + "\n".join(lines) + "\n"

    unlisted = "activities/9999.gpx"
    expect[unlisted] = ("skipped", "unlisted")
    routes = {
        "routes/501.gpx": make_gpx("Boucle prevue", 50, start=None, trk_type="running"),
        "routes/502.gpx": make_gpx("Itineraire", 51, start=None),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        _write(zf, f"{root}activities.csv", csv_text)
        _write(zf, f"__MACOSX/{root}._activities.csv", b"\x00\x05\x16\x07junk")
        for fname, data in members.items():
            _write(zf, f"{root}{fname}", data)
            _write(zf, f"__MACOSX/{root}activities/._{fname.rsplit('/', 1)[-1]}", b"\x00\x05\x16\x07")
        _write(zf, f"{root}{unlisted}", make_gpx("Fichier orphelin", 99, start=_ts(99), trk_type="cycling"))
        for fname, data in routes.items():
            _write(zf, f"{root}{fname}", data)
        _write(zf, f"{root}media/abc.jpg", b"\xff\xd8\xff\xe0synthetic")
        _write(zf, f"{root}profile.csv", "Athlete ID,First Name\n1,Synthetic\n")

    sports_by_start = {
        _key(_ts(int(f.split("/")[1].split(".")[0]) - 1000)): d
        for f, (o, d) in expect.items() if o == "imported"
    }
    reasons: dict[str, int] = {}
    for o, d in expect.values():
        if o == "skipped":
            reasons[d] = reasons.get(d, 0) + 1
    expected = {
        "root": root,
        "members": {f"{root}{k}": v for k, v in expect.items()},
        "imported": sum(1 for o, _ in expect.values() if o == "imported"),
        "skipped": sum(1 for o, _ in expect.values() if o == "skipped"),
        "failed": sum(1 for o, _ in expect.values() if o == "failed"),
        "skipped_by_reason": reasons,
        "skipped_no_sport": 0,
        "members_total": len(expect),
        "sports_by_start": sports_by_start,
        # Never an activity: planned routes + the never-ingestible members.
        "never_ingested": [f"{root}{r}" for r in routes],
        "route_names": ["Boucle prevue", "Itineraire"],
        # Start-email estimate counts what the drain will walk (listed members).
        "estimate_members": sum(1 for f in expect if f != unlisted),
    }
    return buf.getvalue(), expected


# ── Garmin-like export ──────────────────────────────────────────────────


def build_garmin_export() -> tuple[bytes, dict]:
    """Garmin "Export All Data"-like zip (no activities.csv, nested zip)."""
    inner_files = {
        "1_ACTIVITY.fit": (make_fit(21, start=_ts(21), sport="cycling", sub_sport="mountain"),
                           "imported", "mtb"),
        "2_ACTIVITY.fit": (make_fit(22, start=_ts(22), sport="cycling", sub_sport="gravel_cycling",
                                    missized=True), "imported", "gravel"),
        "3_ACTIVITY.fit": (make_fit(23, start=_ts(23), sport="cycling", sub_sport="virtual_activity"),
                           "skipped", "out_of_scope"),
        "4_ACTIVITY.fit": (make_fit(24, start=_ts(24), sport="generic"), "skipped", "sport_unknown"),
    }
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zin:
        for name, (data, _o, _d) in inner_files.items():
            _write(zin, name, data)
    nested = "DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip"
    top_gpx = "DI_CONNECT/DI-Connect-Fitness/activity_25.gpx"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        _write(zf, top_gpx, make_gpx("Sortie route", 25, start=_ts(25), trk_type="road_biking"))
        _write(zf, "DI_CONNECT/DI-Connect-Fitness/summarizedActivities.json", '[{"activityId": 1}]')
        _write(zf, nested, inner.getvalue())
        _write(zf, "__MACOSX/DI_CONNECT/._DI-Connect-Fitness", b"\x00\x05\x16\x07")

    members = {f"{nested}!{k}": (o, d) for k, (_b, o, d) in inner_files.items()}
    members[top_gpx] = ("imported", "road")
    sports_by_start = {
        _key(_ts(21)): "mtb", _key(_ts(22)): "gravel", _key(_ts(25)): "road",
    }
    expected = {
        "members": members,
        "imported": 3,
        "skipped": 2,
        "failed": 0,
        "skipped_by_reason": {"out_of_scope": 1, "sport_unknown": 1},
        "skipped_no_sport": 1,
        "members_total": 5,
        "sports_by_start": sports_by_start,
        "never_ingested": [],
    }
    return buf.getvalue(), expected


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("strava", "garmin"):
        print("usage: synthetic_exports.py strava|garmin OUT.zip", file=sys.stderr)
        return 2
    data, _exp = (build_strava_export if argv[1] == "strava" else build_garmin_export)()
    with open(argv[2], "wb") as fh:
        fh.write(data)
    # One JSON line on stdout: the outcome the drain must produce (read by the
    # Playwright spec so the expectations have a single source).
    summary = {
        k: _exp[k] for k in ("members_total", "imported", "skipped", "failed", "skipped_no_sport")
    }
    summary["sports"] = sorted(_exp["sports_by_start"].values())
    summary["bytes"] = len(data)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
