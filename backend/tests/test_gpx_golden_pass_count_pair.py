"""Golden accumulation tests — pass_count is "how many distinct rides crossed
this edge", driven through the REAL UPSERT (``_update_heat_edges`` →
``_upsert_edges_batch``), never an inline mirror.

Covers (Paul: *"go pour tester (mais pas qu'eux)"*):

  1. TWO real distinct MTB rides that share a sub-path → ``pass_count == 2`` on
     the SHARED edge_keys, ``== 1`` on the divergent parts. Parametrised over
     TWO real pairs (a big-overlap pair + a smaller second pair) so it's not a
     one-fixture fluke.
  2. IDEMPOTENCE — re-ingesting the SAME ride (same user + same activity_id)
     adds NO passes (contributor PK = edge_key+user+activity dedups). Pins the
     "uploaded twice by mistake" case Paul flagged.
  3. CROSS-SPORT — the same physical path ridden as mtb vs gravel produces
     DISJOINT edge_keys (sport-prefixed) → heat never bleeds across sports.

The pairs (real geometries in ``tests/fixtures/``, OSM-matched):
  Afternoon_Mountain_Bike_Ride ∩ Morning_Mountain_Bike_Ride = 14 shared keys
  Morning_Mountain_Bike_Ride   ∩ Mistral_Mountain_Bike_Ride =  3 shared keys
Both resolve to sport=mtb (the named exports carry no ``<trk><type>`` → the
classifier falls back to mtb). Same sport is REQUIRED — heat_edges are
partitioned by sport.

Isolation: ``_update_heat_edges`` opens its own session and COMMITS, so we
snapshot the touched edges' counters up-front, ingest under a UNIQUE throwaway
user + activity_ids, assert the DELTAS, then restore the DB byte-for-byte in a
``finally`` (delete our contributor rows + newly-created edges, restore
pre-existing counters). golden-marked → skips when the occitanie PBF is absent.
"""
import hashlib
import json
import os
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services import ingest
from app.services.gpx import parse_gpx


def _find_gpx(name: str) -> str:
    """Locate a named ride. Committed test fixtures FIRST (always mounted with
    the backend → /app), then the repo ``data/gpx`` as a host fallback."""
    for root in (os.path.join(os.path.dirname(__file__), "fixtures"),
                 "data/gpx", "/app/data/gpx", "../data/gpx",
                 os.path.join(os.path.dirname(__file__), "..", "..", "data", "gpx")):
        p = os.path.join(root, name)
        if os.path.exists(p):
            return p
    return os.path.join("data/gpx", name)  # non-existent → skipif catches it


_GPX_AFTERNOON = _find_gpx("Afternoon_Mountain_Bike_Ride.gpx")
_GPX_MORNING = _find_gpx("Morning_Mountain_Bike_Ride.gpx")
_GPX_MISTRAL = _find_gpx("Mistral_Mountain_Bike_Ride.gpx")
_GPX_GRAVEL = _find_gpx("Morning_Gravel_Ride.gpx")
_SPORT = "mtb"

# Hérault bbox the rides live in — gate when the PBF isn't imported.
_HERAULT_BBOX = (43.40, 3.60, 44.10, 4.20)  # lat0, lon0, lat1, lon1


def _osm_present() -> bool:
    db = SessionLocal()
    try:
        n = db.execute(sa_text(
            "SELECT count(*) FROM osm_road_edges WHERE geometry && "
            "ST_MakeEnvelope(:lon0,:lat0,:lon1,:lat1,4326)"
        ), {"lat0": _HERAULT_BBOX[0], "lon0": _HERAULT_BBOX[1],
            "lat1": _HERAULT_BBOX[2], "lon1": _HERAULT_BBOX[3]}).scalar() or 0
        return n > 1000
    finally:
        db.close()


def _gpx_readable() -> bool:
    return all(os.path.exists(p) for p in (_GPX_AFTERNOON, _GPX_MORNING, _GPX_MISTRAL))


pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not (_osm_present() and _gpx_readable()),
        reason="needs the occitanie OSM PBF in osm_road_edges AND the named "
               "data/gpx fixtures present; CI has neither — run locally.",
    ),
]


