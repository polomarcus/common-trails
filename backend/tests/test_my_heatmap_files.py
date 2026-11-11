"""Private per-user "Ma heatmap" file (docs/ma-heatmap-fichier-prive.md).

Three layers are pinned here:

1. STALENESS — the DB triggers (alembic 0068) bump ``my_heatmap_files.data_rev``
   on every displayed-column change of ``activities``, whatever the write path.
2. INVALIDATION — every path that changes what the user sees schedules a
   (debounced, out-of-request) rebuild of THAT user's file: imports (GPX upload,
   single + bulk/API ingest), duplicate promotion, deletion (API + Strava
   webhook), sport change (single + bulk), Strava geometry upgrade, account
   merge, GDPR deletion. Each test FAILS on code that forgets the path.
3. BUILD + ACCESS + PRIVACY — the file is built with tippecanoe, served only
   through the authenticated Range proxy (never another user's), never
   public-cacheable, never written to a public bucket; the deploy script
   asserts the private bucket's public-access-prevention.
"""

from __future__ import annotations

import ast
import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity, User
from app.db.session import SessionLocal
from app.services import my_heatmap_files as mhf

_DATE = datetime(2025, 5, 4, 8, 0, tzinfo=UTC)


# ── fixtures / helpers ───────────────────────────────────────────────────────


def _set_triggers(on: bool) -> None:
    db = SessionLocal()
    try:
        db.execute(sa_text("UPDATE my_heatmap_config SET enabled = :e WHERE id = 1"), {"e": on})
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _triggers_on():
    """The staleness triggers are no-ops until the feature is activated in
    the DB (first build) — these tests exercise them active."""
    _set_triggers(True)
    yield
    _set_triggers(False)  # the rest of the suite runs like flag-off prod


def test_triggers_are_noop_until_activated(users):
    """Flag-off prod: migration 0068 must not write or lock anything on a
    write to activities until the first build activates the triggers."""
    _set_triggers(False)
    try:
        db = SessionLocal()
        try:
            uid = _mk_user(db)
            users.append(uid)
            _mk_activity(db, uid)
            db.commit()
            assert db.execute(
                sa_text("SELECT count(*) FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
            ).scalar() == 0
            # The first build turns them on (before reading data_rev).
            mhf._activate_triggers(db)
            db.commit()
            _mk_activity(db, uid)
            db.commit()
            assert _rev(uid) >= 1
        finally:
            db.close()
    finally:
        _set_triggers(True)


def test_gdpr_delete_without_bucket_does_not_crash(monkeypatch, users):
    class _Missing(Exception):
        pass

    _Missing.__name__ = "NotFound"

    class _Store:
        def list_keys(self, prefix):
            raise _Missing("bucket does not exist")

    monkeypatch.setattr(mhf, "get_store", lambda: _Store())
    assert mhf.delete_for_user(str(uuid.uuid4())) == 0


@pytest.fixture
def mhf_env(monkeypatch, tmp_path):
    """Flag on, local store, scheduler recorded instead of enqueued."""
    monkeypatch.setenv("MY_HEATMAP_FILES_ENABLED", "true")
    monkeypatch.setenv("MY_HEATMAP_LOCAL_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("MY_HEATMAP_BUCKET", raising=False)
    monkeypatch.delenv("MY_HEATMAP_FILES_USERS", raising=False)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(mhf, "_recorder", recorded)
    yield recorded


def _scheduled(recorded, user_id: str) -> bool:
    return any(u == user_id for u, _ in recorded)


def _rev(user_id: str) -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": user_id}
            ).scalar()
            or 0
        )
    finally:
        db.close()


def _line(lon0: float, lat0: float, n: int = 6, step: float = 0.002) -> str:
    return json.dumps(
        {"type": "LineString", "coordinates": [[lon0 + i * step, lat0 + i * step, 50 + i] for i in range(n)]}
    )


