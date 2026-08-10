"""Account unification — link-time SELF-MERGE + the one-off migration (#477).

Drives the REAL merge function ``account_merge.merge_synthetic_account`` (no
inline mirror) and the REAL Strava-connect callback. Proves the safety
properties Paul must trust before running the prod one-off:

  (a) a Strava athlete bound to a credential-less synthetic account is merged
      into the connecting real user — activities reassigned, synthetic deleted;
  (b) a REAL credentialed account is NEVER auto-absorbed (anti-theft holds);
  (c) cross-account duplicate rides are deduped, keeping the community copy;
  (d) per-activity GDPR deletion still works on a reassigned activity;
  (e) the merge is idempotent and the one-off CLI is dry-run-safe.

Uses the real Postgres test DB (activities, heat_edges, heat_edge_contributors).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity, IntegrationAccount, User
from app.db.session import SessionLocal
from app.services.account_merge import (
    AccountMergeRefused,
    is_synthetic_account,
    merge_synthetic_account,
)
from app.services.ingest import heat_user_id_hash

_STUB_STRAVA_ID = "strava_stub_user_42"
_STUB_SYNTH_EMAIL = f"strava_{_STUB_STRAVA_ID}@strava.local"


# ── helpers ────────────────────────────────────────────────────────────────

def _mk_user(db, *, email: str, password: str | None) -> str:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=email, username="u", hashed_password=password))
    db.flush()
    return uid


def _synthetic_email(athlete: str) -> str:
    return f"strava_{athlete}@strava.local"


def _mk_activity(
    db, *, user_id: str, source: str | None, provider: str = "strava",
    provider_activity_id: str | None = None, when: datetime | None = None,
    distance_m: float = 10000.0, geometry: str | None = None,
) -> str:
    aid = str(uuid.uuid4())
    db.add(Activity(
        id=aid, user_id=user_id, provider=provider,
        provider_activity_id=provider_activity_id, source=source, sport="road",
        name="ride", geometry_geojson=geometry, distance_m=distance_m,
        contribute_heatmap=True, activity_date=when or datetime.now(UTC),
    ))
    db.flush()
    return aid


def _mk_heat_edge(db, *, edge_key: str, sport: str = "road", way_id: int | None = None) -> None:
    db.execute(sa_text(
        "INSERT INTO heat_edges (edge_key, sport, user_count, pass_count, "
        "forward_count, backward_count, osm_way_id, geometry) VALUES "
        "(:k, :s, 0, 0, 0, 0, :w, ST_GeomFromText('LINESTRING(3.8 43.6, 3.81 43.61)', 4326)) "
        "ON CONFLICT (edge_key, sport) DO NOTHING"
    ), {"k": edge_key, "s": sport, "w": way_id})


def _add_contrib(db, *, edge_key: str, user_id: str, activity_id: str) -> None:
    db.execute(sa_text(
        "INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_id) "
        "VALUES (:k, :h, :a) ON CONFLICT DO NOTHING"
    ), {"k": edge_key, "h": heat_user_id_hash(user_id), "a": activity_id})


def _recount_edge(db, edge_key: str) -> None:
    db.execute(sa_text(
        "UPDATE heat_edges SET user_count = sub.uc, pass_count = sub.pc FROM "
        "(SELECT edge_key, COUNT(DISTINCT user_id_hash) uc, COUNT(*) pc "
        " FROM heat_edge_contributors WHERE edge_key = :k GROUP BY edge_key) sub "
        "WHERE heat_edges.edge_key = :k"
    ), {"k": edge_key})


def _edge_counts(db, edge_key: str) -> tuple[int, int]:
    row = db.execute(sa_text(
        "SELECT user_count, pass_count FROM heat_edges WHERE edge_key = :k"
    ), {"k": edge_key}).fetchone()
    return (row[0], row[1]) if row else (0, 0)


@pytest.fixture
def db():
    s = SessionLocal()
    created_users: list[str] = []
    created_edges: list[str] = []
    # expose collectors so tests can register for cleanup
    s._created_users = created_users  # type: ignore[attr-defined]
    s._created_edges = created_edges  # type: ignore[attr-defined]
    try:
        yield s
    finally:
        s.rollback()
        for ek in created_edges:
            s.execute(sa_text("DELETE FROM heat_edge_contributors WHERE edge_key = :k"), {"k": ek})
            s.execute(sa_text("DELETE FROM heat_edges WHERE edge_key = :k"), {"k": ek})
        for uid in created_users:
            s.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": uid})
            s.execute(sa_text("DELETE FROM integration_accounts WHERE user_id = :u"), {"u": uid})
            s.execute(sa_text("DELETE FROM contribution_consents WHERE user_id = :u"), {"u": uid})
            s.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": uid})
        s.commit()
        s.close()


# ── is_synthetic_account gate ────────────────────────────────────────────────

def test_is_synthetic_account_gate(db):
    synth = User(id="x", email=_STUB_SYNTH_EMAIL, username="u", hashed_password=None)
    real_pw = User(id="y", email="real@example.com", username="u", hashed_password="hash")
    real_nopw = User(id="z", email="real2@example.com", username="u", hashed_password=None)
    synth_pw = User(id="w", email=_STUB_SYNTH_EMAIL, username="u", hashed_password="hash")
    assert is_synthetic_account(synth) is True
    assert is_synthetic_account(real_pw) is False
    assert is_synthetic_account(real_nopw) is False  # real email, no @strava.local
    assert is_synthetic_account(synth_pw) is False   # has a credential
    assert is_synthetic_account(None) is False


# ── (a) merge reassigns + deletes synthetic ──────────────────────────────────

def test_merge_reassigns_and_deletes_synthetic(db):
    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    a1 = _mk_activity(db, user_id=synth, source="strava_api", provider_activity_id="s1")
    a2 = _mk_activity(db, user_id=synth, source="manual_upload", provider="gpx")
    db.add(IntegrationAccount(
        id=str(uuid.uuid4()), user_id=synth, provider="strava",
        access_token="x", refresh_token="y", expires_at=9_999_999_999,
        external_user_id=athlete, athlete_name="A"))
    db.commit()

    res = merge_synthetic_account(db, survivor, synth, commit=True)

    assert res.merged is True
    assert res.reassigned_activities == 2
    # activities now owned by survivor
    for aid in (a1, a2):
        assert db.execute(sa_text("SELECT user_id FROM activities WHERE id = :a"),
                          {"a": aid}).scalar() == survivor
    # integration account reassigned
    assert db.execute(sa_text(
        "SELECT user_id FROM integration_accounts WHERE external_user_id = :x"),
        {"x": athlete}).scalar() == survivor
    # synthetic user gone
    assert db.query(User).filter(User.id == synth).first() is None
    assert db.query(User).filter(User.id == survivor).first() is not None


def test_committed_merge_triggers_community_map_rebuild(db):
    """A committed merge reassigns/drops activities → it must fire the
    build-pmtiles rebuild so the change surfaces on the static map (no
    periodic scheduler). Patches the trigger at its source module (merge
    imports it at call time). FAILS on code that never triggers a rebuild.
    """
    from unittest.mock import MagicMock, patch

    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    _mk_activity(db, user_id=synth, source="manual_upload", provider="gpx")
    db.commit()

    fake = MagicMock(return_value=True)
    with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
        res = merge_synthetic_account(db, survivor, synth, commit=True)

    assert res.merged is True
    fake.assert_called_once()


# ── (b) real credentialed account is NEVER absorbed ──────────────────────────

def test_refuses_real_credentialed_account(db):
    survivor = _mk_user(db, email=f"a_{uuid.uuid4().hex[:8]}@example.com", password="hash")
    victim = _mk_user(db, email=f"victim_{uuid.uuid4().hex[:8]}@example.com", password="realpw")
    db._created_users += [survivor, victim]
    vact = _mk_activity(db, user_id=victim, source="manual_upload", provider="gpx")
    db.commit()

    with pytest.raises(AccountMergeRefused):
        merge_synthetic_account(db, survivor, victim, commit=True)
    db.rollback()

    # Victim intact — account + its activity untouched (no theft).
    assert db.query(User).filter(User.id == victim).first() is not None
    assert db.execute(sa_text("SELECT user_id FROM activities WHERE id = :a"),
                      {"a": vact}).scalar() == victim


def test_refuses_synthetic_looking_email_with_password(db):
    """An @strava.local email that somehow has a password is still off-limits."""
    survivor = _mk_user(db, email=f"a_{uuid.uuid4().hex[:8]}@example.com", password="hash")
    athlete = uuid.uuid4().hex[:10]
    other = _mk_user(db, email=_synthetic_email(athlete), password="somehash")
    db._created_users += [survivor, other]
    db.commit()
    with pytest.raises(AccountMergeRefused):
        merge_synthetic_account(db, survivor, other, commit=True)
    db.rollback()
    assert db.query(User).filter(User.id == other).first() is not None


# ── (c) dedup keeps the community copy ───────────────────────────────────────

def test_dedup_prefers_community_copy(db):
    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    when = datetime(2024, 8, 15, 10, 30, tzinfo=UTC)
    # Survivor already has the Strava-API (personal) copy of this ride.
    surv_api = _mk_activity(db, user_id=survivor, source="strava_api",
                            provider="strava", provider_activity_id="ride-x",
                            when=when, distance_m=42000.0)
    # Synthetic holds the manual_upload (community-eligible) copy of the same ride.
    synth_manual = _mk_activity(db, user_id=synth, source="manual_upload",
                                provider="gpx", when=when + timedelta(minutes=2),
                                distance_m=42500.0)  # within ±5 min / ±5 %
    db.commit()

    res = merge_synthetic_account(db, survivor, synth, commit=True)

    assert res.dropped_duplicate_activities == 1
    # The community-eligible copy survived (under survivor); the strava_api twin dropped.
    assert db.execute(sa_text("SELECT user_id FROM activities WHERE id = :a"),
                      {"a": synth_manual}).scalar() == survivor
    assert db.execute(sa_text("SELECT 1 FROM activities WHERE id = :a"),
                      {"a": surv_api}).scalar() is None
    # Exactly one copy remains for survivor in that window.
    n = db.execute(sa_text(
        "SELECT COUNT(*) FROM activities WHERE user_id = :u"), {"u": survivor}).scalar()
    assert n == 1


def test_dedup_exact_provider_activity_id(db):
    """Same (provider, provider_activity_id) under both accounts collapses to one."""
    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    _mk_activity(db, user_id=survivor, source="strava_api", provider="strava",
                 provider_activity_id="dup1", distance_m=5000.0)
    _mk_activity(db, user_id=synth, source="strava_api", provider="strava",
                 provider_activity_id="dup1", distance_m=5000.0)
    db.commit()
    res = merge_synthetic_account(db, survivor, synth, commit=True)
    assert res.dropped_duplicate_activities == 1
    n = db.execute(sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :u"),
                   {"u": survivor}).scalar()
    assert n == 1  # no UNIQUE(user_id, provider, provider_activity_id) violation


# ── (d) GDPR per-activity deletion still works post-merge ─────────────────────

def test_gdpr_delete_after_merge(db):
    from app.services.activity_deletion import delete_user_activity

    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    edge = f"test-merge-gdpr-{athlete}"
    db._created_edges.append(edge)
    aid = _mk_activity(db, user_id=synth, source="manual_upload", provider="gpx")
    _mk_heat_edge(db, edge_key=edge, way_id=None)
    _add_contrib(db, edge_key=edge, user_id=synth, activity_id=aid)
    _recount_edge(db, edge)
    db.commit()

    merge_synthetic_account(db, survivor, synth, commit=True)
    # Activity now belongs to survivor.
    assert db.execute(sa_text("SELECT user_id FROM activities WHERE id = :a"),
                      {"a": aid}).scalar() == survivor

    # GDPR delete keyed on the SURVIVOR now succeeds and cleans heat.
    assert delete_user_activity(db, survivor, aid) is True
    assert db.execute(sa_text("SELECT 1 FROM activities WHERE id = :a"),
                      {"a": aid}).scalar() is None
    assert db.execute(sa_text(
        "SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_id = :a"),
        {"a": aid}).scalar() == 0
    # Edge with zero remaining contributors is gone.
    assert db.execute(sa_text("SELECT 1 FROM heat_edges WHERE edge_key = :k"),
                      {"k": edge}).scalar() is None


# ── K-anonymity: two former accounts collapse to ONE contributor hash ────────

def test_kanon_hash_collapses_on_merge(db):
    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    edge = f"test-merge-kanon-{athlete}"
    db._created_edges.append(edge)
    _mk_heat_edge(db, edge_key=edge)
    # Same edge contributed by BOTH accounts on DIFFERENT (non-duplicate) rides.
    surv_act = _mk_activity(db, user_id=survivor, source="manual_upload",
                            provider="gpx", when=datetime(2024, 1, 1, tzinfo=UTC),
                            distance_m=1000.0)
    synth_act = _mk_activity(db, user_id=synth, source="manual_upload",
                             provider="gpx", when=datetime(2024, 6, 1, tzinfo=UTC),
                             distance_m=2000.0)
    _add_contrib(db, edge_key=edge, user_id=survivor, activity_id=surv_act)
    _add_contrib(db, edge_key=edge, user_id=synth, activity_id=synth_act)
    _recount_edge(db, edge)
    db.commit()
    assert _edge_counts(db, edge) == (2, 2)  # two distinct hashes pre-merge

    merge_synthetic_account(db, survivor, synth, commit=True)

    uc, pc = _edge_counts(db, edge)
    assert uc == 1, "one physical person must count ONCE toward K-anonymity"
    assert pc == 2, "both passes preserved"
    # All contributor rows now carry the survivor's hash.
    hashes = {r[0] for r in db.execute(sa_text(
        "SELECT DISTINCT user_id_hash FROM heat_edge_contributors WHERE edge_key = :k"),
        {"k": edge})}
    assert hashes == {heat_user_id_hash(survivor)}


# ── (e) idempotent + noop guards ─────────────────────────────────────────────

def test_merge_is_idempotent(db):
    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    _mk_activity(db, user_id=synth, source="manual_upload", provider="gpx")
    db.commit()

    r1 = merge_synthetic_account(db, survivor, synth, commit=True)
    assert r1.merged is True
    # Re-running: synthetic row is gone → clean no-op, never raises.
    r2 = merge_synthetic_account(db, survivor, synth, commit=True)
    assert r2.merged is False
    assert r2.reason == "noop_absorbed_missing"


def test_merge_noop_same_user(db):
    u = _mk_user(db, email=f"real_{uuid.uuid4().hex[:8]}@example.com", password="hash")
    db._created_users.append(u)
    db.commit()
    res = merge_synthetic_account(db, u, u, commit=True)
    assert res.merged is False and res.reason == "noop_same_or_missing"


# ── one-off CLI: plan + dry-run safety ───────────────────────────────────────

def test_cli_plan_pairs_synthetic_to_colliding_real():
    from app.cli.merge_dup_accounts import _plan_from_report

    report = {
        "users": [
            {"user_id": "real1", "activities": 5, "synthetic_strava": False, "has_password": True},
            {"user_id": "synthA", "activities": 3, "synthetic_strava": True, "has_password": False},
            {"user_id": "synthB", "activities": 1, "synthetic_strava": True, "has_password": False},
        ],
        "synthetic_strava_accounts": [
            {"user_id": "synthA", "activities": 3},
            {"user_id": "synthB", "activities": 1},
        ],
        "temporal_collisions": [
            # synthA clearly collides with real1
            {"user_a": "real1", "user_b": "synthA", "colliding_activities": 3},
            # synthB collides with nobody real
        ],
    }
    planned, unresolved = _plan_from_report(report)
    assert planned == [{"absorbed": "synthA", "survivor": "real1",
                        "colliding": 3, "absorbed_activities": 3}]
    assert [u["absorbed"] for u in unresolved] == ["synthB"]


def test_cli_dry_run_writes_nothing(db, monkeypatch):
    """`run(apply=False)` must not mutate the DB even when a merge is planned."""
    import app.cli.merge_dup_accounts as cli

    athlete = uuid.uuid4().hex[:10]
    survivor = _mk_user(db, email=f"real_{athlete}@example.com", password="hash")
    synth = _mk_user(db, email=_synthetic_email(athlete), password=None)
    db._created_users += [survivor, synth]
    db.commit()

    # Stub the diagnostic to return a definite plan for our pair.
    def _fake_report(_db, tolerance_min=5):
        return {
            "users": [
                {"user_id": survivor, "activities": 0, "synthetic_strava": False, "has_password": True},
                {"user_id": synth, "activities": 0, "synthetic_strava": True, "has_password": False},
            ],
            "synthetic_strava_accounts": [{"user_id": synth, "activities": 0}],
            "temporal_collisions": [
                {"user_a": min(survivor, synth), "user_b": max(survivor, synth),
                 "colliding_activities": 4}
            ],
        }
    monkeypatch.setattr("app.cli.list_dup_accounts.collect_report", _fake_report)

    rc = cli.run(db, apply=False, tolerance_min=5, pairs=[])
    assert rc == 0
    # Synthetic account STILL present — dry-run wrote nothing.
    assert db.query(User).filter(User.id == synth).first() is not None


# ── END-TO-END: the Strava-connect callback self-merges the synthetic split ──

class TestConnectSelfMerge:
    """Drive the REAL /connect → /callback path (TEST_MODE stub athlete)."""

    @pytest.fixture(autouse=True)
    def _strava_enabled(self, monkeypatch):
        import app.api.integrations_strava as strava_mod
        monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
        monkeypatch.setattr(strava_mod, "TEST_MODE", True)
        monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")
        yield

    @pytest.fixture(autouse=True)
    def _wipe(self):
        def wipe():
            s = SessionLocal()
            try:
                s.execute(sa_text(
                    "DELETE FROM integration_accounts WHERE external_user_id = :x"),
                    {"x": _STUB_STRAVA_ID})
                s.execute(sa_text("DELETE FROM users WHERE email = :e"),
                          {"e": _STUB_SYNTH_EMAIL})
                s.commit()
            finally:
                s.close()
        wipe()
        yield
        wipe()

    def _connect(self, client, bearer):
        connect = client.get(
            "/integrations/strava/connect",
            headers={"Authorization": f"Bearer {bearer}"},
            follow_redirects=False,
        )
        assert connect.status_code in (302, 307), connect.text
        return client.get(connect.headers["location"], follow_redirects=False)

    def test_connect_merges_synthetic_and_reassigns(self, client):
        # A real email user, logged in.
        email = f"real_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post("/auth/register", json={
            "email": email, "password": "testpass123",
            "username": f"u_{uuid.uuid4().hex[:6]}"})
        assert reg.status_code == 201, reg.text
        real_id = reg.json()["user_id"]
        bearer = reg.json()["access_token"]

        # A pre-existing synthetic account owns the stub athlete + an activity.
        s = SessionLocal()
        try:
            synth_id = _mk_user(s, email=_STUB_SYNTH_EMAIL, password=None)
            s.add(IntegrationAccount(
                id=str(uuid.uuid4()), user_id=synth_id, provider="strava",
                access_token="x", refresh_token="y", expires_at=9_999_999_999,
                external_user_id=_STUB_STRAVA_ID, athlete_name="Old"))
            act_id = _mk_activity(s, user_id=synth_id, source="manual_upload",
                                  provider="gpx")
            s.commit()
        finally:
            s.close()

        resp = self._connect(client, bearer)
        assert resp.status_code in (302, 307), resp.text
        assert "status=connected" in resp.headers.get("location", "")

        s = SessionLocal()
        try:
            # Synthetic user absorbed.
            assert s.query(User).filter(User.id == synth_id).first() is None
            # Athlete + activity now belong to the real user.
            assert s.execute(sa_text(
                "SELECT user_id FROM integration_accounts WHERE external_user_id = :x"),
                {"x": _STUB_STRAVA_ID}).scalar() == real_id
            assert s.execute(sa_text("SELECT user_id FROM activities WHERE id = :a"),
                             {"a": act_id}).scalar() == real_id
        finally:
            # cleanup activity + real user
            s.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": real_id})
            s.execute(sa_text("DELETE FROM integration_accounts WHERE user_id = :u"), {"u": real_id})
            s.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": real_id})
            s.commit()
            s.close()

    def test_connect_does_not_steal_real_account(self, client):
        """The athlete bound to a REAL credentialed account is NOT merged —
        the connecting user gets a conflict, the victim keeps everything."""
        victim = SessionLocal()
        try:
            victim_id = _mk_user(victim, email=f"victim_{uuid.uuid4().hex[:8]}@example.com",
                                 password="realpw")
            victim.add(IntegrationAccount(
                id=str(uuid.uuid4()), user_id=victim_id, provider="strava",
                access_token="x", refresh_token="y", expires_at=9_999_999_999,
                external_user_id=_STUB_STRAVA_ID, athlete_name="Victim"))
            victim.commit()
        finally:
            victim.close()

        reg = client.post("/auth/register", json={
            "email": f"a_{uuid.uuid4().hex[:8]}@example.com", "password": "testpass123",
            "username": f"u_{uuid.uuid4().hex[:6]}"})
        real_id = reg.json()["user_id"]
        resp = self._connect(client, reg.json()["access_token"])

        assert "status=conflict" in resp.headers.get("location", "")
        s = SessionLocal()
        try:
            assert s.query(User).filter(User.id == victim_id).first() is not None
            assert s.execute(sa_text(
                "SELECT user_id FROM integration_accounts WHERE external_user_id = :x"),
                {"x": _STUB_STRAVA_ID}).scalar() == victim_id
        finally:
            s.execute(sa_text("DELETE FROM integration_accounts WHERE user_id IN (:v,:r)"),
                      {"v": victim_id, "r": real_id})
            s.execute(sa_text("DELETE FROM users WHERE id IN (:v,:r)"),
                      {"v": victim_id, "r": real_id})
            s.commit()
            s.close()
