"""Consented Strava-archive community-contribution intake (feat/strava-archive-consent-import).

Drives the REAL endpoint + the REAL paced worker (no inline mirrors):

  * consent is REQUIRED — no consent → 422, nothing stored/enqueued.
  * with consent → a ContributionConsent audit row is recorded AND each
    archive member is ENQUEUED (not ingested on the request thread),
    tagged source="manual_upload" with the activities.csv sport.
  * out-of-scope CSV rows (Yoga/…) are skipped, never enqueued (skip beats pollute).
  * the paced worker drains the queue and ingests each member, stamping
    provenance source="manual_upload" on the resulting Activity.
"""
import gzip
import hashlib
import io
import json
import zipfile

import gpxpy
import gpxpy.gpx
import pytest


def _gpx(name: str, lat0: float, lon0: float, n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001, elevation=100 + i,
        ))
    return gpx.to_xml()


def _archive_zip() -> bytes:
    """A Strava-export-shaped ZIP: activities.csv + 3 members (mtb, road, yoga)."""
    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,2024-01-01,VTT session,MountainBikeRide,activities/mtb1.gpx\n"
        "2,2024-01-02,Morning spin,Ride,activities/road1.gpx\n"
        "3,2024-01-03,Zen,Yoga,activities/yoga1.gpx\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/mtb1.gpx", _gpx("VTT session", 44.10, 3.60))
        zf.writestr("activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("activities/yoga1.gpx", _gpx("Zen", 43.60, 3.88))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    """Truncate the archive-intake tables before each test.

    ``drain_pending`` claims pending rows GLOBALLY (no user filter — it is a
    system-wide worker), so a sibling test that enqueues but never drains
    leaves ``pending_archive_files`` rows behind. The next test's drain would
    then pick them up and fail (their member files were written under the
    PREVIOUS test's function-scoped ``tmp_path``, which no longer exists once
    that test ended). Isolating on these tables makes each test self-contained.
    """
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(sa_text(
            "TRUNCATE pending_archive_files, pending_archives, contribution_consents"
        ))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _local_intake_dir(tmp_path, monkeypatch):
    """Force local (filesystem) storage backend under a temp dir."""
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _user_id(client, auth_headers) -> str:
    return client.get("/auth/me", headers=auth_headers).json()["user_id"]


def _count(model, **flt) -> int:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        q = db.query(model)
        for k, v in flt.items():
            q = q.filter(getattr(model, k) == v)
        return q.count()
    finally:
        db.close()


class TestConsentGate:
    def test_no_consent_rejected_and_nothing_stored(self, client, auth_headers):
        uid = _user_id(client, auth_headers)
        from app.db.models import ContributionConsent, PendingArchiveFile
        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "false",
                "consent_version": "v1",
                "consent_text": "I consent",
            },
        )
        assert resp.status_code == 422, resp.text
        assert _count(ContributionConsent, user_id=uid) == 0
        assert _count(PendingArchiveFile, user_id=uid) == 0

    def test_missing_consent_version_rejected(self, client, auth_headers):
        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "true",
                "consent_version": "   ",
                "consent_text": "I consent",
            },
        )
        assert resp.status_code == 422, resp.text


class TestConsentedEnqueue:
    def test_records_consent_and_enqueues_tagged_members(self, client, auth_headers):
        uid = _user_id(client, auth_headers)
        from app.db.models import ContributionConsent, PendingArchiveFile
        from app.db.session import SessionLocal

        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "true",
                "consent_version": "strava-archive-2026-07-v1",
                "consent_text": "Je consens à contribuer mes traces (ODbL).",
                "locale": "fr",
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "accepted"
        # 2 in-scope members enqueued, the Yoga row skipped (not enqueued).
        assert body["enqueued"] == 2
        assert body["skipped"] >= 1

        # Consent audit row recorded verbatim.
        db = SessionLocal()
        try:
            consent = db.query(ContributionConsent).filter(
                ContributionConsent.id == body["consent_id"]).one()
            assert consent.user_id == uid
            assert consent.source == "manual_upload"
            assert consent.consent_version == "strava-archive-2026-07-v1"
            assert "ODbL" in consent.consent_text
            assert consent.locale == "fr"

            pend = db.query(PendingArchiveFile).filter(
                PendingArchiveFile.consent_id == body["consent_id"]).all()
            assert {p.status for p in pend} == {"pending"}
            assert {p.source for p in pend} == {"manual_upload"}
            sports = sorted(p.sport for p in pend)
            assert sports == ["mtb", "road"]  # from activities.csv, not the form
        finally:
            db.close()


class TestPacedWorkerIngestsWithProvenance:
    def test_worker_drains_and_stamps_source(self, client, auth_headers):
        uid = _user_id(client, auth_headers)
        from app.db.models import Activity, PendingArchiveFile
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending

        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "true",
                "consent_version": "v1",
                "consent_text": "consent (ODbL)",
            },
        )
        assert resp.status_code == 202, resp.text

        # Drive the REAL worker; pace 0 + skip heat compute keeps it fast/OSM-free.
        summary = drain_pending(limit=50, pace_seconds=0.0, skip_heat_computation=True)
        assert summary["imported"] == 2, summary
        assert summary["failed"] == 0, summary

        db = SessionLocal()
        try:
            acts = db.query(Activity).filter(Activity.user_id == uid).all()
            assert len(acts) == 2
            assert {a.source for a in acts} == {"manual_upload"}
            assert sorted(a.sport for a in acts) == ["mtb", "road"]
            # Queue fully drained (done for the 2 ingested members).
            pend = db.query(PendingArchiveFile).filter(
                PendingArchiveFile.user_id == uid).all()
            assert {p.status for p in pend} == {"done"}
            assert all(p.activity_id for p in pend)
        finally:
            db.close()


# ── Signed-URL WHOLE-archive flow (init → PUT → complete → job), migration 0060 ──


def _fit_gz(name: str) -> bytes:
    """Gzipped fake FIT bytes — parse_fit is stubbed in tests (real FIT binary
    generation needs the Garmin SDK). The bytes carry ``name`` so distinct
    members hash distinctly."""
    return gzip.compress(b"FIT\x00" + name.encode())


def _fit_parsed_stub(raw: bytes) -> dict:
    """Stand-in for ``fit_parser.parse_fit`` — returns a valid parsed dict with
    a real LineString and a content-derived file_hash (so idempotence holds)."""
    coords = [[3.60 + i * 0.001, 44.10 + i * 0.001, 100 + i] for i in range(8)]
    return {
        "name": "Garmin Activity",
        "geometry_geojson": json.dumps({"type": "LineString", "coordinates": coords}),
        "distance_m": 1234.0,
        "elevation_gain_m": 42.0,
        "file_hash": hashlib.sha256(raw).hexdigest(),
        "coord_count": len(coords),
        "activity_date": None,
        "sport": "gravel",
    }


def _archive_zip_with_fit() -> bytes:
    """Strava-export-shaped ZIP: activities.csv + a .gpx member + a .fit.gz member."""
    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,2024-01-01,Morning spin,Ride,activities/road1.gpx\n"
        "2,2024-01-02,VTT gz,MountainBikeRide,activities/ride.fit.gz\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("activities/ride.fit.gz", _fit_gz("ride"))
    return buf.getvalue()


def _init(client, auth_headers, *, consent="true", version="strava-archive-2026-07-v1"):
    return client.post(
        "/imports/strava-archive/init",
        headers=auth_headers,
        json={
            "sport": "road",
            "consent": consent == "true",
            "consent_version": version,
            "consent_text": "Je consens à contribuer mes traces (ODbL).",
            "locale": "fr",
            "filename": "export.zip",
        },
    )


class TestInitConsentGate:
    def test_init_without_consent_rejected_and_nothing_recorded(self, client, auth_headers):
        uid = _user_id(client, auth_headers)
        from app.db.models import ContributionConsent, PendingArchive
        resp = _init(client, auth_headers, consent="false")
        assert resp.status_code == 422, resp.text
        assert _count(ContributionConsent, user_id=uid) == 0
        assert _count(PendingArchive, user_id=uid) == 0

    def test_init_missing_version_rejected(self, client, auth_headers):
        resp = _init(client, auth_headers, version="   ")
        assert resp.status_code == 422, resp.text


class TestInitReturnsSignedUrl:
    def test_init_gcs_returns_signed_url_and_records_row(self, client, auth_headers, monkeypatch):
        """With a bucket configured, init returns a V4 signed PUT URL (signer
        mocked) + an awaiting_upload row + a consent audit row."""
        uid = _user_id(client, auth_headers)
        from app.db.models import ContributionConsent, PendingArchive
        from app.db.session import SessionLocal
        from app.services import archive_intake

        monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "test-bucket")
        fake_url = "https://storage.googleapis.com/test-bucket/archive-intake/x.zip?X-Goog-Signature=deadbeef"
        monkeypatch.setattr(archive_intake, "generate_signed_put_url", lambda key: fake_url)

        resp = _init(client, auth_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["storage_backend"] == "gcs"
        assert body["upload_url"] == fake_url
        assert body["upload_method"] == "PUT"
        assert body["content_type"] == "application/zip"
        assert body["bucket_key"].startswith(f"archive-intake/{uid}/")

        db = SessionLocal()
        try:
            row = db.get(PendingArchive, body["archive_id"])
            assert row.user_id == uid
            assert row.status == "awaiting_upload"
            assert row.storage_backend == "gcs"
            assert _count(ContributionConsent, user_id=uid) == 1
        finally:
            db.close()

    def test_init_local_returns_backend_upload_path(self, client, auth_headers):
        """No bucket (dev/TEST_MODE) → a local backend upload path stand-in."""
        resp = _init(client, auth_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["storage_backend"] == "local"
        assert body["upload_url"] == f"/imports/strava-archive/upload/{body['archive_id']}"


class TestCompleteOwnershipAndFlip:
    def test_complete_flow_flips_status(self, client, auth_headers):
        body = _init(client, auth_headers).json()
        # PUT the archive to the local stand-in endpoint.
        put = client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        assert put.status_code == 200, put.text
        # Complete verifies ownership + object presence + flips to uploaded.
        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        assert comp.status_code == 202, comp.text
        assert comp.json()["status"] == "uploaded"
        assert comp.json()["enqueue"] == "scheduled"

        from app.db.models import PendingArchive
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            assert db.get(PendingArchive, body["archive_id"]).status == "uploaded"
        finally:
            db.close()

    def test_complete_triggers_job_and_reports_triggered(self, client, auth_headers):
        """Happy path: /complete flips the row to 'uploaded', calls the trigger
        helper ONCE, and reports enqueue='triggered' when it returned True."""
        from unittest.mock import MagicMock, patch

        from app.db.models import PendingArchive
        from app.db.session import SessionLocal

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())

        trigger = MagicMock(return_value=True)
        with patch.object(__import__("app.api.imports", fromlist=["imports"]),
                          "trigger_ingest_archives_job", trigger):
            comp = client.post(
                "/imports/strava-archive/complete",
                headers=auth_headers,
                json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
            )

        assert comp.status_code == 202, comp.text
        assert comp.json()["status"] == "uploaded"
        assert comp.json()["enqueue"] == "triggered"
        trigger.assert_called_once_with()

        db = SessionLocal()
        try:
            assert db.get(PendingArchive, body["archive_id"]).status == "uploaded"
        finally:
            db.close()

    def test_complete_survives_trigger_raising(self, client, auth_headers):
        """Best-effort: if the trigger helper RAISES, /complete still returns 202
        and the row stays 'uploaded' (the daily backstop drains it)."""
        from unittest.mock import MagicMock, patch

        from app.db.models import PendingArchive
        from app.db.session import SessionLocal

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())

        boom = MagicMock(side_effect=RuntimeError("metadata server unreachable"))
        with patch.object(__import__("app.api.imports", fromlist=["imports"]),
                          "trigger_ingest_archives_job", boom):
            comp = client.post(
                "/imports/strava-archive/complete",
                headers=auth_headers,
                json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
            )

        assert comp.status_code == 202, comp.text
        assert comp.json()["status"] == "uploaded"
        assert comp.json()["enqueue"] == "scheduled"
        boom.assert_called_once_with()

        db = SessionLocal()
        try:
            assert db.get(PendingArchive, body["archive_id"]).status == "uploaded"
        finally:
            db.close()

    def test_complete_rejects_foreign_key(self, client, auth_headers):
        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        # A key under a DIFFERENT user's prefix → 403.
        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": "archive-intake/someone-else/evil.zip"},
        )
        assert comp.status_code == 403, comp.text

    def test_complete_before_upload_is_409(self, client, auth_headers):
        body = _init(client, auth_headers).json()
        # No PUT happened → object absent → 409.
        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        assert comp.status_code == 409, comp.text


class TestWholeArchiveWorkerStreamsAndIngests:
    def test_worker_ingests_gpx_and_fit_tagging_manual_upload(self, client, auth_headers, monkeypatch):
        uid = _user_id(client, auth_headers)
        from app.db.models import Activity, PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives

        # Stub the binary FIT parser — the .fit.gz member is fake FIT bytes.
        monkeypatch.setattr("app.services.fit_parser.parse_fit", _fit_parsed_stub)

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )

        summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert summary["archives"] == 1, summary
        assert summary["imported"] == 2, summary  # 1 GPX + 1 FIT
        assert summary["failed"] == 0, summary

        db = SessionLocal()
        try:
            acts = db.query(Activity).filter(Activity.user_id == uid).all()
            assert len(acts) == 2
            # #453 provenance separation: community-eligible source stamped.
            assert {a.source for a in acts} == {"manual_upload"}
            # GPX member → road (CSV), FIT member → gravel (FIT device sport).
            assert sorted(a.sport for a in acts) == ["gravel", "road"]
            arch = db.get(PendingArchive, body["archive_id"])
            assert arch.status == "done"
            assert arch.imported == 2
        finally:
            db.close()

    def test_worker_is_idempotent_on_rerun(self, client, auth_headers, monkeypatch):
        uid = _user_id(client, auth_headers)
        from app.db.models import Activity, PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives

        monkeypatch.setattr("app.services.fit_parser.parse_fit", _fit_parsed_stub)

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        first = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert first["imported"] == 2, first

        # Re-arm the SAME archive + re-drain → file_hash dedup means 0 new imports.
        db = SessionLocal()
        try:
            db.get(PendingArchive, body["archive_id"]).status = "uploaded"
            db.commit()
        finally:
            db.close()
        second = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert second["imported"] == 0, second
        assert _count(Activity, user_id=uid) == 2


# ── Archive size guard (security fast-follow to #455) ────────────────────────
#
# The signed PUT URL binds content-TYPE but NOT size → an authenticated user
# could PUT a multi-GB object; the importer's download_to_filename would land
# it in the 2 Gi job's tmpfs BEFORE the zip-content guard runs → OOM/DoS. These
# drive the REAL /complete handler + the REAL drain_pending_archives worker.


class TestArchiveSizeGuard:
    def test_complete_rejects_oversize_object_and_marks_failed(
        self, client, auth_headers, monkeypatch
    ):
        """An object larger than MAX_ARCHIVE_BYTES → /complete returns 413,
        marks the row 'failed' (so it never drains), and deletes the object."""
        from app.db.models import PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives
        from app.services import archive_intake

        body = _init(client, auth_headers).json()
        # PUT the real archive FIRST (under the real, generous cap), then shrink
        # the cap so the object is now "oversize" at complete-time.
        put = client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        assert put.status_code == 200, put.text
        monkeypatch.setattr(archive_intake, "MAX_ARCHIVE_BYTES", 10)

        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        # On pre-fix code /complete had NO size check → 202 + status 'uploaded'.
        assert comp.status_code == 413, comp.text
        assert "too large" in comp.json()["detail"].lower()

        db = SessionLocal()
        try:
            row = db.get(PendingArchive, body["archive_id"])
            assert row.status == "failed"
            assert row.last_error and "too large" in row.last_error.lower()
        finally:
            db.close()

        # Object was deleted to free the bucket…
        assert not archive_intake.archive_exists(
            body["storage_backend"], body["bucket_key"]
        )
        # …and a drain never touches a 'failed' row (only 'uploaded' is claimed).
        summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert summary["archives"] == 0, summary
        assert summary["imported"] == 0, summary

    def test_complete_accepts_in_limit_object(self, client, auth_headers):
        """A normal-sized archive still completes (202 + 'uploaded') — the guard
        does not regress the happy path."""
        from app.db.models import PendingArchive
        from app.db.session import SessionLocal

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        assert comp.status_code == 202, comp.text
        db = SessionLocal()
        try:
            assert db.get(PendingArchive, body["archive_id"]).status == "uploaded"
        finally:
            db.close()

    def test_worker_skips_oversize_without_downloading(
        self, client, auth_headers, monkeypatch
    ):
        """Defense-in-depth: an 'uploaded' row whose object is oversize (e.g. a
        direct-bucket PUT that bypassed /complete) is failed by the worker
        WITHOUT ever streaming/downloading the object."""
        import contextlib

        from app.db.models import Activity, PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives
        from app.services import archive_intake

        # Complete normally (under the real cap) so the row is 'uploaded'.
        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )

        # Track whether the download/unzip is ever attempted.
        opened = {"called": False}
        real_open = archive_intake.open_archive_zip

        @contextlib.contextmanager
        def _tracking_open(backend, key):
            opened["called"] = True
            with real_open(backend, key) as zf:
                yield zf

        monkeypatch.setattr(archive_intake, "open_archive_zip", _tracking_open)
        # Now the object is "oversize" relative to the (shrunk) cap.
        monkeypatch.setattr(archive_intake, "MAX_ARCHIVE_BYTES", 10)

        uid = _user_id(client, auth_headers)
        summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)

        # On pre-fix code the worker would open_archive_zip (download) then fail
        # only inside the zip-content guard → opened["called"] would be True.
        assert opened["called"] is False, "oversize object must NOT be downloaded"
        assert summary["archives_failed"] == 1, summary
        assert summary["imported"] == 0, summary
        assert _count(Activity, user_id=uid) == 0

        db = SessionLocal()
        try:
            row = db.get(PendingArchive, body["archive_id"])
            assert row.status == "failed"
            assert row.last_error and "too large" in row.last_error.lower()
        finally:
            db.close()

    def test_worker_marks_zip_bomb_failed_with_human_reason(
        self, client, auth_headers, monkeypatch
    ):
        """A member-count / uncompressed-size zip-bomb is failed with a
        HUMAN-READABLE reason (never an opaque ZipBombError class name)."""
        from app.db.models import PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives
        from app.services import archive_intake
        from app.services import gpx as gpx_service

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_archive_zip_with_fit())
        client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )

        # Force the hardened cap to trip during member iteration.
        def _boom(zf):
            raise gpx_service.ZipBombError("ZIP decompresses too large; possible zip-bomb.")
            yield  # pragma: no cover — makes this a generator

        monkeypatch.setattr(archive_intake, "iter_zip_members", _boom)

        summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert summary["archives_failed"] == 1, summary

        db = SessionLocal()
        try:
            row = db.get(PendingArchive, body["archive_id"])
            assert row.status == "failed"
            assert row.last_error and "rejected" in row.last_error.lower()
            assert "ZipBombError" not in row.last_error
        finally:
            db.close()


# ── Event-driven job trigger helper (run_jobs.trigger_ingest_archives_job) ─────


class TestTriggerIngestArchivesJobHelper:
    def test_noop_without_network_in_test_mode(self, monkeypatch):
        """TEST_MODE (or an unset job-name env) → the helper returns False and
        makes NO HTTP call (metadata server / Cloud Run API untouched)."""
        from unittest.mock import MagicMock

        from app.services import run_jobs

        # Configure the project/region but keep TEST_MODE on — the gate must
        # still short-circuit before any network I/O.
        monkeypatch.setenv("TEST_MODE", "true")
        monkeypatch.setenv("GCP_PROJECT", "common-trails")
        monkeypatch.setenv("GCP_REGION", "europe-west1")
        monkeypatch.setenv("INGEST_ARCHIVES_JOB_NAME", "common-trails-ingest-pending-archives-prod")

        import httpx
        get_spy = MagicMock(side_effect=AssertionError("httpx.get must not be called"))
        post_spy = MagicMock(side_effect=AssertionError("httpx.post must not be called"))
        monkeypatch.setattr(httpx, "get", get_spy)
        monkeypatch.setattr(httpx, "post", post_spy)

        assert run_jobs.trigger_ingest_archives_job() is False
        get_spy.assert_not_called()
        post_spy.assert_not_called()

    def test_noop_when_job_name_unset(self, monkeypatch):
        """Not in TEST_MODE but the job-name env is unset → still a no-op."""
        from unittest.mock import MagicMock

        from app.services import run_jobs

        monkeypatch.setenv("TEST_MODE", "false")
        monkeypatch.setenv("GCP_PROJECT", "common-trails")
        monkeypatch.setenv("GCP_REGION", "europe-west1")
        monkeypatch.delenv("INGEST_ARCHIVES_JOB_NAME", raising=False)

        import httpx
        get_spy = MagicMock(side_effect=AssertionError("httpx.get must not be called"))
        monkeypatch.setattr(httpx, "get", get_spy)

        assert run_jobs.trigger_ingest_archives_job() is False
        get_spy.assert_not_called()


# ── Post-drain PMTiles rebuild ping (run_jobs.trigger_build_pmtiles_job) ───────


class TestBuildPmtilesJobHelper:
    def test_noop_without_network_in_test_mode(self, monkeypatch):
        """TEST_MODE (with the job-name env set) → the helper returns False and
        makes NO HTTP call (metadata server / Cloud Run API untouched)."""
        from unittest.mock import MagicMock

        from app.services import run_jobs

        monkeypatch.setenv("TEST_MODE", "true")
        monkeypatch.setenv("GCP_PROJECT", "common-trails")
        monkeypatch.setenv("GCP_REGION", "europe-west1")
        monkeypatch.setenv("BUILD_PMTILES_JOB_NAME", "common-trails-build-pmtiles-prod")

        import httpx
        get_spy = MagicMock(side_effect=AssertionError("httpx.get must not be called"))
        post_spy = MagicMock(side_effect=AssertionError("httpx.post must not be called"))
        monkeypatch.setattr(httpx, "get", get_spy)
        monkeypatch.setattr(httpx, "post", post_spy)

        assert run_jobs.trigger_build_pmtiles_job() is False
        get_spy.assert_not_called()
        post_spy.assert_not_called()

    def test_noop_when_job_name_unset(self, monkeypatch):
        """Not in TEST_MODE but the job-name env is unset → still a no-op."""
        from unittest.mock import MagicMock

        from app.services import run_jobs

        monkeypatch.setenv("TEST_MODE", "false")
        monkeypatch.setenv("GCP_PROJECT", "common-trails")
        monkeypatch.setenv("GCP_REGION", "europe-west1")
        monkeypatch.delenv("BUILD_PMTILES_JOB_NAME", raising=False)

        import httpx
        get_spy = MagicMock(side_effect=AssertionError("httpx.get must not be called"))
        monkeypatch.setattr(httpx, "get", get_spy)

        assert run_jobs.trigger_build_pmtiles_job() is False
        get_spy.assert_not_called()


class TestDrainPingsPmtilesRebuild:
    """The archive drain fires the PMTiles rebuild ONCE after ingesting >=1
    activity with heat computation on, and stays best-effort if the ping fails.
    Drives the REAL drain_pending worker; ingest_activity is patched so the test
    is OSM-free while the drain's real ping wiring runs."""

    def _enqueue_two(self, client, auth_headers):
        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "true",
                "consent_version": "v1",
                "consent_text": "consent (ODbL)",
            },
        )
        assert resp.status_code == 202, resp.text

    def test_successful_drain_pings_rebuild_once(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        from app.jobs import ingest_pending_archives as job

        self._enqueue_two(client, auth_headers)

        created = {"n": 0}

        def _fake_ingest(**kw):
            created["n"] += 1
            # activity_id None avoids the activities FK — the ping wiring under
            # test only cares about the imported count, not a real Activity row.
            return {"status": "created", "activity_id": None}

        ping = MagicMock(return_value=True)
        with patch.object(job.ingest_service, "ingest_activity", side_effect=_fake_ingest), \
             patch.object(job, "trigger_build_pmtiles_job", ping):
            # heat computation ON (skip=False) is what gates the ping.
            summary = job.drain_pending(limit=50, pace_seconds=0.0, skip_heat_computation=False)

        assert summary["imported"] == 2, summary
        ping.assert_called_once_with()

    def test_drain_completes_when_ping_raises(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        from app.db.models import PendingArchiveFile
        from app.db.session import SessionLocal
        from app.jobs import ingest_pending_archives as job

        uid = _user_id(client, auth_headers)
        self._enqueue_two(client, auth_headers)

        created = {"n": 0}

        def _fake_ingest(**kw):
            created["n"] += 1
            # activity_id None avoids the activities FK — the ping wiring under
            # test only cares about the imported count, not a real Activity row.
            return {"status": "created", "activity_id": None}

        boom = MagicMock(side_effect=RuntimeError("metadata server unreachable"))
        with patch.object(job.ingest_service, "ingest_activity", side_effect=_fake_ingest), \
             patch.object(job, "trigger_build_pmtiles_job", boom):
            summary = job.drain_pending(limit=50, pace_seconds=0.0, skip_heat_computation=False)

        # A raising ping must NOT fail the drain — rows stay committed as 'done'.
        assert summary["imported"] == 2, summary
        assert summary["failed"] == 0, summary
        boom.assert_called_once_with()

        db = SessionLocal()
        try:
            pend = db.query(PendingArchiveFile).filter(
                PendingArchiveFile.user_id == uid).all()
            assert pend and {p.status for p in pend} == {"done"}
        finally:
            db.close()

    def test_no_ping_when_heat_skipped(self, client, auth_headers):
        """The scale/test drain (skip_heat_computation=True) computes no heat →
        nothing new to display → the rebuild must NOT fire."""
        from unittest.mock import MagicMock, patch

        from app.jobs import ingest_pending_archives as job

        self._enqueue_two(client, auth_headers)

        created = {"n": 0}

        def _fake_ingest(**kw):
            created["n"] += 1
            # activity_id None avoids the activities FK — the ping wiring under
            # test only cares about the imported count, not a real Activity row.
            return {"status": "created", "activity_id": None}

        ping = MagicMock(return_value=True)
        with patch.object(job.ingest_service, "ingest_activity", side_effect=_fake_ingest), \
             patch.object(job, "trigger_build_pmtiles_job", ping):
            summary = job.drain_pending(limit=50, pace_seconds=0.0, skip_heat_computation=True)

        assert summary["imported"] == 2, summary
        ping.assert_not_called()