def _start() -> tuple[float, float]:
    j = (uuid.uuid4().int % 100_000) * 1e-5
    return (-150.0 + j, -40.0 + j / 2)  # South Pacific — no OSM, no other suite


def _mk_user(db, email: str | None = None, password: str | None = "x") -> str:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=email or f"mhf_{uid[:8]}@example.com", username="u", hashed_password=password))
    db.flush()
    return uid


def _mk_activity(db, user_id: str, **kw) -> str:
    lon, lat = _start()
    aid = str(uuid.uuid4())
    db.add(
        Activity(
            id=aid,
            user_id=user_id,
            provider=kw.pop("provider", "file"),
            source=kw.pop("source", "manual_upload"),
            sport=kw.pop("sport", "road"),
            name=kw.pop("name", "ride"),
            geometry_geojson=kw.pop("geometry_geojson", _line(lon, lat)),
            distance_m=kw.pop("distance_m", 1000.0),
            activity_date=kw.pop("activity_date", _DATE),
            contribute_heatmap=False,
            **kw,
        )
    )
    db.flush()
    return aid


def _cleanup(user_ids):
    db = SessionLocal()
    try:
        for u in user_ids:
            db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": u})
            db.execute(sa_text("DELETE FROM my_heatmap_files WHERE user_id = :u"), {"u": u})
            db.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": u})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def users():
    created: list[str] = []
    yield created
    _cleanup(created)


def _user_id_of(client, headers) -> str:
    return client.get("/auth/me", headers=headers).json()["user_id"]


# ── 1. staleness: the triggers ───────────────────────────────────────────────


def test_hook_columns_match_trigger():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0069_my_heatmap_shares.py"
    tree = ast.parse(path.read_text())
    cols = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_DISPLAYED_COLUMNS"
    )
    assert tuple(cols) == mhf.DISPLAYED_COLUMNS


def test_trigger_bumps_on_raw_sql_writes(users):
    """Raw SQL (no ORM) must still mark the file stale — the trigger is the
    exhaustive backstop for write paths nobody remembered to hook."""
    db = SessionLocal()
    try:
        u1, u2 = _mk_user(db), _mk_user(db)
        users += [u1, u2]
        db.commit()
        aid = str(uuid.uuid4())
        db.execute(
            sa_text(
                "INSERT INTO activities (id, user_id, provider, sport, geometry_geojson, created_at) "
                "VALUES (:a, :u, 'file', 'road', :g, now())"
            ),
            {"a": aid, "u": u1, "g": _line(*_start())},
        )
        db.commit()
        r0 = _rev(u1)
        assert r0 >= 1

        db.execute(sa_text("UPDATE activities SET name = 'x' WHERE id = :a"), {"a": aid})
        db.commit()
        assert _rev(u1) == r0 + 1

        # A column the file does not display → no bump (no useless rebuild).
        db.execute(sa_text("UPDATE activities SET contribute_heatmap = true WHERE id = :a"), {"a": aid})
        db.commit()
        assert _rev(u1) == r0 + 1

        # Ownership change (account merge) → BOTH users stale.
        r2 = _rev(u2)
        db.execute(sa_text("UPDATE activities SET user_id = :u2 WHERE id = :a"), {"a": aid, "u2": u2})
        db.commit()
        assert _rev(u1) == r0 + 2
        assert _rev(u2) == r2 + 1

        db.execute(sa_text("DELETE FROM activities WHERE id = :a"), {"a": aid})
        db.commit()
        assert _rev(u2) == r2 + 2
    finally:
        db.close()


