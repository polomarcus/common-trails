"""Strava export ``routes/`` folder + start-of-import email.

Bugs (end-to-end import of a real Strava bulk export):

1. ``routes/*.gpx`` (PLANNED routes: no ``<time>``, often ``<type>running``,
   no activities.csv row) were yielded by ``iter_zip_members`` like any .gpx
   and ingested as consented rides into the public heatmap.
2. The routes the drain could not sport-classify were counted as
   ``skipped_no_sport`` → the end email told the user to "pick a sport and
   re-import" files that are not activities at all.
3. (feature) no feedback between upload and the end email → a start email
   with an estimated duration, sent once per archive.
4. cleanup CLI for routes already ingested in prod.

Every test drives the real functions (iter_zip_members / the real drain /
the real CLI); only ``send_email`` is mocked.
"""

import io
import uuid
import zipfile
from unittest.mock import MagicMock, patch

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.services import email as email_service


def _gpx(name: str, lat0: float, lon0: float, *, trk_type: str | None = None, n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    if trk_type:
        trk.type = trk_type
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=lon0 + i * 0.001,
                elevation=100 + i,
            )
        )
    return gpx.to_xml()


_CSV = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
    "1,2024-01-01,Morning spin,Ride,activities/1.gpx\n"
    "2,2024-01-02,VTT,MountainBikeRide,activities/2.fit.gz\n"
)