def _geojson(path: str) -> str:
    with open(path, "rb") as fh:
        return parse_gpx(fh.read())["geometry_geojson"]


def _match_keys(path: str, db, sport: str = _SPORT) -> set[str]:
    """Read-only: the exact set of edge_keys this ride would produce."""
    coords = json.loads(_geojson(path))["coordinates"]
    edges, _ = ingest._match_to_osm(ingest._resegment_coords(coords), sport, db)
    return {e["edge_key"] for e in edges}


def _read_state(db, keys: list[str], sport: str = _SPORT) -> dict[str, tuple[int, int, int, int]]:
    """(pass_count, user_count, forward_count, backward_count) per edge_key."""
    rows = db.execute(sa_text(
        "SELECT edge_key, pass_count, user_count, forward_count, backward_count "
        "FROM heat_edges WHERE edge_key = ANY(:k) AND sport = :s"
    ), {"k": keys, "s": sport}).fetchall()
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def _restore(all_touched, preexisting, pre, new_keys, user_hashes, sport):
    """Undo everything this test wrote — leave the seed byte-for-byte. The
    UPSERT bumps pass/user AND forward/backward counts, so restore all four."""
    clean = SessionLocal()
    try:
        clean.execute(sa_text(
            "DELETE FROM heat_edge_contributors WHERE user_id_hash = ANY(:h) "
            "AND edge_key = ANY(:k)"
        ), {"h": list(user_hashes), "k": all_touched})
        created = sorted(set(new_keys) - preexisting)
        if created:
            clean.execute(sa_text(
                "DELETE FROM heat_edges WHERE edge_key = ANY(:k) AND sport = :s"
            ), {"k": created, "s": sport})
        for k in preexisting:
            pc, uc, fc, bc = pre[k]
            clean.execute(sa_text(
                "UPDATE heat_edges SET pass_count = :pc, user_count = :uc, "
                "forward_count = :fc, backward_count = :bc "
                "WHERE edge_key = :k AND sport = :s"
            ), {"pc": pc, "uc": uc, "fc": fc, "bc": bc, "k": k, "s": sport})
        clean.commit()
    finally:
        clean.close()


# ── 1. Two rides sharing a path accumulate on the shared segment only ────────

@pytest.mark.parametrize("path_a,path_b,min_shared", [
    pytest.param(_GPX_AFTERNOON, _GPX_MORNING, 10, id="afternoon+morning"),
    pytest.param(_GPX_MORNING, _GPX_MISTRAL, 1, id="morning+mistral"),
])
def test_two_rides_sharing_a_path_accumulate_pass_count(path_a, path_b, min_shared):
    db = SessionLocal()
    try:
        keys_a = _match_keys(path_a, db)
        keys_b = _match_keys(path_b, db)
    finally:
        db.close()

    shared = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    all_touched = sorted(keys_a | keys_b)
    assert len(shared) >= min_shared, (
        f"expected >= {min_shared} shared edge_keys, got {len(shared)} — "
        "the OSM match or the chosen pair regressed"
    )

    user_id = f"_pc_pair_{uuid.uuid4().hex}"
    user_hash = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    act_a, act_b = str(uuid.uuid4()), str(uuid.uuid4())

    snap_db = SessionLocal()
    try:
        pre = _read_state(snap_db, all_touched)
    finally:
        snap_db.close()
    preexisting = set(pre)

    new_a = new_b = ()
    try:
        _, new_a = ingest._update_heat_edges(user_id, _SPORT, _geojson(path_a), activity_id=act_a)
        _, new_b = ingest._update_heat_edges(user_id, _SPORT, _geojson(path_b), activity_id=act_b)

        chk = SessionLocal()
        try:
            post = _read_state(chk, all_touched)
        finally:
            chk.close()

        def delta(k: str) -> int:
            assert k in post, f"edge {k} missing after ingest"
            return post[k][0] - pre.get(k, (0, 0, 0, 0))[0]

        bad_shared = {k: delta(k) for k in shared if delta(k) != 2}
        assert not bad_shared, (
            f"{len(bad_shared)} shared edges did not accumulate to +2 passes: "
            f"{dict(list(bad_shared.items())[:5])}"
        )
        bad_a = {k: delta(k) for k in only_a if delta(k) != 1}
        bad_b = {k: delta(k) for k in only_b if delta(k) != 1}
        assert not bad_a, f"{len(bad_a)} A-only edges did not get +1: {dict(list(bad_a.items())[:5])}"
        assert not bad_b, f"{len(bad_b)} B-only edges did not get +1: {dict(list(bad_b.items())[:5])}"

        present = set(post)
        assert keys_a <= present and keys_b <= present, (
            "after ingesting both rides, every matched edge of each ride must "
            "be present (the merge keeps both traces)"
        )
        for k in shared:
            assert post[k][1] >= 1, f"shared edge {k} must have user_count >= 1"
    finally:
        _restore(all_touched, preexisting, pre, set(new_a) | set(new_b), {user_hash}, _SPORT)