def test_rollback_schedules_nothing(mhf_env, users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        _mk_activity(db, uid)
        db.rollback()
    finally:
        db.close()
    assert mhf_env == []


def test_flag_off_schedules_nothing(monkeypatch, users):
    monkeypatch.setenv("MY_HEATMAP_FILES_ENABLED", "false")
    recorded: list = []
    monkeypatch.setattr(mhf, "_recorder", recorded)
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.commit()
    finally:
        db.close()
    assert recorded == []


# ── 2. invalidation paths ────────────────────────────────────────────────────


def test_import_gpx_upload_schedules(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    _upload(client, auth_headers, "mhf-up", *_start(), contribute=False)
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) >= 1


def test_import_single_and_bulk_ingest_schedule(mhf_env, users):
    """Strava API / webhook (``ingest_activity``) and archive / API batch
    (``ingest_activities_bulk``) ingest paths."""
    from app.services.ingest import ingest_activities_bulk, ingest_activity

    db = SessionLocal()
    try:
        u1, u2 = _mk_user(db), _mk_user(db)
        users += [u1, u2]
        db.commit()
    finally:
        db.close()

    res = ingest_activity(
        u1,
        {
            "provider": "strava",
            "provider_activity_id": f"mhf-{uuid.uuid4().hex[:8]}",
            "source": "strava_api",
            "sport": "road",
            "geometry_geojson": _line(*_start()),
            "distance_m": 1200,
            "activity_date": _DATE,
        },
        contribute_heatmap=False,
        skip_heat_computation=True,
    )
    assert res["status"] == "created"
    assert _scheduled(mhf_env, u1)

    out = ingest_activities_bulk(
        u2,
        [
            {
                "provider": "strava",
                "provider_activity_id": f"mhf-b-{i}-{uuid.uuid4().hex[:6]}",
                "sport": "gravel",
                "geometry_geojson": _line(*_start()),
                "distance_m": 900,
                "activity_date": _DATE + timedelta(hours=i),
            }
            for i in range(3)
        ],
        contribute_heatmap=False,
        source="strava_api",
    )
    assert out.get("created", out.get("ingested", 0)) or _rev(u2) >= 1
    assert _scheduled(mhf_env, u2)


def test_duplicate_promotion_schedules(mhf_env, users):
    from app.services.ingest import ingest_activity

    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        db.commit()
    finally:
        db.close()
    lon, lat = _start()
    geo = _line(lon, lat, n=40, step=0.0005)
    first = ingest_activity(
        uid,
        {"provider": "strava", "provider_activity_id": f"mhf-p-{uuid.uuid4().hex[:8]}",
         "source": "strava_api", "sport": "mtb", "geometry_geojson": geo,
         "distance_m": 5000, "activity_date": _DATE},
        contribute_heatmap=False, skip_heat_computation=True,
    )
    assert first["status"] == "created"
    mhf_env.clear()
    rev_before = _rev(uid)
    second = ingest_activity(
        uid,
        {"provider": "file", "provider_activity_id": None, "source": "manual_upload",
         "sport": "mtb", "geometry_geojson": _line(lon, lat, n=41, step=0.0005),
         "distance_m": 5020, "file_hash": f"mhf-{uuid.uuid4().hex}",
         "activity_date": _DATE + timedelta(seconds=30)},
        contribute_heatmap=False, skip_heat_computation=True,
    )
    assert second["status"] == "promoted", second
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) > rev_before


def test_delete_api_schedules(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    aid = _upload(client, auth_headers, "mhf-del", *_start(), contribute=False)
    mhf_env.clear()
    rev_before = _rev(uid)
    assert client.delete(f"/me/activities/{aid}", headers=auth_headers).status_code == 204
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) > rev_before


def test_sport_change_single_and_bulk_schedule(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    a1 = _upload(client, auth_headers, "mhf-s1", *_start(), contribute=False)
    a2 = _upload(client, auth_headers, "mhf-s2", *_start(), contribute=False)

    mhf_env.clear()
    r0 = _rev(uid)
    assert client.patch(f"/me/activities/{a1}", headers=auth_headers, json={"sport": "mtb"}).status_code == 200
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) > r0

    mhf_env.clear()
    r1 = _rev(uid)
    resp = client.patch(
        "/me/activities/sport", headers=auth_headers, json={"activity_ids": [a1, a2], "sport": "road"}
    )
    assert resp.status_code == 200
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) > r1


