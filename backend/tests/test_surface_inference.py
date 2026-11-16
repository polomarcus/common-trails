"""Off-road inferred from OSM terrain for the default-sport bike rides.

A Garmin "Bike" profile (or a Strava "Ride") says nothing about road / gravel /
MTB: the import files the ride ``road`` + ``sport_is_default``. A ride that runs
mostly on unpaved OSM ways (≥ 15 %) moves to ``offroad`` — still flagged
default (an inference, not the rider's word). Synthetic ways only: a paved road
and an unpaved track, 2 km each, 1.1 km apart.
"""

import json
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services import surface_inference as si

LAT_ROAD, LAT_TRACK = 43.470, 43.480
LON0, LON1 = 3.700, 3.725
ROAD = ({"highway": "tertiary"}, [[LON0, LAT_ROAD], [LON1, LAT_ROAD]])
TRACK = ({"highway": "track", "tracktype": "grade3"}, [[LON0, LAT_TRACK], [LON1, LAT_TRACK]])


def _line(lat, n=60, lon0=LON0, lon1=LON1, jitter=0.00008):
    """A ride along ``lat`` with ±9 m of lateral GPS wobble."""
    return [[lon0 + (lon1 - lon0) * i / (n - 1), lat + (jitter if i % 2 else -jitter)] for i in range(n)]


def _write_tiles(tmp_path, ways):
    tiles = si.tiles_from_ways(ways)
    for name, (p, u) in tiles.items():
        (tmp_path / name).write_bytes(si.encode_tile(p, u))
    (tmp_path / "manifest.json").write_text(json.dumps(si.manifest_for(tiles)))
    return str(tmp_path)


@pytest.fixture
def index(tmp_path):
    return si.load_index(_write_tiles(tmp_path, [ROAD, TRACK]))


# ── 1. OSM way classification ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"highway": "tertiary"}, "p"),
        ({"highway": "residential"}, "p"),
        ({"highway": "tertiary", "surface": "gravel"}, "u"),  # explicit surface wins
        ({"highway": "track"}, "u"),
        ({"highway": "track", "tracktype": "grade1"}, "p"),
        ({"highway": "track", "surface": "asphalt"}, "p"),
        ({"highway": "path"}, "u"),
        ({"highway": "bridleway"}, "u"),
        ({"highway": "path", "bicycle": "designated"}, None),  # often an untagged asphalt voie verte
        ({"highway": "path", "bicycle": "designated", "surface": "compacted"}, "u"),
        ({"highway": "footway"}, None),
        ({"highway": "cycleway"}, "p"),
        ({"highway": "service", "surface": "dirt;grass"}, "u"),
        ({"highway": "proposed"}, None),
        ({"building": "yes"}, None),
    ],
)
def test_classify_way(tags, expected):
    assert si.classify_way(tags) == expected


def test_cell_keys_round_trip_everywhere():
    for lon, lat in [(3.71, 43.47), (-80.5, 26.0), (-0.0001, -33.9), (179.99, -89.0)]:
        x, y = si.key_lonlat(si.cell_key(lon, lat))
        assert abs(x - lon) <= si.GRID_DEG / 2 + 1e-12 and abs(y - lat) <= si.GRID_DEG / 2 + 1e-12
    assert si.tile_name(3.71, 43.47) == "t_86_7.bin"
    assert si.tile_name(-0.1, -0.1) == "t_-1_-1.bin"


def test_tile_codec_round_trip():
    p, u = {1, 5, 9}, {2, 1 << 40}
    assert si.decode_tile(si.encode_tile(p, u)) == (frozenset(p), frozenset(u))
    with pytest.raises(ValueError):
        si.decode_tile(b"nope")


# ── 2. Unpaved share of a ride ───────────────────────────────────────────────


def test_ride_on_the_road_is_paved(index):
    share = si.unpaved_share(_line(LAT_ROAD), index)
    assert share is not None and share.unpaved == 0.0 and share.known >= si.MIN_KNOWN_CELLS


def test_ride_on_the_track_is_unpaved(index):
    share = si.unpaved_share(_line(LAT_TRACK), index)
    assert share is not None and share.unpaved == 1.0


def test_half_and_half(index):
    # Road one way, then 1.1 km across nothing, then the track back.
    coords = _line(LAT_ROAD) + list(reversed(_line(LAT_TRACK)))
    share = si.unpaved_share(coords, index)
    assert share is not None and 0.4 < share.unpaved < 0.6


