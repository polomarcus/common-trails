"""Garmin "Export all": the activity TYPE and NAME the rider set in Garmin
Connect (``DI_CONNECT/DI-Connect-Fitness/<account>_<n>_summarizedActivities.json``)
are used for each FIT, joined by START TIME (the FIT file names are not the
Connect activity ids).

Prod, 2026-11: 1,982 rides were "road by default", all from Garmin archives,
all named "Cycling" — the FIT only carries the device profile (very often the
generic "Bike" one → ``cycling/generic``) while the rider had re-typed them
``gravel_cycling`` / ``mountain_biking`` in Connect.

DB-free: the REAL ``drain_pending_archives`` runs over an in-memory synthetic
zip (tiny hand-encoded FIT files + a fake summary); only the DB seams and
``ingest_activity`` are stubbed, the latter capturing what would be stored.
"""
from __future__ import annotations

import contextlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.jobs import ingest_pending_archives as drain_mod
from app.services import archive_intake, garmin_connect
from app.services.strava_utils import GENERIC_ROAD, is_default_sport
from tests.synthetic_exports import make_fit

_T0 = datetime(2025, 5, 4, 7, 15, tzinfo=UTC)
_NESTED = "DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_0-_Part1.zip"
_PART0 = "DI_CONNECT/DI-Connect-Fitness/rider@example.com_0_summarizedActivities.json"
_PART1 = "DI_CONNECT/DI-Connect-Fitness/rider@example.com_1_summarizedActivities.json"


def _start(day: int, seconds: int = 0) -> datetime:
    return _T0 + timedelta(days=day, seconds=seconds)


def _row(start: datetime, activity_type, name="Sortie", **extra) -> dict:
    ms = int(start.timestamp() * 1000)
    return {
        "activityId": 7_000_000 + ms % 100_000, "name": name, "activityType": activity_type,
        "sportType": "CYCLING", "beginTimestamp": ms, "startTimeGmt": float(ms),
        "parent": False, "distance": 2_500_000.0, **extra,
    }


def _summary(*rows: dict) -> str:
    return json.dumps([{"summarizedActivitiesExport": list(rows)}], indent=1)