def test_strava_geometry_upgrade_schedules(mhf_env, users):
    from app.api.integrations_strava import _gps_apply_batch

    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        aid = _mk_activity(db, uid, provider="strava", source="strava_api")
        db.commit()
    finally:
        db.close()
    mhf_env.clear()
    r0 = _rev(uid)
    _gps_apply_batch(str(uuid.uuid4()), [(aid, _line(*_start(), n=12))], 0)
    assert _scheduled(mhf_env, uid)
    assert _rev(uid) > r0


def test_strava_webhook_delete_schedules(mhf_env, users):
    from app.api.internal_strava_webhook import _delete_activity_for_strava
    from app.db.models import IntegrationAccount

    athlete = uuid.uuid4().hex[:10]
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid, provider="strava", provider_activity_id="777")
        db.add(IntegrationAccount(
            id=str(uuid.uuid4()), user_id=uid, provider="strava", access_token="x",
            refresh_token="y", expires_at=9_999_999_999, external_user_id=athlete, athlete_name="A"))
        db.commit()
    finally:
        db.close()
    mhf_env.clear()
    try:
        assert _delete_activity_for_strava(athlete, "777")["status"] == "deleted"
        assert _scheduled(mhf_env, uid)
    finally:
        db = SessionLocal()
        db.execute(sa_text("DELETE FROM integration_accounts WHERE user_id = :u"), {"u": uid})
        db.commit()
        db.close()


def test_account_merge_rebuilds_survivor_and_deletes_absorbed(mhf_env, users):
    from app.services.account_merge import merge_synthetic_account

    athlete = uuid.uuid4().hex[:10]
    db = SessionLocal()
    try:
        survivor = _mk_user(db, email=f"real_{athlete}@example.com")
        synth = _mk_user(db, email=f"strava_{athlete}@strava.local", password=None)
        users += [survivor, synth]
        _mk_activity(db, synth, provider="gpx", source="manual_upload")
        db.commit()
    finally:
        db.close()
    # The absorbed account had a built file.
    assert mhf.build_for_user(synth)["status"] == "published"
    store = mhf.get_store()
    assert store.list_keys(f"users/{synth}/")
    mhf_env.clear()

    db = SessionLocal()
    try:
        res = merge_synthetic_account(db, survivor, synth, commit=True)
    finally:
        db.close()
    assert res.merged
    assert _scheduled(mhf_env, survivor)
    assert not _scheduled(mhf_env, synth)
    assert store.list_keys(f"users/{synth}/") == []
    db = SessionLocal()
    try:
        assert db.execute(
            sa_text("SELECT count(*) FROM my_heatmap_files WHERE user_id = :u"), {"u": synth}
        ).scalar() == 0
    finally:
        db.close()


def test_gdpr_delete_and_orphan_sweep(mhf_env, users):
    db = SessionLocal()
    try:
        u1, u2 = _mk_user(db), _mk_user(db)
        users += [u1, u2]
        _mk_activity(db, u1)
        _mk_activity(db, u2)
        db.commit()
    finally:
        db.close()
    assert mhf.build_for_user(u1)["status"] == "published"
    assert mhf.build_for_user(u2)["status"] == "published"
    store = mhf.get_store()

    # GDPR runbook step: objects AND row gone.
    assert mhf.delete_for_user(u1) >= 2
    assert store.list_keys(f"users/{u1}/") == []
    db = SessionLocal()
    try:
        assert db.execute(
            sa_text("SELECT count(*) FROM my_heatmap_files WHERE user_id = :u"), {"u": u1}
        ).scalar() == 0
        # Step forgotten for u2: the user row disappears, the sweep catches it.
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": u2})
        db.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": u2})
        db.commit()
    finally:
        db.close()
    assert u2 in mhf.sweep_orphans()
    assert store.list_keys(f"users/{u2}/") == []


# ── 3. build, manifest, proxy ────────────────────────────────────────────────


def _range(client, headers, url, start, end):
    return client.get(url, headers={**headers, "Range": f"bytes={start}-{end}"})