def _strava_zip(prefix: str = "strava-export/") -> bytes:
    """A (re-zipped) Strava export: activities.csv, two listed activities, a
    planned route typed ``running`` (would be ingested on the old code), an
    untyped route (was counted as "no sport"), and an unlisted stray GPX."""
    import gzip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{prefix}activities.csv", _CSV)
        zf.writestr(f"{prefix}activities/1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr(f"{prefix}activities/2.fit.gz", gzip.compress(b"not-a-real-fit"))
        zf.writestr(f"{prefix}routes/x.gpx", _gpx("Planned", 44.10, 3.60, trk_type="running"))
        zf.writestr(f"{prefix}routes/y.gpx", _gpx("Planned 2", 44.20, 3.70))
        zf.writestr(f"{prefix}activities/unlisted.gpx", _gpx("Stray", 43.0, 3.0))
    return buf.getvalue()


def _members(zip_bytes: bytes) -> list[tuple]:
    from app.services import archive_intake

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return list(archive_intake.iter_zip_members(zf))


# ── 1. iter_zip_members: only activities listed in activities.csv ────────────


class TestStravaExportSelection:
    @pytest.mark.parametrize("prefix", ["strava-export/", ""])
    def test_only_csv_activities_are_yielded(self, prefix):
        members = _members(_strava_zip(prefix))
        names = [n for n, _raw, _s in members]
        # routes/ never yielded; the CSV-unlisted activities/ member is yielded
        # as an out-of-scope skip (None sport, bytes never read).
        assert names == [
            f"{prefix}activities/1.gpx",
            f"{prefix}activities/2.fit.gz",
            f"{prefix}activities/unlisted.gpx",
        ]
        by_name = {n.rsplit("/", 1)[-1]: (raw, s) for n, raw, s in members}
        assert by_name["1.gpx"][1] == "road"
        assert by_name["2.fit.gz"][1] == "mtb"
        assert by_name["unlisted.gpx"] == (None, None)

    def test_huge_csv_field_does_not_truncate_the_map(self):
        """A >131072-char description used to raise csv.Error mid-file."""
        csv = (
            "Activity ID,Activity Date,Activity Name,Activity Type,Activity Description,Filename\n"
            f'1,2024-01-01,A,Ride,"{"x" * 200_000}",activities/1.gpx\n'
            "2,2024-01-02,B,Ride,short,activities/2.gpx\n"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("activities.csv", csv)
            zf.writestr("activities/1.gpx", _gpx("A", 45.0, 4.0))
            zf.writestr("activities/2.gpx", _gpx("B", 45.1, 4.1))
        sports = {n: s for n, _raw, s in _members(buf.getvalue())}
        assert sports == {"activities/1.gpx": "road", "activities/2.gpx": "road"}

    def test_partial_csv_parse_does_not_drop_activities(self, monkeypatch):
        """CSV parse fails half-way → partial map → NO listing restriction
        (activities after the failure point still yielded), routes still out."""
        from app.services import archive_intake
        from app.services import gpx as gpx_service

        monkeypatch.setattr(
            gpx_service,
            "parse_strava_activities_csv_checked",
            lambda _b: ({"activities/1.gpx": "road"}, False),
        )
        names = [n for n, _raw, _s in _members(_strava_zip(""))]
        assert names == ["activities/1.gpx", "activities/2.fit.gz", "activities/unlisted.gpx"]
        sports = {n: s for n, _raw, s in _members(_strava_zip(""))}
        assert sports["activities/unlisted.gpx"] is archive_intake._NO_CSV

    def test_parse_zip_of_gpx_skips_strava_routes(self):
        """Direct API path (POST /imports/files) — same exclusion."""
        from app.services import gpx as gpx_service

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("activities.csv", _CSV)
            zf.writestr("activities/1.gpx", _gpx("Morning spin", 45.0, 4.0))
            zf.writestr("routes/x.gpx", _gpx("Planned", 44.0, 3.0, trk_type="running"))
        names = [r.get("source_file") for r in gpx_service.parse_zip_of_gpx(buf.getvalue())]
        assert names == ["activities/1.gpx"]

    def test_routes_excluded_even_when_csv_headers_unrecognised(self):
        """A CSV we can't parse → no Filename restriction, but routes/ of a
        Strava export are still never yielded."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("activities.csv", "foo,bar\n1,2\n")
            zf.writestr("activities/1.gpx", _gpx("a", 45.0, 4.0, trk_type="road_biking"))
            zf.writestr("routes/r.gpx", _gpx("r", 44.0, 3.0, trk_type="running"))
        names = [n for n, _raw, _s in _members(buf.getvalue())]
        assert names == ["activities/1.gpx"]

    def test_garmin_like_zip_without_csv_yields_everything(self):
        """No activities.csv → unchanged: every GPX/FIT (incl. a folder that
        happens to be called routes/, and nested-zip members) is yielded."""
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as zin:
            zin.writestr("a.fit", b"fit-bytes")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("DI_CONNECT/x/1.gpx", _gpx("a", 45.0, 4.0))
            zf.writestr("DI_CONNECT/x/2.fit", b"fit-bytes-2")
            zf.writestr("routes/3.gpx", _gpx("c", 44.0, 3.0))
            zf.writestr("UploadedFiles_0.zip", inner.getvalue())
        from app.services import archive_intake

        names = [n for n, _raw, s in _members(buf.getvalue())]
        assert names == [
            "DI_CONNECT/x/1.gpx",
            "DI_CONNECT/x/2.fit",
            "routes/3.gpx",
            "UploadedFiles_0.zip!a.fit",
        ]
        assert all(s is archive_intake._NO_CSV for _n, _r, s in _members(buf.getvalue()))

    def test_estimate_member_count_matches_the_iterator(self):
        from app.services import archive_intake

        with zipfile.ZipFile(io.BytesIO(_strava_zip())) as zf:
            assert archive_intake.estimate_member_count(zf) == 2


# ── Start-email copy / rounding (pure) ───────────────────────────────────────


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "environ 1 minute"),
        (59, "environ 1 minute"),
        (61, "environ 2 minutes"),
        (9 * 60, "environ 9 minutes"),
        (11 * 60, "environ 15 minutes"),
        (55 * 60, "environ 55 minutes"),
        (58 * 60, "environ 1 heure"),
        (70 * 60, "environ 1 h 30"),
        (100 * 60, "environ 2 heures"),
    ],
)
def test_format_duration_estimate_fr(seconds, expected):
    assert email_service.format_duration_estimate(seconds, "fr") == expected


def test_format_duration_estimate_en():
    assert email_service.format_duration_estimate(30, "en") == "about 1 minute"
    assert email_service.format_duration_estimate(70 * 60, "en") == "about 1 hour 30 minutes"


def test_started_email_copy():
    subject, html = email_service.render_archive_started_email(
        members=1500,
        estimated_seconds=660,
        locale="fr",
    )
    assert subject == "Début de ton import, estimé à environ 15 minutes"
    assert "<strong>1500</strong> fichiers" in html
    assert "second email" in html
    assert "0 minute" not in html


# ── DB-backed: the real drain + the real CLI ─────────────────────────────────


@pytest.fixture
def _archive_env(tmp_path, monkeypatch):
    from app.db.session import SessionLocal
    from app.services import archive_intake

    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")
    db = SessionLocal()
    try:
        db.execute(sa_text("TRUNCATE pending_archive_files, pending_archives, contribution_consents"))
        db.commit()
    finally:
        db.close()
    yield


def _mk_user() -> tuple[str, str]:
    from app.db.models import User
    from app.db.session import SessionLocal

    email = f"rider_{uuid.uuid4().hex[:8]}@example.com"
    db = SessionLocal()
    try:
        u = User(email=email, username=f"routes_{uuid.uuid4().hex[:6]}")
        db.add(u)
        db.commit()
        return u.id, email
    finally:
        db.close()


def _mk_archive(user_id: str, content: bytes) -> str:
    from app.db.models import PendingArchive
    from app.db.session import SessionLocal
    from app.services import archive_intake

    bucket_key = f"archive-intake/{user_id}/{uuid.uuid4()}.zip"
    archive_intake.store_archive_local(bucket_key, content)
    db = SessionLocal()
    try:
        row = PendingArchive(
            user_id=user_id,
            storage_backend="local",
            bucket_key=bucket_key,
            fallback_sport="unknown",
            contribute_heatmap=True,
            status="uploaded",
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _drain():
    from app.jobs.ingest_pending_archives import drain_pending_archives

    return drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)


def _activities(user_id: str) -> list[dict]:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return [
            dict(r)
            for r in db.execute(
                sa_text("SELECT id, name, file_hash FROM activities WHERE user_id = :u"), {"u": user_id}
            ).mappings()
        ]
    finally:
        db.close()


@pytest.mark.usefixtures("_archive_env")
def test_drain_ingests_no_route_and_email_has_no_bogus_no_sport_line():
    uid, _email = _mk_user()
    _mk_archive(uid, _strava_zip())
    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        summary = _drain()

    names = sorted(a["name"] for a in _activities(uid))
    assert names == ["Morning spin"], names  # never "Planned" / "Stray"
    # 1.gpx imported; 2.fit.gz is garbage → failed. The CSV-unlisted member
    # counts in skipped (honest bucket). Routes: nowhere.
    assert summary["imported"] == 1, summary
    assert summary["skipped"] == 1, summary
    send.assert_called_once()  # small archive → end email only
    _to, _subject, html = send.call_args.args
    assert "<strong>1</strong> ignorée (hors-scope / doublons)" in html
    assert "sans sport identifiable" not in html
    assert "réimporte" not in html


@pytest.mark.usefixtures("_archive_env")
def test_start_email_sent_once_with_estimate_and_not_on_rerun(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "ARCHIVE_START_EMAIL_MIN_SECONDS", 0.0)
    monkeypatch.setattr(config, "ARCHIVE_ESTIMATE_SECONDS_PER_MEMBER", 0.4)
    monkeypatch.setattr(config, "ARCHIVE_ESTIMATE_OVERHEAD_SECONDS", 60.0)
    from app.jobs.ingest_pending_archives import estimate_archive_seconds

    assert estimate_archive_seconds(1500) == pytest.approx(660.0)

    uid, email = _mk_user()
    rid = _mk_archive(uid, _strava_zip())
    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        _drain()
    subjects = [c.args[1] for c in send.call_args_list]
    assert subjects[0] == "Début de ton import, estimé à environ 2 minutes"  # 60 + 2×0.4 s
    assert send.call_args_list[0].args[0] == email
    assert "<strong>2</strong> fichiers" in send.call_args_list[0].args[2]
    assert len(subjects) == 2 and subjects[1].startswith("Ton archive")

    # Re-run of the SAME archive (admin requeue / killed drain re-claimed):
    # attempts > 0 → no second start email, only the terminal one.
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa_text("UPDATE pending_archives SET status='uploaded' WHERE id=:id"), {"id": rid})
        db.commit()
    finally:
        db.close()
    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send2:
        _drain()
    subjects2 = [c.args[1] for c in send2.call_args_list]
    assert not any(s.startswith("Début") for s in subjects2), subjects2
    assert len(subjects2) == 1


@pytest.mark.usefixtures("_archive_env")
def test_start_email_skipped_for_small_archive_by_default():
    uid, _email = _mk_user()
    _mk_archive(uid, _strava_zip())
    with patch.object(email_service, "send_email", MagicMock(return_value=True)) as send:
        _drain()
    assert not any(c.args[1].startswith("Début") for c in send.call_args_list)


# ── 4. cleanup CLI — the negative cases are the point ───────────────────────


def _ingest_raw(uid: str, name: str, raw: bytes, *, fallback: str = "unknown"):
    """Pre-fix behaviour: a member ingested through the REAL member ingest."""
    from app.jobs.ingest_pending_archives import _ingest_member_bytes

    outcome, aid, _err = _ingest_member_bytes(
        user_id=uid,
        filename=name,
        raw=raw,
        resolved_sport=None,
        fallback_sport=fallback,
        contribute_heatmap=True,
        source="manual_upload",
    )
    assert outcome == "imported" and aid, (outcome, _err)
    return str(aid)


def _read(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(name)


def _drained_archive(uid: str, content: bytes) -> None:
    _mk_archive(uid, content)
    with patch.object(email_service, "send_email", MagicMock(return_value=True)):
        _drain()


def _dated_gpx(name: str, lat0: float, lon0: float) -> str:
    import datetime as dt

    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    trk.type = "road_biking"
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    t0 = dt.datetime(2024, 5, 1, 8, 0, tzinfo=dt.UTC)
    for i in range(8):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=lon0 + i * 0.001,
                time=t0 + dt.timedelta(seconds=10 * i),
            )
        )
    return gpx.to_xml()


@pytest.mark.usefixtures("_archive_env")
def test_cli_dry_run_out_then_apply_ids_file_deletes_only_routes_with_one_ping(tmp_path):
    from app.cli import purge_strava_route_activities as cli

    uid, _email = _mk_user()
    content = _strava_zip()
    _drained_archive(uid, content)  # real ride imported (fixed code)
    r1 = _ingest_raw(uid, "strava-export/routes/x.gpx", _read(content, "strava-export/routes/x.gpx"))
    r2 = _ingest_raw(
        uid, "strava-export/routes/y.gpx", _read(content, "strava-export/routes/y.gpx"), fallback="road"
    )
    assert len(_activities(uid)) == 3

    out = tmp_path / "ids.json"
    dry = cli.dry_run(user_id=uid, out=str(out))
    assert dry["candidates"] == 2 and dry["by_method"] == {"archive-hash": 2}
    assert len(_activities(uid)) == 3  # dry-run deletes nothing

    with patch("app.services.run_jobs.trigger_build_pmtiles_job", MagicMock(return_value=True)) as ping:
        applied = cli.apply(ids_file=str(out))
    assert applied["deleted"] == 2 and applied["skipped"] == []
    assert ping.call_count == 1  # ONE rebuild for N deletions
    remaining = {a["id"] for a in _activities(uid)}
    assert r1 not in remaining and r2 not in remaining
    assert [a["name"] for a in _activities(uid)] == ["Morning spin"]


@pytest.mark.usefixtures("_archive_env")
def test_cli_duplicate_export_identical_hashes_keeps_the_ride():
    """A routes/ member byte-identical to a real ride (+ a duplicated export
    folder): the hash is also an activity's → subtracted → never listed.
    The ride is undated, so only the subtraction protects it."""
    from app.cli import purge_strava_route_activities as cli

    ride = _gpx("Morning spin", 45.75, 4.83)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for top in ("strava-export/", "strava-export-copy/"):
            zf.writestr(f"{top}activities.csv", _CSV)
            zf.writestr(f"{top}activities/1.gpx", ride)
            zf.writestr(f"{top}routes/dup.gpx", ride)
    uid, _email = _mk_user()
    _drained_archive(uid, buf.getvalue())
    assert [a["name"] for a in _activities(uid)] == ["Morning spin"]

    assert cli.dry_run(user_id=uid)["candidates"] == 0


@pytest.mark.usefixtures("_archive_env")
def test_cli_ignores_csv_unlisted_timestamped_gpx():
    from app.cli import purge_strava_route_activities as cli

    extra = _dated_gpx("Added by hand", 43.5, 3.5)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", _CSV)
        zf.writestr("activities/1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("activities/extra.gpx", extra)
    uid, _email = _mk_user()
    _drained_archive(uid, buf.getvalue())
    _ingest_raw(uid, "activities/extra.gpx", extra.encode())  # pre-fix ingest
    assert len(_activities(uid)) == 2

    assert cli.dry_run(user_id=uid)["candidates"] == 0


@pytest.mark.usefixtures("_archive_env")
def test_cli_ignores_routes_folder_of_non_strava_zip():
    """No activities.csv → a "routes" folder is just a folder: its (undated)
    rides are legit and never listed, by either method."""
    from app.cli import purge_strava_route_activities as cli
    from app.db.models import PendingArchiveFile
    from app.db.session import SessionLocal

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("routes/r.gpx", _gpx("My loop", 44.0, 3.0, trk_type="road_biking"))
        zf.writestr("activities/a.gpx", _gpx("Other", 44.5, 3.5, trk_type="road_biking"))
    uid, _email = _mk_user()
    _drained_archive(uid, buf.getvalue())
    ids = {a["name"]: str(a["id"]) for a in _activities(uid)}
    assert set(ids) == {"My loop", "Other"}

    # Per-member queue rows for the same files (no CSV sport on the sibling).
    db = SessionLocal()
    try:
        for fname, aid in (("routes/r.gpx", ids["My loop"]), ("activities/a.gpx", ids["Other"])):
            db.add(
                PendingArchiveFile(
                    user_id=uid,
                    storage_backend="local",
                    storage_key=f"{uid}/{uuid.uuid4()}",
                    original_filename=fname,
                    sport="unknown",
                    status="done",
                    activity_id=aid,
                )
            )
        db.commit()
    finally:
        db.close()

    assert cli.dry_run(user_id=uid)["candidates"] == 0


@pytest.mark.usefixtures("_archive_env")
def test_cli_member_row_requires_csv_sibling():
    """Legacy per-member queue: routes/ row is listed only when a sibling
    under the same root got its sport from activities.csv."""
    from app.cli import purge_strava_route_activities as cli
    from app.db.models import PendingArchiveFile
    from app.db.session import SessionLocal

    uid, _email = _mk_user()
    route_id = _ingest_raw(uid, "exp/routes/x.gpx", _gpx("Planned", 44.1, 3.6, trk_type="running").encode())
    consent = str(uuid.uuid4())

    def _row(fname, sport, aid=None):
        return PendingArchiveFile(
            user_id=uid,
            consent_id=consent,
            storage_backend="local",
            storage_key=f"{uid}/{uuid.uuid4()}",
            original_filename=fname,
            sport=sport,
            status="done",
            activity_id=aid,
        )

    db = SessionLocal()
    try:
        db.add(_row("exp/routes/x.gpx", "unknown", route_id))
        db.add(_row("exp/activities/1.gpx", "pick:road"))  # a pick, not a CSV sport
        db.commit()
    finally:
        db.close()
    assert cli.dry_run(user_id=uid)["candidates"] == 0

    db = SessionLocal()
    try:
        db.add(_row("exp/activities/2.gpx", "road"))  # CSV sport → Strava root
        db.commit()
    finally:
        db.close()
    dry = cli.dry_run(user_id=uid)
    assert dry["candidates"] == 1 and dry["by_method"] == {"member-row": 1}


@pytest.mark.usefixtures("_archive_env")
def test_cli_apply_skips_ids_that_no_longer_satisfy_the_guards(tmp_path):
    import json

    from app.cli import purge_strava_route_activities as cli
    from app.db.session import SessionLocal

    uid, _email = _mk_user()
    content = _strava_zip()
    _drained_archive(uid, content)
    ride_id = next(str(a["id"]) for a in _activities(uid) if a["name"] == "Morning spin")
    route_id = _ingest_raw(uid, "strava-export/routes/x.gpx", _read(content, "strava-export/routes/x.gpx"))

    out = tmp_path / "ids.json"
    assert cli.dry_run(user_id=uid, out=str(out))["candidates"] == 1
    # Tampered / stale list: add the real ride; and the route now has a date.
    listed = json.loads(out.read_text())
    listed.append({"activity_id": ride_id, "user_id": uid, "how": "archive-hash"})
    out.write_text(json.dumps(listed))
    db = SessionLocal()
    try:
        db.execute(sa_text("UPDATE activities SET activity_date = now() WHERE id = :id"), {"id": route_id})
        db.commit()
    finally:
        db.close()

    with patch("app.services.run_jobs.trigger_build_pmtiles_job", MagicMock(return_value=True)) as ping:
        applied = cli.apply(ids_file=str(out))
    assert applied["deleted"] == 0
    assert sorted(applied["skipped"]) == sorted([ride_id, route_id])
    assert ping.call_count == 0
    assert len(_activities(uid)) == 2