def _zip(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _archive(fits: dict[str, bytes], summaries: dict[str, bytes | str]) -> bytes:
    """Garmin "Export all" layout: FIT files in the inner UploadedFiles zip,
    the summary part(s) next to it."""
    return _zip({**summaries, _NESTED: _zip(fits)})


def _fit(day: int, sub_sport: str = "generic", sport: str = "cycling") -> bytes:
    return make_fit(day, start=_start(day), sport=sport, sub_sport=sub_sport)


@pytest.fixture
def drain(monkeypatch):
    """``run(zip_bytes)`` → ``(stored, counts)``: the REAL archive drain over
    the zip; ``stored`` = the activity_data each created activity would get,
    keyed by its FIT ``day``; ``counts`` = the archive's terminal counts."""
    state: dict = {}

    def fake_ingest_activity(*, user_id, activity_data, contribute_heatmap):
        start = activity_data["activity_date"].replace(tzinfo=UTC)
        state["stored"][(start - _T0).days] = activity_data
        return {"status": "created", "activity_id": f"a{len(state['stored'])}"}

    def finish(archive_id, status, counts, error=None):
        state["status"], state["counts"] = status, dict(counts)

    monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", lambda: None)
    monkeypatch.setattr(drain_mod, "_reap_stuck_archives", lambda db: [])
    monkeypatch.setattr(drain_mod, "_notify_archive_started", lambda *a, **k: None)
    monkeypatch.setattr(drain_mod, "_notify_archive_terminal", lambda *a, **k: None)
    monkeypatch.setattr(drain_mod, "_finish_archive_now", finish)
    monkeypatch.setattr(drain_mod.ingest_service, "ingest_activity", fake_ingest_activity)
    monkeypatch.setattr(archive_intake, "archive_size", lambda b, k: None)
    monkeypatch.setattr("app.db.session.SessionLocal", MagicMock())

    def run(content: bytes):
        state.update(stored={}, status=None, counts=None)

        @contextlib.contextmanager
        def fake_open(backend, key):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                yield zf

        monkeypatch.setattr(archive_intake, "open_archive_zip", fake_open)
        monkeypatch.setattr(drain_mod, "_claim_archives", lambda db, limit: [{
            "id": "arch-garmin", "user_id": "u-garmin", "storage_backend": "local",
            "bucket_key": "k", "fallback_sport": archive_intake.NO_SPORT_HINT,
            "contribute_heatmap": True, "source": "manual_upload", "attempts": 1,
        }])
        drain_mod.drain_pending_archives(limit=1, pace_seconds=0.0, skip_heat_computation=True)
        assert state["status"] == "done", state
        return state["stored"], state["counts"]

    return run


def _sport(activity: dict) -> tuple[str, bool, str]:
    return activity["sport"], activity["sport_is_default"], activity["name"]


# ── 1. The drain, end to end over a synthetic archive ───────────────────────


def test_generic_fit_retyped_gravel_in_connect_is_gravel_with_connect_name(drain):
    """THE bug: generic Bike profile FIT, re-typed gravel_cycling in Connect →
    was road-by-default "Cycling"; now gravel, not default, Connect's name."""
    content = _archive(
        {"rider@example.com_101.fit": _fit(1)},
        {_PART0: _summary(_row(_start(1), "gravel_cycling", "Gravel des Causses"))},
    )
    stored, counts = drain(content)
    assert _sport(stored[1]) == ("gravel", False, "Gravel des Causses")
    assert type(stored[1]["sport"]) is str  # never the DefaultSport marker
    assert counts["imported"] == 1 and counts.get("imported_default_sport", 0) == 0
    assert counts["garmin_connect_matched"] == 1


def test_generic_fit_with_connect_cycling_stays_road_by_default(drain):
    """A bare Connect "cycling" says nothing more than the FIT: road, still
    flagged default (the rider is asked) — but with the Connect name."""
    content = _archive(
        {"rider@example.com_102.fit": _fit(2)},
        {_PART0: _summary(_row(_start(2), "cycling", "Sortie du matin"))},
    )
    stored, counts = drain(content)
    assert _sport(stored[2]) == ("road", True, "Sortie du matin")
    assert counts["imported_default_sport"] == 1


def test_fit_without_summary_row_is_unchanged(drain):
    """Most FIT files have no row (652 rows for 6,275 files on a real export):
    exactly the old behaviour."""
    content = _archive(
        {"rider@example.com_103.fit": _fit(3), "rider@example.com_104.fit": _fit(4, "mountain")},
        {_PART0: _summary(_row(_start(30), "gravel_cycling", "Autre jour"))},
    )
    stored, counts = drain(content)
    assert _sport(stored[3]) == ("road", True, "Cycling")
    assert _sport(stored[4]) == ("mtb", False, "Cycling")
    assert counts.get("garmin_connect_matched", 0) == 0


@pytest.mark.parametrize(
    "summary",
    [
        b"{not json at all",
        b"\x00\xff\xfe garbage",
        b"",
        b'[{"activityId": 1}]',  # no rows array
        b'[{"summarizedActivitiesExport": "nope"}]',
        b'[{"summarizedActivitiesExport": [{"beginTimestamp": "x", "activityType": 3}]}]',
        _summary(_row(_start(5), "gravel_cycling", "Coupe"))[:40].encode(),  # truncated
    ],
)
def test_malformed_summary_never_fails_the_import(drain, summary):
    content = _archive({"rider@example.com_105.fit": _fit(5)}, {_PART0: summary})
    stored, counts = drain(content)
    assert counts["imported"] == 1 and counts["failed"] == 0
    assert _sport(stored[5]) == ("road", True, "Cycling")


def test_explicit_connect_type_beats_the_fit_device_profile(drain):
    """The rider's latest word wins, both ways (not only generic → specific)."""
    content = _archive(
        {
            "rider@example.com_106.fit": _fit(6, "mountain"),  # device: MTB profile
            "rider@example.com_107.fit": _fit(7, "gravel_cycling"),
            "rider@example.com_108.fit": _fit(8, "road"),
        },
        {_PART0: _summary(
            _row(_start(6), "road_biking", "En fait sur route"),
            _row(_start(7), "mountain_biking", "En fait du VTT"),
            _row(_start(8), "running", "Mauvais profil"),
        )},
    )
    stored, _counts = drain(content)
    assert _sport(stored[6]) == ("road", False, "En fait sur route")
    assert _sport(stored[7]) == ("mtb", False, "En fait du VTT")
    assert _sport(stored[8]) == ("running", False, "Mauvais profil")


def test_generic_connect_type_name_refines_but_never_beats_a_fit_discipline(drain):
    content = _archive(
        {
            "rider@example.com_109.fit": _fit(9),  # generic
            "rider@example.com_110.fit": _fit(10, "mountain"),
            "rider@example.com_111.fit": _fit(11),  # generic, Connect type unknown
        },
        {_PART0: _summary(
            _row(_start(9), "cycling", "Sortie VTT au Caroux"),
            _row(_start(10), "cycling", "Gravel ou presque"),
            _row(_start(11), "other", "Tour du lac"),
        )},
    )
    stored, _counts = drain(content)
    assert _sport(stored[9]) == ("mtb", False, "Sortie VTT au Caroux")
    assert _sport(stored[10]) == ("mtb", False, "Gravel ou presque")  # FIT mountain decides
    assert _sport(stored[11]) == ("road", True, "Tour du lac")  # unknown type: no information


def test_connect_indoor_or_virtual_type_skips_the_member(drain):
    content = _archive(
        {"rider@example.com_112.fit": _fit(12), "rider@example.com_113.fit": _fit(13, "road")},
        {_PART0: _summary(
            _row(_start(12), "indoor_cycling", "Home trainer"),
            _row(_start(13), "virtual_ride", "Zwift"),
        )},
    )
    stored, counts = drain(content)
    assert stored == {}
    assert counts["skipped"] == 2 and counts["imported"] == 0


def test_several_parts_junk_and_a_broken_part(drain):
    content = _archive(
        {"rider@example.com_114.fit": _fit(14), "rider@example.com_115.fit": _fit(15)},
        {
            _PART0: _summary(_row(_start(14), "gravel_cycling", "Part zero")),
            _PART1: _summary(_row(_start(15), "mountain_biking", "Part one")),
            "DI_CONNECT/DI-Connect-Fitness/rider@example.com_2_summarizedActivities.json": b"[{",
            "__MACOSX/DI_CONNECT/DI-Connect-Fitness/._rider@example.com_0_summarizedActivities.json":
                _summary(_row(_start(14), "running", "junk")),
        },
    )
    stored, _counts = drain(content)
    assert _sport(stored[14]) == ("gravel", False, "Part zero")
    assert _sport(stored[15]) == ("mtb", False, "Part one")


def test_join_is_by_start_time_within_tolerance(drain):
    content = _archive(
        {
            "rider@example.com_116.fit": _fit(16),
            "rider@example.com_117.fit": _fit(17),
            "rider@example.com_118.fit": _fit(18),
        },
        {_PART0: _summary(
            _row(_start(16, 45), "gravel_cycling", "45 s plus tard"),  # within ±60 s
            _row(_start(17, 90), "gravel_cycling", "Trop loin"),  # outside
            _row(_start(18, -20), "gravel_cycling", "Egalite A"),  # ambiguous tie
            _row(_start(18, 20), "mountain_biking", "Egalite B"),
        )},
    )
    stored, _counts = drain(content)
    assert _sport(stored[16]) == ("gravel", False, "45 s plus tard")
    assert _sport(stored[17]) == ("road", True, "Cycling")
    assert _sport(stored[18]) == ("road", True, "Cycling")


def test_long_connect_name_is_truncated_to_the_column(drain):
    content = _archive(
        {"rider@example.com_119.fit": _fit(19)},
        {_PART0: _summary(_row(_start(19), "road_biking", "  " + "x" * 400 + "  "))},
    )
    stored, _counts = drain(content)
    assert stored[19]["name"] == "x" * 255


# ── 2. Classification of a Connect activity type ────────────────────────────


@pytest.mark.parametrize(
    "activity_type,name,expected",
    [
        ("gravel_cycling", None, ("gravel", None, False)),
        ("cyclocross", None, ("gravel", None, False)),
        ("mountain_biking", None, ("mtb", None, False)),
        ("e_bike_mountain", None, ("mtb", None, False)),
        ("enduro_mtb", None, ("mtb", None, False)),
        ("downhill_biking", None, ("mtb", None, False)),
        ("road_biking", None, ("road", None, False)),
        ("track_cycling", None, ("road", None, False)),
        ("running", None, ("running", None, False)),
        ("trail_running", None, ("running", None, False)),
        ("street_running", None, ("running", None, False)),
        ("hiking", None, ("running", None, False)),
        ("walking", None, ("running", None, False)),
        ("cycling", None, (None, "road", False)),
        ("cycling", "Sortie gravel", (None, "gravel", False)),
        ("cycling", "VTT", (None, "mtb", False)),
        ("e_bike_fitness", None, (None, "road", False)),
        ("indoor_cycling", None, (None, None, True)),
        ("virtual_ride", None, (None, None, True)),
        ("treadmill_running", None, (None, None, True)),
        ("open_water_swimming", None, (None, None, True)),
        ("strength_training", None, (None, None, True)),
        ("other", None, (None, None, False)),
        ("tennis_v2", None, (None, None, False)),
        ("", None, (None, None, False)),
        (None, None, (None, None, False)),
    ],
)
def test_classify_connect_type(activity_type, name, expected):
    info = garmin_connect.classify_connect_type(activity_type, name)
    assert (info.sport, info.hint, info.skip) == expected
    # A bare bike type stays a DEFAULT unless the name refined it.
    assert is_default_sport(info.hint) == (expected[1] == "road")


def test_connect_types_from_the_api_shape_are_read():
    """``activityType`` as the Connect API writes it ({"typeKey": …})."""
    row = _row(_start(0), {"typeKey": "gravel_cycling"}, "API")
    assert garmin_connect._row_activity(row).sport == "gravel"


# ── 3. Index: streaming decode, bounds, lookup ──────────────────────────────


def _index(summaries: dict[str, bytes | str]) -> garmin_connect.ConnectIndex:
    with zipfile.ZipFile(io.BytesIO(_zip(summaries))) as zf:
        return garmin_connect.build_index(zf)


def test_streaming_decode_with_tiny_chunks(monkeypatch):
    """Rows (and the rows key) straddling chunk boundaries decode the same."""
    rows = [_row(_start(d), "gravel_cycling", f"R{d} été") for d in range(20)]
    monkeypatch.setattr(garmin_connect, "_CHUNK_CHARS", 7)
    index = _index({_PART0: _summary(*rows)})
    assert len(index) == 20
    assert index.lookup(_start(13).replace(tzinfo=None)).name == "R13 été"


def test_truncated_part_keeps_its_complete_rows():
    text = _summary(*[_row(_start(d), "mountain_biking", f"R{d}") for d in range(5)])
    cut = text.index('"R3"')  # inside row #3
    index = _index({_PART0: text[:cut]})
    assert len(index) == 3
    assert index.lookup(_start(2)).name == "R2"
    assert index.lookup(_start(3)) is None


def test_oversize_row_abandons_the_part_only(monkeypatch):
    """A row that keeps growing past the cap (never completes within the
    bounded buffer) abandons ITS part; the other parts still count."""
    monkeypatch.setattr(garmin_connect, "_CHUNK_CHARS", 100)
    monkeypatch.setattr(garmin_connect, "_MAX_ROW_CHARS", 2_000)
    big = _row(_start(1), "gravel_cycling", "big", blob="y" * 5_000)
    index = _index({
        _PART0: _summary(_row(_start(0), "gravel_cycling", "ok"), big, _row(_start(2), "running")),
        _PART1: _summary(_row(_start(3), "road_biking", "other part")),
    })
    assert index.lookup(_start(0)).name == "ok"
    assert index.lookup(_start(1)) is None and index.lookup(_start(2)) is None
    assert index.lookup(_start(3)).name == "other part"


def test_row_cap_bounds_the_index(monkeypatch):
    monkeypatch.setattr(garmin_connect, "_MAX_ROWS", 3)
    index = _index({_PART0: _summary(*[_row(_start(d), "cycling") for d in range(10)])})
    assert len(index) == 3


def test_identical_rows_at_the_same_start_are_not_ambiguous():
    row = _row(_start(4), "gravel_cycling", "Doublon")
    index = _index({_PART0: _summary(row), _PART1: _summary(row)})
    assert index.lookup(_start(4)).sport == "gravel"


def test_rows_without_a_usable_start_are_ignored():
    index = _index({_PART0: _summary(
        {"activityType": "gravel_cycling", "name": "no time"},
        {"activityType": "gravel_cycling", "beginTimestamp": True},
        {"activityType": "gravel_cycling", "beginTimestamp": 12},  # not epoch ms
        {"activityType": "gravel_cycling", "startTimeGmt": float(_start(6).timestamp() * 1000)},
    )})
    assert len(index) == 1 and index.lookup(_start(6)).sport == "gravel"


def test_no_summary_and_unusable_start_times_never_match():
    index = _index({"other.json": "{}"})
    assert len(index) == 0 and index.lookup(_start(0)) is None
    index = _index({_PART0: _summary(_row(_start(0), "gravel_cycling"))})
    assert index.lookup(None) is None and index.lookup("2025-05-04") is None


def test_effective_sport_cascade_with_connect():
    parsed = {"sport": GENERIC_ROAD}
    gravel = garmin_connect.ConnectActivity(sport="gravel")
    generic = garmin_connect.classify_connect_type("cycling")
    assert drain_mod._effective_sport_with_default(parsed, None, None, gravel) == ("gravel", False)
    assert drain_mod._effective_sport_with_default(parsed, None, None, generic) == ("road", True)
    assert drain_mod._effective_sport_with_default(parsed, None, None, None) == ("road", True)
    # No FIT sport at all, Connect says "a bike ride": road by default.
    assert drain_mod._effective_sport_with_default({}, None, None, generic) == ("road", True)
    # The rider's explicit pick for the zip still turns a generic road explicit.
    assert drain_mod._effective_sport_with_default(parsed, None, "road", generic) == ("road", False)
