"""Full import of a (synthetic) bulk export, end to end — the automatic
version of the manual "drop my real Strava export and watch" run.

That manual run found real bugs (Strava ``routes/`` ingested into the public
heatmap, FIT members failing on a mis-sized field, …). The repo is public, so
the export here is SYNTHETIC (``tests/synthetic_exports.py``) but reproduces
every trap we know of. The pipeline is the REAL one:

    POST /imports/strava-archive/init → PUT …/upload/{id} → POST …/complete
      (exactly what the browser dropzone does in local/dev mode)
    → ``app.jobs.ingest_pending_archives.main()`` (the job's entrypoint, heat
      computation ON, as in prod)

Only the outside world is faked: ``send_email`` (captured), the
build-pmtiles ``jobs:run`` trigger (captured). Two spies WRAP real functions
(``iter_zip_members``, ``_ingest_member_bytes``) to record per-member
outcomes; they never change a result.
"""

from __future__ import annotations

import hashlib
import io
import sys
import uuid
import zipfile

import pytest
from sqlalchemy import text as sa_text

from app.services import email as email_service
from tests.synthetic_exports import build_garmin_export, build_strava_export


@pytest.fixture
def pipeline(tmp_path, monkeypatch, client):
    """Isolated archive storage + captured emails / rebuild pings + per-member
    outcome spies. Returns a ``run(zip_bytes)`` helper that uploads like the
    browser, runs the drain job entrypoint, and returns what happened."""
    import app.config as config
    from app.db.session import SessionLocal
    from app.jobs import ingest_pending_archives as drain_mod
    from app.services import archive_intake

    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")
    # Always send the start email (the default skips archives < 3 min).
    monkeypatch.setattr(config, "ARCHIVE_START_EMAIL_MIN_SECONDS", 0.0)
    monkeypatch.delenv("DRAIN_PACE_SECONDS", raising=False)

    db = SessionLocal()
    try:
        # The drain claims EVERY 'uploaded' archive: start from an empty queue
        # (same convention as the other archive-drain tests).
        db.execute(sa_text("TRUNCATE pending_archive_files, pending_archives"))
        db.commit()
    finally:
        db.close()

    state: dict = {}

    def fake_send(to, subject, html):
        state["emails"].append((to, subject, html))
        return True

    def fake_ping():
        state["pings"] += 1
        return True

    real_iter = archive_intake.iter_zip_members
    real_ingest = drain_mod._ingest_member_bytes

    def spy_iter(zf, **kw):
        for name, raw, csv_sport in real_iter(zf, **kw):
            if csv_sport is None:
                # raw None = activities/ file the CSV does not list;
                # bytes = a CSV row whose Activity Type is out of scope.
                state["outcomes"][name] = ("skipped", "unlisted" if raw is None else "out_of_scope")
            yield name, raw, csv_sport

    def spy_ingest(**kw):
        name = kw["filename"]
        try:
            outcome, aid, err = real_ingest(**kw)
        except Exception:
            state["outcomes"][name] = ("failed", None)
            raise
        if outcome == "imported":
            state["outcomes"][name] = ("imported", None)
        elif err is None:
            state["outcomes"][name] = ("skipped", "duplicate")
        elif err in ("no_geometry", "sport_unknown"):
            state["outcomes"][name] = ("skipped", err)
        else:  # parser skip_reason (FIT/GPX sport out of scope)
            state["outcomes"][name] = ("skipped", "out_of_scope")
        return outcome, aid, err

    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(drain_mod, "trigger_build_pmtiles_job", fake_ping)
    monkeypatch.setattr(archive_intake, "iter_zip_members", spy_iter)
    monkeypatch.setattr(drain_mod, "_ingest_member_bytes", spy_ingest)

    email = f"export_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"exp_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user_id = resp.json()["user_id"]

    def run(content: bytes) -> dict:
        state.update(emails=[], pings=0, outcomes={})
        # 1-3: the browser's local/dev upload flow.
        init = client.post(
            "/imports/strava-archive/init",
            headers=headers,
            json={
                "consent": True,
                "consent_version": "v5",
                "consent_text": "J'accepte de partager mes traces sous licence ODbL.",
                "locale": "fr",
                "filename": "export.zip",
                "size": len(content),
            },
        )
        assert init.status_code == 201, init.text
        body = init.json()
        assert body["storage_backend"] == "local" and body["upload_mode"] == "put"
        put = client.put(body["upload_url"], headers=headers, content=content)
        assert put.status_code == 200 and put.json() == {"stored": len(content)}, put.text
        done = client.post(
            "/imports/strava-archive/complete",
            headers=headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        assert done.status_code == 202 and done.json()["status"] == "uploaded", done.text

        # 4: the Cloud Run job entrypoint, as deployed (heat computation on).
        monkeypatch.setattr(sys, "argv", ["ingest_pending_archives", "--pace", "0"])
        drain_mod.main()

        db = SessionLocal()
        try:
            row = db.execute(
                sa_text(
                    "SELECT status, members_total, imported, skipped, failed, last_error "
                    "FROM pending_archives WHERE id = :id"
                ),
                {"id": body["archive_id"]},
            ).mappings().one()
        finally:
            db.close()
        return {
            "row": dict(row),
            "emails": list(state["emails"]),
            "pings": state["pings"],
            "outcomes": dict(state["outcomes"]),
        }

    return {"run": run, "user_id": user_id, "email": email, "headers": headers, "client": client}


def _activities(user_id: str) -> list[dict]:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return [
            dict(r)
            for r in db.execute(
                sa_text(
                    "SELECT name, sport, file_hash, source, contribute_heatmap, "
                    "display_geojson IS NOT NULL AS has_display, geometry IS NOT NULL AS has_geom, "
                    "to_char(activity_date AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI') AS start "
                    "FROM activities WHERE user_id = :u"
                ),
                {"u": user_id},
            ).mappings()
        ]
    finally:
        db.close()


def _counts(row: dict) -> tuple:
    return (row["status"], row["members_total"], row["imported"], row["skipped"], row["failed"])


def _assert_imported(user_id: str, expected: dict) -> list[dict]:
    acts = _activities(user_id)
    assert len(acts) == expected["imported"], acts
    assert {a["start"]: a["sport"] for a in acts} == expected["sports_by_start"]
    for a in acts:
        assert a["has_display"], f"display_geojson not filled for {a['name']}"
        assert a["has_geom"], a
        assert a["contribute_heatmap"] is True
        assert a["source"] == "manual_upload"  # archive_intake.ARCHIVE_PROVENANCE
    return acts


def _expected_outcomes(expected: dict) -> dict:
    """Per-member outcome; an import's sport is asserted from the DB row."""
    return {n: (o, None if o == "imported" else d) for n, (o, d) in expected["members"].items()}


def _outcomes_by_reason(outcomes: dict) -> dict:
    reasons: dict[str, int] = {}
    for kind, reason in outcomes.values():
        if kind == "skipped":
            reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


def test_synthetic_strava_export_full_pipeline_and_idempotent_redrop(pipeline):
    content, expected = build_strava_export()
    assert content == build_strava_export()[0], "generator must be deterministic"

    first = pipeline["run"](content)

    # ── Counts (archive row = what the end email reports) ──
    assert first["row"]["last_error"] is None
    assert _counts(first["row"]) == (
        "done", expected["members_total"], expected["imported"], expected["skipped"], expected["failed"],
    )
    # ── Per-member outcome, incl. the skip reason ──
    assert first["outcomes"] == _expected_outcomes(expected)
    assert _outcomes_by_reason(first["outcomes"]) == expected["skipped_by_reason"]

    # ── Activities: sport of each, display geometry, no route ──
    acts = _assert_imported(pipeline["user_id"], expected)
    names = {a["name"] for a in acts}
    assert not names & set(expected["route_names"]), names
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        route_hashes = {hashlib.sha256(zf.read(n)).hexdigest() for n in expected["never_ingested"]}
    assert not route_hashes & {a["file_hash"] for a in acts}
    assert not any("routes/" in n for n in first["outcomes"])

    # ── "Ma heatmap" source: /me/activities serves every imported ride ──
    me = pipeline["client"].get("/me/activities?limit=5000", headers=pipeline["headers"])
    assert me.status_code == 200
    assert me.json()["total"] == expected["imported"]
    assert len(me.json()["features"]) == expected["imported"]

    # ── Emails: start (with estimate) then end, to the owner ──
    assert [e[0] for e in first["emails"]] == [pipeline["email"]] * 2
    (_, s_subject, s_html), (_, e_subject, e_html) = first["emails"]
    assert s_subject.startswith("Début de votre import, estimé à environ ")
    assert f"<strong>{expected['estimate_members']}</strong> fichiers" in s_html
    assert e_subject == "Votre archive est importée 🚴"
    assert f"<strong>{expected['imported']}</strong> importées" in e_html
    assert f"<strong>{expected['skipped']}</strong> ignorées (hors-scope / doublons)" in e_html
    assert f"<strong>{expected['failed']}</strong> en erreur" in e_html
    assert "sans sport identifiable" not in e_html  # routes never land in "pick a sport"
    # Every imported ride is dated (the untimed 1015.gpx via its activities.csv
    # date) → no "sans date" line, every one goes public.
    assert expected["imported_undated"] == 0
    assert "sans date" not in e_html
    assert "Ma heatmap" in e_html

    # ── Rebuild ping fired (once, before the email — ordering pinned elsewhere) ──
    assert first["pings"] == 1

    # ── Idempotence: the same export dropped again imports nothing ──
    second = pipeline["run"](content)
    n_skip_again = expected["skipped"] + expected["imported"]
    assert _counts(second["row"]) == (
        "done", expected["members_total"], 0, n_skip_again, expected["failed"],
    )
    assert len(_activities(pipeline["user_id"])) == expected["imported"]
    assert second["pings"] == 0  # nothing new → no rebuild
    _to, subject, html = second["emails"][-1]
    assert subject == "Votre archive a été traitée"
    assert "aucune nouvelle trace" in html
    assert "<strong>0</strong> importée" in html
    assert f"<strong>{n_skip_again}</strong> ignorées (hors-scope / doublons)" in html


def test_synthetic_garmin_export_full_pipeline(pipeline):
    content, expected = build_garmin_export()

    res = pipeline["run"](content)

    assert res["row"]["last_error"] is None
    assert _counts(res["row"]) == (
        "done", expected["members_total"], expected["imported"], expected["skipped"], expected["failed"],
    )
    assert res["outcomes"] == _expected_outcomes(expected)
    assert _outcomes_by_reason(res["outcomes"]) == expected["skipped_by_reason"]
    _assert_imported(pipeline["user_id"], expected)

    _to, subject, html = res["emails"][-1]
    assert subject == "Votre archive est importée 🚴"
    assert f"<strong>{expected['imported']}</strong> importées" in html
    # The FIT with no usable sport gets its own actionable line.
    assert f"<strong>{expected['skipped_no_sport']}</strong> sans sport identifiable" in html
    # The untimed itinerary: imported (Ma heatmap) but never public — said so.
    assert (f"<strong>{expected['imported_undated']}</strong> trace sans date "
            "(itinéraires planifiés ?) : visible dans Ma heatmap uniquement, "
            "pas sur la carte publique") in html
    me = pipeline["client"].get("/me/activities?limit=5000", headers=pipeline["headers"]).json()
    assert me["total"] == expected["imported"]
    from app.db.session import SessionLocal
    from app.services.provenance import community_eligible_conditions

    conds, params = community_eligible_conditions()
    db = SessionLocal()
    try:
        public = db.execute(
            sa_text(f"SELECT COUNT(*) FROM activities WHERE user_id = :u AND {' AND '.join(conds)}"),
            {**params, "u": pipeline["user_id"]},
        ).scalar()
    finally:
        db.close()
    assert public == expected["imported"] - expected["imported_undated"]
    assert "<strong>1</strong> ignorée (hors-scope / doublons)" in html
    assert res["pings"] == 1