def test_not_measurable_outside_coverage_or_off_any_way(index):
    assert si.unpaved_share(_line(LAT_ROAD, lon0=5.0, lon1=5.025), index) is None  # no tile
    assert si.unpaved_share(_line(43.4900), index) is None  # covered tile, but no known way
    assert si.unpaved_share(_line(LAT_ROAD, lon1=LON0 + 0.002), index) is None  # too short
    assert si.unpaved_share([], index) is None


def test_a_ride_crossing_a_tile_border(tmp_path):
    # 43.5° is a tile border: a road on both sides, ridden across it.
    road = ({"highway": "secondary"}, [[3.71, 43.49], [3.71, 43.51]])
    idx = si.load_index(_write_tiles(tmp_path, [road]))
    assert len(idx.tiles) == 2
    share = si.unpaved_share([[3.71, 43.49 + 0.02 * i / 59] for i in range(60)], idx)
    assert share is not None and share.unpaved == 0.0


# ── 3. Where the tiles come from ─────────────────────────────────────────────


def test_surface_url(monkeypatch):
    monkeypatch.delenv("OSM_SURFACE_URL", raising=False)
    monkeypatch.delenv("UPLOADS_BUCKET", raising=False)
    assert si.surface_url() is None
    monkeypatch.setenv("UPLOADS_BUCKET", "bkt")
    assert si.surface_url() == "gs://bkt/osm-surface/v1"
    monkeypatch.setenv("OSM_SURFACE_URL", "/data/osm/")
    assert si.surface_url() == "/data/osm"


def test_load_index_missing_or_bad_manifest(tmp_path):
    assert si.load_index(str(tmp_path)) is None
    (tmp_path / "manifest.json").write_text('{"format": 99}')
    assert si.load_index(str(tmp_path)) is None
    (tmp_path / "manifest.json").write_text("not json")
    assert si.load_index(str(tmp_path)) is None
    (tmp_path / "manifest.json").write_text("[1, 2]")
    assert si.load_index(str(tmp_path)) is None


def test_threshold_env(monkeypatch):
    monkeypatch.delenv("OFFROAD_UNPAVED_SHARE", raising=False)
    assert si.threshold() == 0.15
    monkeypatch.setenv("OFFROAD_UNPAVED_SHARE", "0.3")
    assert si.threshold() == 0.3
    for bad in ("x", "0", "2"):
        monkeypatch.setenv("OFFROAD_UNPAVED_SHARE", bad)
        assert si.threshold() == 0.15


# ── 4. Inference over the stored rides ───────────────────────────────────────


@pytest.fixture
def rides():
    """One synthetic account: default rides on the track / on the road /
    outside the tiles, an explicit road ride on the track, a gravel ride."""
    uid = str(uuid.uuid4())
    spec = {
        "def_track": ("road", True, _line(LAT_TRACK)),
        "def_road": ("road", True, _line(LAT_ROAD)),
        "def_elsewhere": ("road", True, _line(LAT_ROAD, lon0=5.0, lon1=5.025)),
        "explicit_road_on_track": ("road", False, _line(LAT_TRACK)),
        "gravel": ("gravel", False, _line(LAT_TRACK)),
    }
    ids = {}
    db = SessionLocal()
    try:
        for key, (sport, dflt, coords) in spec.items():
            ids[key] = str(uuid.uuid4())
            db.execute(
                sa_text(
                    "INSERT INTO activities (id, user_id, provider, source, sport, sport_is_default, name, "
                    "geometry_geojson, contribute_heatmap, activity_date, created_at) "
                    "VALUES (:id, :u, 'file', 'manual_upload', :sp, :d, 'Cycling', :g, true, now(), now())"
                ),
                {
                    "id": ids[key],
                    "u": uid,
                    "sp": sport,
                    "d": dflt,
                    "g": json.dumps({"type": "LineString", "coordinates": coords}),
                },
            )
        db.commit()
    finally:
        db.close()
    yield uid, ids
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": uid})
        db.commit()
    finally:
        db.close()


def _state(ids):
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text("SELECT id::text, sport, sport_is_default FROM activities WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(ids.values())},
        ).fetchall()
    finally:
        db.close()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    return {k: by_id[v] for k, v in ids.items()}


def test_dry_run_reports_and_changes_nothing(index, rides):
    uid, ids = rides
    before = _state(ids)
    db = SessionLocal()
    try:
        rep = si.infer_offroad(db, index, user_id=uid, apply=False)
        db.commit()
    finally:
        db.close()
    assert (rep.examined, rep.offroad, rep.kept_road, rep.unmeasured) == (3, 1, 1, 1)
    assert rep.changed_ids == []
    assert _state(ids) == before


