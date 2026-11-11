"""``activities.sport_is_default`` — a sport that is only a generic default.

A Strava export calls most bike rides just "Ride": they are imported as
``road`` for lack of a better signal, and flagged so the rider can classify
them. Covers the classifier, the ingest cascade, the API (count / list / bulk
re-classify / confirm), the "Ma heatmap" summary, the done email and
migration 0071 (up / down / up + backfill).
"""

import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal, engine
from app.jobs import ingest_pending_archives as drain
from app.services import archive_intake
from app.services import gpx as gpx_service
from app.services import my_heatmap_files as mhf
from app.services.strava_utils import (
    GENERIC_ROAD,
    classify_sport,
    classify_sport_with_default,
    is_default_sport,
    resolve_sport_cascade,
    resolve_sport_cascade_with_default,
)
from tests.test_me_activities_delete import _make_gpx_bytes, _register_second_user, _upload
from tests.test_sport_classification import _make_fit

# ── 1. Classifier ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "activity_type,name,expected",
    [
        ("Ride", None, ("road", True)),  # generic, nothing refines it
        ("Ride", "Sortie du dimanche", ("road", True)),
        ("E-Bike Ride", None, ("road", True)),
        ("Vélo", None, ("road", True)),  # French UI label
        ("Gravel Ride", None, ("gravel", False)),  # specific type
        ("Mountain Bike Ride", None, ("mtb", False)),
        ("Ride", "Sortie gravel", ("gravel", False)),  # name refines
        ("Ride", "VTT au Caroux", ("mtb", False)),
        ("Road Ride", None, ("road", False)),  # the type DOES say road
        ("Vélo de route", None, ("road", False)),
        ("Run", None, ("running", False)),
        ("Virtual Ride", None, (None, False)),  # skipped
        ("Underwater Hockey", None, (None, False)),  # unknown
        (None, None, (None, False)),
    ],
)
def test_classify_sport_with_default(activity_type, name, expected):
    assert classify_sport_with_default(activity_type, name) == expected
    # The historical signature is untouched.
    assert classify_sport(activity_type, name) == expected[0]


@pytest.mark.parametrize(
    "candidates,expected",
    [
        ((GENERIC_ROAD,), ("road", True)),
        ((GENERIC_ROAD, GENERIC_ROAD), ("road", True)),  # CSV "Ride" + FIT Bike profile
        ((GENERIC_ROAD, "road"), ("road", False)),  # FIT sub_sport=road / explicit pick
        ((GENERIC_ROAD, None, "road"), ("road", False)),
        ((GENERIC_ROAD, "gravel"), ("gravel", False)),  # refined by a later source
        (("road",), ("road", False)),
        (("gravel", GENERIC_ROAD), ("gravel", False)),
        (("running",), ("running", False)),
        ((None, "unknown"), (None, False)),
    ],
)
def test_cascade_with_default(candidates, expected):
    assert resolve_sport_cascade_with_default(*candidates) == expected


def test_cascade_never_leaks_the_marker():
    out = resolve_sport_cascade(GENERIC_ROAD)
    assert out == "road" and type(out) is str


def test_csv_marks_generic_rides_only():
    csv = (
        b"Activity ID,Activity Name,Activity Type,Filename\n"
        b"1,Morning Ride,Ride,activities/1.gpx\n"
        b"2,Sortie gravel,Ride,activities/2.gpx\n"
        b"3,Col,Gravel Ride,activities/3.gpx\n"
        b"4,Footing,Run,activities/4.gpx\n"
    )
    out = gpx_service.parse_strava_activities_csv(csv)
    assert out == {
        "activities/1.gpx": "road",
        "activities/2.gpx": "gravel",
        "activities/3.gpx": "gravel",
        "activities/4.gpx": "running",
    }
    assert {k for k, v in out.items() if is_default_sport(v)} == {"activities/1.gpx"}


def test_fit_and_gpx_generic_types_are_marked():
    from app.services.fit_parser import _fit_sport_from_values

    assert is_default_sport(_fit_sport_from_values("cycling", "generic"))
    assert not is_default_sport(_fit_sport_from_values("cycling", "road"))
    assert is_default_sport(gpx_service.gpx_track_type_skip_or_sport("EBikeRide")[0])
    assert not is_default_sport(gpx_service.gpx_track_type_skip_or_sport("road_cycling")[0])