def test_build_manifest_proxy_roundtrip(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    assert client.get("/me/heatmap/manifest", headers=auth_headers).json()["status"] == "missing"
    assert _scheduled(mhf_env, uid), "manifest self-heals a missing file"

    ids = [_upload(client, auth_headers, f"mhf-b{i}", *_start(), contribute=False) for i in range(3)]
    res = mhf.build_for_user(uid)
    assert res["status"] == "published" and res["activity_count"] == 3

    man = client.get("/me/heatmap/manifest", headers=auth_headers)
    assert man.headers["cache-control"] == "private, no-store"
    m = man.json()
    assert m["status"] == "ready" and m["activity_count"] == 3

    head = _range(client, auth_headers, m["tiles_url"], 0, 16383)
    assert head.status_code == 206
    assert head.content[:7] == b"PMTiles"
    assert head.headers["content-range"].startswith("bytes 0-")
    assert head.headers["cache-control"].startswith("private")

    summ = client.get(m["summary_url"], headers=auth_headers)
    assert summ.status_code == 200
    body = summ.json()
    assert body["total"] == 3
    assert {a["id"] for a in body["activities"]} == set(ids)
    a0 = body["activities"][0]
    assert len(a0["bbox"]) == 4 and len(a0["center"]) == 2 and len(a0["start"]) == 2
    assert "coordinates" not in json.dumps(body)

    # A change → stale (JSON fallback) → rebuild → new version, previous kept.
    v1 = m["version"]
    client.patch(f"/me/activities/{ids[0]}", headers=auth_headers, json={"sport": "mtb"})
    assert client.get("/me/heatmap/manifest", headers=auth_headers).json()["status"] == "stale"
    assert mhf.build_for_user(uid)["status"] == "published"
    m2 = client.get("/me/heatmap/manifest", headers=auth_headers).json()
    assert m2["status"] == "ready" and m2["version"] != v1
    assert _range(client, auth_headers, f"/me/heatmap/{v1}.pmtiles", 0, 99).status_code == 206
    # A rebuild with no change is a no-op.
    assert mhf.build_for_user(uid)["status"] == "fresh"
    # Third build → v1 is cleaned up.
    client.patch(f"/me/activities/{ids[1]}", headers=auth_headers, json={"sport": "mtb"})
    assert mhf.build_for_user(uid)["status"] == "published"
    assert _range(client, auth_headers, f"/me/heatmap/{v1}.pmtiles", 0, 99).status_code == 404
    assert not any(v1 in k for k in mhf.get_store().list_keys(f"users/{uid}/"))


def test_build_for_user_without_activities(mhf_env, users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        db.commit()
    finally:
        db.close()
    res = mhf.build_for_user(uid)
    assert res["status"] == "published" and res["activity_count"] == 0
    db = SessionLocal()
    try:
        m = mhf.manifest(db, uid)
    finally:
        db.close()
    assert m["status"] == "ready" and m["tiles_url"] is None


def test_concurrent_older_build_cannot_overwrite_newer(mhf_env, users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.commit()
    finally:
        db.close()
    assert mhf.build_for_user(uid)["status"] == "published"
    db = SessionLocal()
    try:
        built = db.execute(
            sa_text("SELECT built_rev, version FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
        ).first()
    finally:
        db.close()
    # An older snapshot losing the race must not publish.
    published, _, _ = mhf._publish_cas(uid, built[0] - 1, "r0-0000000000000000", 0, 0, 0)
    assert not published
    published, _, _ = mhf._publish_cas(uid, built[0], "r0-0000000000000001", 0, 0, 0)
    assert not published


def test_internal_rebuild_endpoint(mhf_env, client, users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.commit()
    finally:
        db.close()
    resp = client.post("/internal/my-heatmap/rebuild", json={"user_id": uid})
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert client.post("/internal/my-heatmap/rebuild", json={}).status_code == 400


# ── 4. privacy ───────────────────────────────────────────────────────────────


def test_proxy_requires_auth_and_ownership(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _register_second_user, _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    _upload(client, auth_headers, "mhf-priv", *_start(), contribute=False)
    mhf.build_for_user(uid)
    m = client.get("/me/heatmap/manifest", headers=auth_headers).json()

    assert client.get("/me/heatmap/manifest").status_code == 401
    assert client.get(m["tiles_url"], headers={"Range": "bytes=0-99"}).status_code == 401
    assert client.get(m["summary_url"]).status_code == 401

    other = _register_second_user(client)
    users.append(_user_id_of(client, other))
    assert _range(client, other, m["tiles_url"], 0, 99).status_code == 404
    assert client.get(m["summary_url"], headers=other).status_code == 404

    # Malformed version (path traversal attempt) and Range abuse.
    assert _range(client, auth_headers, "/me/heatmap/..%2F..%2Fx.pmtiles", 0, 9).status_code == 404
    assert client.get(m["tiles_url"], headers=auth_headers).status_code == 416
    assert _range(client, auth_headers, m["tiles_url"], 0, 64 * 1024 * 1024).status_code == 416


def test_flag_off_hides_everything(mhf_env, client, auth_headers, monkeypatch, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    _upload(client, auth_headers, "mhf-off", *_start(), contribute=False)
    mhf.build_for_user(uid)
    m = client.get("/me/heatmap/manifest", headers=auth_headers).json()
    monkeypatch.setenv("MY_HEATMAP_FILES_ENABLED", "false")
    assert client.get("/me/heatmap/manifest", headers=auth_headers).json() == {"status": "disabled"}
    assert _range(client, auth_headers, m["tiles_url"], 0, 99).status_code == 404


def test_allow_list(monkeypatch, mhf_env):
    monkeypatch.setenv("MY_HEATMAP_FILES_USERS", "a,b")
    assert mhf.is_enabled_for("a")
    assert not mhf.is_enabled_for("c")


@pytest.mark.parametrize(
    "bucket",
    ["common-trails-heatmap-prod", "common-trails-heatmap-staging", "same-as-public"],
)
def test_refuses_public_heatmap_bucket(monkeypatch, bucket):
    monkeypatch.setenv("HEATMAP_GCS_BUCKET", "same-as-public")
    monkeypatch.setenv("MY_HEATMAP_BUCKET", bucket)
    with pytest.raises(mhf.BucketConfigError):
        mhf.get_store()


def test_gcs_upload_is_private_and_immutable(monkeypatch):
    monkeypatch.setenv("MY_HEATMAP_BUCKET", "common-trails-my-heatmap-prod")
    monkeypatch.setenv("HEATMAP_GCS_BUCKET", "common-trails-heatmap-prod")
    store = mhf.get_store()
    blob = MagicMock()
    bucket = MagicMock()
    bucket.blob.return_value = blob
    store._bucket = bucket
    store.upload("users/u/r1-x.pmtiles", "/dev/null", "application/vnd.pmtiles")
    assert blob.cache_control == "private, no-store"
    kwargs = blob.upload_from_filename.call_args.kwargs
    assert kwargs["if_generation_match"] == 0
    assert "predefined_acl" not in kwargs
    blob.make_public.assert_not_called()
    names = [c[0] for c in blob.method_calls]
    assert not any("acl" in n or "public" in n for n in names)


def _deploy_script() -> str:
    for parent in Path(__file__).resolve().parents:
        p = parent / "scripts" / "deploy-prod.sh"
        if p.is_file():
            return p.read_text()
    pytest.skip("scripts/deploy-prod.sh not mounted")


def test_deploy_asserts_private_bucket():
    s = _deploy_script()
    assert 'MY_HEATMAP_BUCKET_NAME="common-trails-my-heatmap-${ENV}"' in s
    # Distinct from the public heatmap bucket behind tiles.chemins-communs.fr.
    assert 'HEATMAP_GCS_BUCKET="common-trails-heatmap-${ENV}"' in s
    assert "--public-access-prevention" in s
    assert "public_access_prevention" in s and "enforced" in s
    assert "--uniform-bucket-level-access" in s
    # Never granted to everyone; the ONLY member bound is the runtime SA, and a
    # public member found on the bucket aborts the deploy.
    block = s[s.index("# ── 0b. Assert the PRIVATE") :]
    block = block[: block.index("# ── Deploy one Cloud Run service")]
    assert '--member="serviceAccount:${API_SA}" --role="roles/storage.objectAdmin"' in block
    assert block.count("--member=") == 1
    assert "(allUsers|allAuthenticatedUsers)" in block and "ABORT" in block
    # Wiring: flag OFF by default, bucket + handler on the services.
    assert 'MY_HEATMAP_FILES_ENABLED: "${MY_HEATMAP_FILES_ENABLED:-false}"' in s
    assert 'MY_HEATMAP_BUCKET: "${MY_HEATMAP_BUCKET_NAME}"' in s
    assert 'INTERNAL_MY_HEATMAP_HANDLER_URL: "${WORKER_RUN_URL}/internal/my-heatmap/rebuild"' in s


def test_summary_is_gzip_and_geometry_free(mhf_env, users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid, name="Maison → boulot")
        db.commit()
    finally:
        db.close()
    res = mhf.build_for_user(uid)
    raw = mhf.get_store().read(mhf.summary_key(uid, res["version"]))
    data = json.loads(gzip.decompress(raw))
    assert data["activities"][0]["name"] == "Maison → boulot"
    assert set(data["activities"][0]) >= {"id", "sport", "bbox", "center", "start", "undated"}


def _run_deploy_with_stub(tmp_path, flag: str):
    import os
    import stat
    import subprocess

    script = None
    for parent in Path(__file__).resolve().parents:
        p = parent / "scripts" / "deploy-prod.sh"
        if p.is_file():
            script = p
            break
    if script is None:
        pytest.skip("scripts/deploy-prod.sh not mounted")
    log = tmp_path / "calls.log"
    stub = tmp_path / "gcloud"
    # Records every call; describe → '' (public_access_prevention NOT enforced).
    stub.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{log}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "DEPLOY_YES": "1",
        "STRAVA_WEBHOOK_SUBSCRIPTION_ID": "349136",
        "MY_HEATMAP_FILES_ENABLED": flag,
    }
    proc = subprocess.run(["bash", str(script), "test-tag"], env=env, capture_output=True, text=True, timeout=60)
    calls = log.read_text() if log.exists() else ""
    return proc, calls


def test_deploy_flag_off_makes_no_bucket_or_iam_call(tmp_path):
    """Flag OFF (prod default): the deploy touches exactly the infra it did
    before this feature — no call on the private bucket at all."""
    proc, calls = _run_deploy_with_stub(tmp_path, "false")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "my-heatmap" not in calls.replace("/internal/my-heatmap/rebuild", "")
    assert "storage buckets create" not in calls
    assert "storage buckets add-iam-policy-binding" not in calls


def test_deploy_flag_on_aborts_on_unverified_bucket(tmp_path):
    """Flag ON + a bucket that cannot be proven private → the deploy ABORTS."""
    proc, calls = _run_deploy_with_stub(tmp_path, "true")
    assert proc.returncode != 0
    assert "public_access_prevention" in proc.stderr and "ABORT" in proc.stderr
    assert "storage buckets update gs://common-trails-my-heatmap-prod" in calls


# ── review round 1 regressions ───────────────────────────────────────────────


def test_force_rebuild_republishes_a_fresh_file(mhf_env, users):
    """`backfill --force` (tile-format change) must replace a FRESH file."""
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.commit()
    finally:
        db.close()
    v1 = mhf.build_for_user(uid)["version"]
    res = mhf.build_for_user(uid, force=True)
    assert res["status"] == "published" and res["version"] != v1


def test_savepoint_rollback_keeps_outer_schedule(mhf_env, users):
    """Ingest wraps each activity in begin_nested(): one failing savepoint must
    not drop the rebuild the outer transaction already asked for."""
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.flush()
        sp = db.begin_nested()
        _mk_activity(db, uid)
        sp.rollback()
        db.commit()
    finally:
        db.close()
    assert _scheduled(mhf_env, uid)


def test_build_racing_a_row_reset_never_publishes_ahead_of_data(mhf_env, users):
    """Row deleted + recreated (support delete) while a build of rev N is in
    flight: publishing built_rev=N > data_rev would make the file stale forever."""
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        for _ in range(3):
            _mk_activity(db, uid)
            db.commit()  # one INSERT statement each → data_rev 3
        rev = _rev(uid)
        assert rev >= 3
        db.execute(sa_text("DELETE FROM my_heatmap_files WHERE user_id = :u"), {"u": uid})
        db.execute(sa_text("INSERT INTO my_heatmap_files (user_id) VALUES (:u)"), {"u": uid})
        db.commit()
    finally:
        db.close()
    published, _, _ = mhf._publish_cas(uid, rev, "r9-0000000000000000", 0, 0, 0)
    assert not published
    assert mhf.build_for_user(uid)["status"] == "published"
    db = SessionLocal()
    try:
        assert mhf.read_state(db, uid).fresh
    finally:
        db.close()


def test_trigger_geometry_only_change_counts_only_without_cache(users):
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        cached = _mk_activity(db, uid, display_geojson=_line(*_start()))
        uncached = _mk_activity(db, uid)
        db.commit()
        r0 = _rev(uid)
        db.execute(sa_text("UPDATE activities SET geometry_geojson = :g WHERE id = :a"),
                   {"g": _line(*_start(), n=9), "a": cached})
        db.commit()
        assert _rev(uid) == r0, "geometry-only change with a display cache → not displayed"
        db.execute(sa_text("UPDATE activities SET geometry_geojson = :g WHERE id = :a"),
                   {"g": _line(*_start(), n=9), "a": uncached})
        db.commit()
        assert _rev(uid) == r0 + 1, "no cache → the file derives from geometry_geojson"
    finally:
        db.close()


def test_range_past_eof_is_416(mhf_env, client, auth_headers, users):
    from tests.test_me_activities_delete import _upload

    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    _upload(client, auth_headers, "mhf-eof", *_start(), contribute=False)
    mhf.build_for_user(uid)
    m = client.get("/me/heatmap/manifest", headers=auth_headers).json()
    assert _range(client, auth_headers, m["tiles_url"], 10**9, 10**9 + 10).status_code == 416


def test_activation_waits_for_inflight_writer(mhf_env, users):
    """A write whose trigger ran while disabled but that commits AFTER the
    flip must not be missing from a `ready` file: the flip waits for it."""
    import threading

    _set_triggers(False)
    db = SessionLocal()
    uid = _mk_user(db)
    users.append(uid)
    db.commit()
    _mk_activity(db, uid)  # in flight: trigger ran while disabled, not committed
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(mhf.build_for_user(uid)))
    t.start()
    t.join(timeout=2)
    assert t.is_alive(), "activation must wait for the in-flight writer"
    db.commit()
    db.close()
    t.join(timeout=60)
    assert result["status"] == "published" and result["activity_count"] == 1
    db = SessionLocal()
    try:
        assert mhf.read_state(db, uid).fresh
    finally:
        db.close()


def test_backfill_enqueue_schedules_on_worker_without_building(mhf_env, users):
    """Prod jobs have no bucket access (403): `backfill --enqueue` must only
    schedule each stale user's rebuild, never build/upload in-process."""
    from app.cli import my_heatmap_files as cli

    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid)
        db.commit()
    finally:
        db.close()
    mhf_env.clear()
    summary = cli.backfill(user_id=uid, enqueue=True)
    assert summary.get("recorded") == 1
    assert (uid, "backfill") in mhf_env
    assert mhf.get_store().list_keys(f"users/{uid}/") == []  # nothing built here