# ── 2. Re-ingesting the same ride is idempotent (uploaded-twice case) ────────

def test_reingesting_same_ride_does_not_double_count():
    """The SAME ride re-ingested under the SAME user + activity_id must add NO
    passes — the contributor PK (edge_key, user, activity) dedups. Guards the
    'uploaded the same GPX twice by mistake' case (Paul, byte-identical pair)."""
    db = SessionLocal()
    try:
        keys = sorted(_match_keys(_GPX_MORNING, db))
    finally:
        db.close()

    user_id = f"_pc_idem_{uuid.uuid4().hex}"
    user_hash = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    act = str(uuid.uuid4())  # SAME activity_id both times

    snap_db = SessionLocal()
    try:
        pre = _read_state(snap_db, keys)
    finally:
        snap_db.close()
    preexisting = set(pre)

    new_all = set()
    try:
        geo = _geojson(_GPX_MORNING)

        def _snapshot() -> dict:
            s = SessionLocal()
            try:
                return _read_state(s, keys)
            finally:
                s.close()

        _, n1 = ingest._update_heat_edges(user_id, _SPORT, geo, activity_id=act)
        after_first = _snapshot()
        # Second identical ingest — must be a no-op for pass_count.
        _, n2 = ingest._update_heat_edges(user_id, _SPORT, geo, activity_id=act)
        after_second = _snapshot()
        new_all = set(n1) | set(n2)

        bumped = {k: (after_first[k][0], after_second[k][0])
                  for k in after_second
                  if k in after_first and after_second[k][0] != after_first[k][0]}
        assert not bumped, (
            f"{len(bumped)} edges gained passes on a duplicate re-ingest "
            f"(same user+activity): {dict(list(bumped.items())[:5])}"
        )
        # And the net effect of the first ingest was exactly +1 everywhere new.
        for k in keys:
            assert after_first[k][0] - pre.get(k, (0, 0, 0, 0))[0] == 1, (
                f"first ingest should be +1 on {k}"
            )
    finally:
        _restore(keys, preexisting, pre, new_all, {user_hash}, _SPORT)


# ── 3. Cross-sport negative control — heat never bleeds across sports ────────

def test_same_path_different_sport_keys_are_disjoint():
    """Ingesting the same physical path as mtb vs gravel must NOT accumulate:
    edge_keys are sport-prefixed, so the two sets are disjoint and neither
    sport's pass_count can be lifted by the other. Read-only (the partition is
    structural — no writes needed to prove it)."""
    db = SessionLocal()
    try:
        keys_mtb = _match_keys(_GPX_AFTERNOON, db, sport="mtb")
        keys_gravel = _match_keys(_GPX_AFTERNOON, db, sport="gravel")
    finally:
        db.close()

    assert keys_mtb and keys_gravel, "both sports must match the path"
    crossover = keys_mtb & keys_gravel
    assert not crossover, (
        f"{len(crossover)} edge_keys shared across mtb/gravel for the SAME path "
        "— heat would bleed across sports (sport prefix regressed)"
    )