def test_legacy_member_queue_stores_a_plain_sport():
    """The per-member queue column keeps its historical values (no marker)."""
    assert archive_intake.split_queued_sport("road") == ("road", None)
    assert archive_intake.split_queued_sport("pick:road") == (None, "road")


# ── 2. Ingest stores the flag ────────────────────────────────────────────────


def _flag(activity_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.execute(
            sa_text("SELECT sport_is_default FROM activities WHERE id = :a"), {"a": activity_id}
        ).scalar()
    finally:
        db.close()


def _start() -> tuple[float, float]:
    j = (uuid.uuid4().int % 100_000) * 1e-5
    return (-120.0 + j, -30.0 + j / 2)  # South Pacific — no OSM, no other suite


@pytest.fixture
def drain_user():
    uid = str(uuid.uuid4())
    yield uid
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": uid})
        db.execute(sa_text("DELETE FROM my_heatmap_files WHERE user_id = :u"), {"u": uid})
        db.commit()
    finally:
        db.close()


def _drain(user_id, name, csv_sport, raw=None, fallback_sport=None, stats=None):
    lon, lat = _start()
    return drain._ingest_member_bytes(
        user_id=user_id,
        filename=f"activities/{name}.{'fit' if raw else 'gpx'}",
        raw=raw or _make_gpx_bytes(name, lon, lat),
        resolved_sport=csv_sport,
        fallback_sport=fallback_sport,
        contribute_heatmap=False,
        source="manual_upload",
        skip_heat_computation=True,
        stats=stats,
    )


def test_archive_member_generic_ride_is_flagged(drain_user):
    stats: dict = {}
    out, aid, _ = _drain(drain_user, "generic", GENERIC_ROAD, stats=stats)
    assert out == "imported" and _flag(aid) is True
    out, aid, _ = _drain(drain_user, "gravel", "gravel", stats=stats)
    assert out == "imported" and _flag(aid) is False
    # "Road Ride" in the CSV: a real classification.
    out, aid, _ = _drain(drain_user, "plain-road", "road", stats=stats)
    assert out == "imported" and _flag(aid) is False
    # No CSV row, the rider picked road for the zip: an explicit choice.
    out, aid, _ = _drain(drain_user, "picked", None, fallback_sport="road", stats=stats)
    assert out == "imported" and _flag(aid) is False
    assert stats == {"imported_default_sport": 1}


def test_archive_member_fit_cascade(captured):
    # CSV "Ride" + FIT generic Bike profile → still nobody said road.
    _drain("u", "a", GENERIC_ROAD, raw=_make_fit("cycling", "generic"))
    # CSV "Ride" + FIT sub_sport=road → the device said road.
    _drain("u", "b", GENERIC_ROAD, raw=_make_fit("cycling", "road"))
    # CSV "Ride" + FIT mountain → mtb.
    _drain("u", "c", GENERIC_ROAD, raw=_make_fit("cycling", "mountain"))
    # No CSV, FIT generic, no pick → road by default.
    _drain("u", "d", None, raw=_make_fit("cycling", "generic"))
    # No CSV, FIT generic, the rider picked road → explicit.
    _drain("u", "e", None, raw=_make_fit("cycling", "generic"), fallback_sport="road")
    got = [(c["sport"], c["sport_is_default"]) for c in captured]
    assert got == [("road", True), ("road", False), ("mtb", False), ("road", True), ("road", False)]
    assert all(type(c["sport"]) is str for c in captured)


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs["activity_data"])
        return {"status": "created", "activity_id": f"a{len(calls)}"}

    monkeypatch.setattr(drain.ingest_service, "ingest_activity", _fake)
    return calls


def test_dropzone_upload_with_picked_sport_is_not_a_default(client, auth_headers):
    lon, lat = _start()
    aid = _upload(client, auth_headers, "Picked", lon, lat, contribute=False)
    assert _flag(aid) is False
    feat = client.get(f"/me/activities/{aid}", headers=auth_headers).json()
    assert feat["properties"]["sport_is_default"] is False


# ── 3. API ───────────────────────────────────────────────────────────────────

_DAY0 = datetime(2025, 3, 1, 8, 0, tzinfo=UTC)


def _user_id(client, headers) -> str:
    return client.get("/auth/me", headers=headers).json()["user_id"]