def test_apply_moves_only_default_rides_off_pavement(index, rides):
    uid, ids = rides
    db = SessionLocal()
    try:
        rep = si.infer_offroad(db, index, user_id=uid, apply=True)
        db.commit()
    finally:
        db.close()
    assert rep.changed_ids == [ids["def_track"]]
    st = _state(ids)
    # Still flagged default: an inference, the rider can still say gravel / MTB / road.
    assert st["def_track"] == ("offroad", True)
    assert st["def_road"] == ("road", True)
    assert st["def_elsewhere"] == ("road", True)
    # The rider's own word is never overridden.
    assert st["explicit_road_on_track"] == ("road", False)
    assert st["gravel"] == ("gravel", False)
    # Idempotent: a second pass finds nothing new (offroad rows are not re-examined).
    db = SessionLocal()
    try:
        again = si.infer_offroad(db, index, user_id=uid, apply=True)
        db.commit()
    finally:
        db.close()
    assert again.changed_ids == [] and again.examined == 2


def test_threshold_decides(index, rides):
    uid, ids = rides
    db = SessionLocal()
    try:
        rep = si.infer_offroad(db, index, user_id=uid, apply=False, min_share=1.01)
    finally:
        db.close()
    assert rep.offroad == 0 and rep.kept_road == 2


def test_after_import_hook(tmp_path, monkeypatch, rides):
    uid, ids = rides
    monkeypatch.delenv("UPLOADS_BUCKET", raising=False)
    monkeypatch.delenv("OSM_SURFACE_URL", raising=False)
    assert si.infer_offroad_after_import(uid) == 0  # not configured → nothing
    assert _state(ids)["def_track"] == ("road", True)

    monkeypatch.setenv("OSM_SURFACE_URL", _write_tiles(tmp_path, [ROAD, TRACK]))
    assert si.infer_offroad_after_import(uid) == 1
    assert _state(ids)["def_track"] == ("offroad", True)


def test_after_import_hook_never_raises(monkeypatch, rides):
    uid, _ids = rides

    def boom(*_a, **_k):
        raise RuntimeError("tiles unreadable")

    monkeypatch.setattr(si, "load_index", boom)
    assert si.infer_offroad_after_import(uid) == 0


def test_drain_runs_the_inference_for_the_archive_owner_before_the_ping(tmp_path, monkeypatch):
    """A real archive drain ends with the off-road inference for its owner,
    BEFORE the rebuild ping (so the requested build already files the rides
    right) — and a failing inference never fails the import."""
    from unittest.mock import MagicMock, patch

    from app.jobs import ingest_pending_archives as drain_mod
    from app.services import archive_intake
    from app.services import email as email_service
    from tests import test_archive_terminal_email as tae

    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")
    db = SessionLocal()
    try:
        db.execute(sa_text("TRUNCATE pending_archive_files, pending_archives, contribution_consents"))
        db.commit()
    finally:
        db.close()

    calls: list[str] = []
    real_finished = drain_mod._ArchiveRebuildPinger.archive_finished

    def hook(user_id):
        calls.append(f"infer:{user_id}")
        raise RuntimeError("must not fail the import")  # the real hook never raises; be paranoid

    def ping(self, *a, **k):
        calls.append("ping")
        return real_finished(self, *a, **k)

    monkeypatch.setattr(si, "infer_offroad_after_import", hook)
    monkeypatch.setattr(drain_mod._ArchiveRebuildPinger, "archive_finished", ping)
    uid = tae._mk_user(f"offroad_{uuid.uuid4().hex[:8]}@example.com")
    rid = tae._mk_uploaded_archive(uid)
    with patch.object(email_service, "send_email", MagicMock(return_value=True)):
        try:
            summary = tae._drain()
        except RuntimeError:
            pytest.fail("an inference error must not escape the drain")
    assert calls == [f"infer:{uid}", "ping"]
    assert summary["imported"] == 2
    assert tae._row(rid)["status"] == "done"


def test_cli_dry_run_output_is_anonymised(tmp_path, rides, caplog):
    from app.cli import infer_offroad as cli

    uid, ids = rides
    caplog.set_level("INFO", logger="infer_offroad")
    rep = cli.run(apply=False, user_id=uid, url=_write_tiles(tmp_path, [ROAD, TRACK]))
    assert rep.offroad == 1
    text = caplog.text
    assert "U1: 3 default rides → offroad=1 road=1 unmeasured=1" in text
    assert uid not in text and not any(v in text for v in ids.values())
    assert _state(ids)["def_track"] == ("road", True)