def _seed(user_id: str, n: int, *, default: bool = True, sport: str = "road") -> list[str]:
    """``n`` activities, the LAST one the most recent."""
    db = SessionLocal()
    ids = []
    try:
        for i in range(n):
            lon, lat = _start()
            aid = str(uuid.uuid4())
            db.add(
                Activity(
                    id=aid,
                    user_id=user_id,
                    provider="file",
                    source="manual_upload",
                    sport=sport,
                    sport_is_default=default,
                    name=f"Ride {i}",
                    geometry_geojson=json.dumps(
                        {"type": "LineString", "coordinates": [[lon, lat], [lon + 0.01, lat + 0.01]]}
                    ),
                    distance_m=1000.0 * (i + 1),
                    activity_date=_DAY0 + timedelta(days=i),
                    contribute_heatmap=False,
                )
            )
            ids.append(aid)
        db.commit()
    finally:
        db.close()
    return ids


def _status(client, headers, **params) -> dict:
    resp = client.get("/me/activities/default-sport", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_default_sport_requires_auth(client):
    assert client.get("/me/activities/default-sport").status_code == 401


def test_default_sport_count_and_list(client, auth_headers):
    uid = _user_id(client, auth_headers)
    assert _status(client, auth_headers) == {"count": 0, "activities": []}
    ids = _seed(uid, 3)
    _seed(uid, 2, default=False)
    body = _status(client, auth_headers, limit=2)
    assert body["count"] == 3
    # Newest first, capped, no geometry.
    assert [a["id"] for a in body["activities"]] == [ids[2], ids[1]]
    assert body["activities"][0] == {
        "id": ids[2],
        "name": "Ride 2",
        "sport": "road",
        "distance_m": 3000.0,
        "activity_date": (_DAY0 + timedelta(days=2)).isoformat(),
    }
    # Another account sees nothing of it.
    assert _status(client, _register_second_user(client))["count"] == 0


def test_me_activities_features_carry_the_flag(client, auth_headers):
    uid = _user_id(client, auth_headers)
    (flagged,) = _seed(uid, 1)
    (plain,) = _seed(uid, 1, default=False, sport="gravel")
    feats = client.get("/me/activities", headers=auth_headers).json()["features"]
    by_id = {f["properties"]["id"]: f["properties"]["sport_is_default"] for f in feats}
    assert by_id == {flagged: True, plain: False}


def test_single_patch_clears_the_flag_on_change_and_on_confirm(client, auth_headers):
    uid = _user_id(client, auth_headers)
    changed, confirmed, untouched = _seed(uid, 3)
    r = client.patch(f"/me/activities/{changed}", headers=auth_headers, json={"sport": "mtb"})
    assert r.status_code == 200 and r.json()["changed"] is True
    r = client.patch(f"/me/activities/{confirmed}", headers=auth_headers, json={"sport": "road"})
    assert r.status_code == 200 and r.json()["changed"] is False
    assert (_flag(changed), _flag(confirmed), _flag(untouched)) == (False, False, True)
    assert _status(client, auth_headers)["count"] == 1


def test_bulk_ids_clear_the_flag(client, auth_headers):
    uid = _user_id(client, auth_headers)
    a, b, c = _seed(uid, 3)
    r = client.patch(
        "/me/activities/sport", headers=auth_headers, json={"activity_ids": [a, b], "sport": "gravel"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["matched"], body["updated"], body["default_sport_count"]) == (2, 2, 1)
    assert (_flag(a), _flag(b), _flag(c)) == (False, False, True)


def test_bulk_from_default_reclassifies_all(client, auth_headers):
    uid = _user_id(client, auth_headers)
    ids = _seed(uid, 4)
    (other,) = _seed(uid, 1, default=False)  # a road ride the rider already confirmed
    theirs = _seed(_user_id(client, _register_second_user(client)), 1)[0]
    r = client.patch(
        "/me/activities/sport", headers=auth_headers, json={"from_default": True, "sport": "gravel"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sport"] == "gravel"
    assert (body["matched"], body["updated"], body["default_sport_count"]) == (4, 4, 0)
    assert sorted(body["updated_ids"]) == sorted(ids)
    db = SessionLocal()
    try:
        rows = dict(
            db.execute(
                sa_text("SELECT id::text, sport FROM activities WHERE id = ANY(CAST(:i AS uuid[]))"),
                {"i": ids + [other, theirs]},
            ).fetchall()
        )
    finally:
        db.close()
    assert [rows[i] for i in ids] == ["gravel"] * 4
    assert rows[other] == "road" and rows[theirs] == "road"
    assert _flag(theirs) is True  # someone else's stays untouched


def test_bulk_from_default_confirm_road_only_clears_the_flag(client, auth_headers):
    uid = _user_id(client, auth_headers)
    ids = _seed(uid, 3)
    r = client.patch(
        "/me/activities/sport", headers=auth_headers, json={"from_default": True, "sport": "road"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["matched"], body["updated"], body["updated_ids"]) == (3, 0, [])
    assert body["default_sport_count"] == 0
    assert [_flag(i) for i in ids] == [False] * 3


@pytest.mark.parametrize(
    "payload",
    [
        {"sport": "gravel"},  # no target
        {"sport": "gravel", "from_default": False},
        {"sport": "gravel", "from_default": True, "activity_ids": [str(uuid.uuid4())]},  # both
        {"sport": "offroad", "from_default": True},
    ],
)
def test_bulk_target_validation(client, auth_headers, payload):
    assert client.patch("/me/activities/sport", headers=auth_headers, json=payload).status_code == 422


# ── 4. "Ma heatmap" file ─────────────────────────────────────────────────────


def test_summary_entry_carries_the_flag_and_defaults_to_false():
    parts = [[[1.0, 2.0], [1.1, 2.1]]]
    assert mhf._summary_entry({"id": "a", "sport_is_default": True}, parts)["sport_is_default"] is True
    # A row dict without the key (older caller) reads as "not a default".
    assert mhf._summary_entry({"id": "a"}, parts)["sport_is_default"] is False


def test_flag_is_not_a_watched_column():
    """A mass UPDATE of the flag (the 0071 backfill) must not mark every
    rider's private file stale."""
    assert "sport_is_default" not in mhf.DISPLAYED_COLUMNS
    with engine.connect() as conn:
        src = conn.execute(
            sa_text("SELECT prosrc FROM pg_proc WHERE proname = 'my_heatmap_bump_upd'")
        ).scalar()
    assert src and "sport_is_default" not in src


def _data_rev(db, user_id: str) -> int:
    return int(
        db.execute(
            sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": user_id}
        ).scalar()
        or 0
    )


@pytest.fixture
def triggers_on():
    db = SessionLocal()
    try:
        before = db.execute(sa_text("SELECT enabled FROM my_heatmap_config WHERE id = 1")).scalar()
        db.execute(sa_text("UPDATE my_heatmap_config SET enabled = true WHERE id = 1"))
        db.commit()
        yield
        db.execute(sa_text("UPDATE my_heatmap_config SET enabled = :e WHERE id = 1"), {"e": bool(before)})
        db.commit()
    finally:
        db.close()


def test_confirming_refreshes_only_that_riders_file(client, auth_headers, triggers_on):
    """The summary carries the flag: a confirmation (flag only, no watched
    column changes) must still bump the rider's own file — and nobody else's."""
    uid = _user_id(client, auth_headers)
    other = _user_id(client, _register_second_user(client))
    ids = _seed(uid, 2)
    _seed(other, 1)
    db = SessionLocal()
    try:
        rev, other_rev = _data_rev(db, uid), _data_rev(db, other)
        # Raw flag-only UPDATE (what the backfill does): no bump.
        db.execute(sa_text("UPDATE activities SET sport_is_default = true WHERE user_id = :u"), {"u": uid})
        db.commit()
        assert _data_rev(db, uid) == rev
    finally:
        db.close()
    r = client.patch(f"/me/activities/{ids[0]}", headers=auth_headers, json={"sport": "road"})
    assert r.status_code == 200
    r = client.patch(
        "/me/activities/sport", headers=auth_headers, json={"from_default": True, "sport": "road"}
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert _data_rev(db, uid) == rev + 2
        assert _data_rev(db, other) == other_rev
    finally:
        db.close()


# ── 5. Done email ────────────────────────────────────────────────────────────


def test_done_email_mentions_default_sport_rides():
    from app.services.email import render_archive_done_email

    _s, html = render_archive_done_email(
        imported=10, skipped=0, failed=0, locale="fr", imported_default_sport=7
    )
    assert "<strong>7</strong> sorties vélo classées « Route » par défaut" in html
    assert "tu peux les reclasser depuis la carte" in html
    _s, html = render_archive_done_email(
        imported=10, skipped=0, failed=0, locale="en", imported_default_sport=1
    )
    assert "<strong>1</strong> bike ride filed under Road by default" in html
    for locale in ("fr", "en"):
        _s, html = render_archive_done_email(imported=10, skipped=0, failed=0, locale=locale)
        assert "par défaut" not in html and "by default" not in html


# ── 6. Migration 0071 ────────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0071_activities_sport_is_default.py"
    spec = importlib.util.spec_from_file_location("migration_0071", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_alembic():
    """The installed ``alembic`` package. Under pytest the working directory
    (``backend/``) is on ``sys.path`` and its ``alembic/`` folder shadows the
    package as a namespace — import the real one with that entry set aside."""
    saved = sys.path[:]
    try:
        sys.path[:] = [p for p in sys.path if not (Path(p or ".") / "alembic" / "versions").is_dir()]
        for name in [m for m in sys.modules if m == "alembic" or m.startswith("alembic.")]:
            if getattr(sys.modules[name], "__file__", None) is None:
                del sys.modules[name]
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
    finally:
        sys.path[:] = saved
    return MigrationContext, Operations


def _has_column(conn) -> bool:
    return bool(
        conn.execute(
            sa_text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'activities' AND column_name = 'sport_is_default'"
            )
        ).scalar()
    )


def test_migration_0071_up_down_up_and_backfill():
    """Runs the real ``downgrade()`` / ``upgrade()`` on seeded rows inside ONE
    transaction that is rolled back (DDL is transactional in Postgres)."""
    MigrationContext, Operations = _real_alembic()
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0071", "0070")
    uid = str(uuid.uuid4())
    # (sport, source, provider, geometry_source) → expected flag after backfill
    cases = {
        "archive road": (("road", "manual_upload", "file", "polyline"), True),
        "archive road, no geometry_source": (("road", "manual_upload", "file", None), True),
        "archive gravel": (("gravel", "manual_upload", "file", "polyline"), False),
        "dropzone road (sport picked)": (("road", "manual_upload", "file", "stream"), False),
        "strava api road": (("road", "strava_api", "strava", "polyline"), False),
        "promoted api twin": (("road", "manual_upload", "strava", "stream"), False),
        "archive running": (("running", "manual_upload", "file", "polyline"), False),
    }
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(sa_text("SET LOCAL lock_timeout = '20s'"))
            conn.execute(sa_text("UPDATE my_heatmap_config SET enabled = true WHERE id = 1"))
            for name, ((sport, source, provider, gsrc), _exp) in cases.items():
                conn.execute(
                    sa_text(
                        "INSERT INTO activities (id, user_id, provider, source, sport, name, geometry_source) "
                        "VALUES (:id, :u, :p, :s, :sp, :n, :g)"
                    ),
                    {"id": str(uuid.uuid4()), "u": uid, "p": provider, "s": source, "sp": sport, "n": name, "g": gsrc},
                )
            rev = conn.execute(
                sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
            ).scalar()

            def flags() -> dict:
                return dict(
                    conn.execute(
                        sa_text("SELECT name, sport_is_default FROM activities WHERE user_id = :u"), {"u": uid}
                    ).fetchall()
                )

            def sports() -> dict:
                return dict(
                    conn.execute(sa_text("SELECT name, sport FROM activities WHERE user_id = :u"), {"u": uid}).fetchall()
                )

            before = sports()
            with Operations.context(MigrationContext.configure(conn)):
                assert _has_column(conn)  # the suite runs at head
                mod.downgrade()
                assert not _has_column(conn)
                mod.upgrade()
                assert flags() == {name: exp for name, (_row, exp) in cases.items()}
                mod.downgrade()
                assert not _has_column(conn)
                mod.upgrade()
                assert flags() == {name: exp for name, (_row, exp) in cases.items()}
            assert sports() == before  # ``sport`` itself is never touched
            # The backfill marked no private heatmap file stale…
            assert (
                conn.execute(
                    sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
                ).scalar()
                == rev
            )
            # …and left the staleness trigger enabled.
            assert (
                conn.execute(
                    sa_text(
                        "SELECT tgenabled FROM pg_trigger WHERE tgname = 'activities_my_heatmap_upd' "
                        "AND tgrelid = 'activities'::regclass"
                    )
                ).scalar()
                == "O"
            )
            conn.execute(sa_text("UPDATE activities SET name = 'renamed' WHERE user_id = :u"), {"u": uid})
            assert (
                conn.execute(
                    sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
                ).scalar()
                == rev + 1
            )
        finally:
            trans.rollback()
    with engine.connect() as conn:
        assert _has_column(conn)
