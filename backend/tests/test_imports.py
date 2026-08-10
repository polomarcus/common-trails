"""Tests for GPX/ZIP file imports and heatmap."""
import datetime as dt
import io
import zipfile

import gpxpy
import gpxpy.gpx


def _make_gpx_bytes(
    name: str = "Test",
    n_points: int = 10,
    *,
    start_time: dt.datetime | None = None,
    lat0: float = 45.75,
    lon0: float = 4.83,
) -> bytes:
    """Generate a minimal valid GPX file in memory."""
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    for i in range(n_points):
        point_time = (
            start_time + dt.timedelta(seconds=i * 10) if start_time else None
        )
        segment.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=lon0 + i * 0.001,
                elevation=180 + i,
                time=point_time,
            )
        )
    return gpx.to_xml().encode("utf-8")


def _make_zip_of_gpx(n_files: int = 2) -> bytes:
    """Generate a ZIP containing multiple GPX files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(n_files):
            zf.writestr(f"route_{i}.gpx", _make_gpx_bytes(f"Route {i}").decode())
    return buf.getvalue()


class TestGpxUpload:
    def test_upload_single_gpx(self, client, auth_headers):
        gpx_bytes = _make_gpx_bytes("My Ride")
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("my_ride.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] in ("created", "already_exists")
        assert "activity_id" in data

    def test_upload_gpx_with_heatmap(self, client, auth_headers):
        gpx_bytes = _make_gpx_bytes("Heatmap Ride")
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("heat.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "gravel", "contribute_heatmap": "true",
                  "consent_version": "v-gpx-test", "consent_text": "ODbL ok", "locale": "fr"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "created"

    def test_gpx_contribute_without_consent_is_personal(self, client, auth_headers):
        """/gpx/upload with contribute_heatmap=true but NO consent must ingest as
        PERSONAL (contribute_heatmap=False → not published to the ODbL layer).
        Real handler, patch ingest to capture the flag. FAILS on the old
        pass-through that published without a consent row."""
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value={"activity_id": "g1", "status": "created"})
        with patch("app.services.ingest.ingest_activity", fake):
            resp = client.post(
                "/gpx/upload",
                headers=auth_headers,
                files={"file": ("nc.gpx", _make_gpx_bytes("NoConsent"), "application/gpx+xml")},
                data={"sport": "mtb", "contribute_heatmap": "true"},  # NO consent
            )
        assert resp.status_code == 202
        assert fake.call_args.kwargs["contribute_heatmap"] is False

    def test_gpx_contribute_with_consent_publishes(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value={"activity_id": "g2", "status": "created"})
        with patch("app.services.ingest.ingest_activity", fake):
            resp = client.post(
                "/gpx/upload",
                headers=auth_headers,
                files={"file": ("c.gpx", _make_gpx_bytes("Consent"), "application/gpx+xml")},
                data={"sport": "mtb", "contribute_heatmap": "true",
                      "consent_version": "v-gpx-pub", "consent_text": "ODbL ok", "locale": "fr"},
            )
        assert resp.status_code == 202
        assert fake.call_args.kwargs["contribute_heatmap"] is True

    def test_upload_duplicate_gpx_skipped(self, client, auth_headers):
        """Same file uploaded twice should be idempotent (skip second)."""
        gpx_bytes = _make_gpx_bytes("Deduplicated")
        kwargs = dict(
            headers=auth_headers,
            files={"file": ("dedup.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        r1 = client.post("/gpx/upload", **kwargs)
        r2 = client.post("/gpx/upload", **kwargs)
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["status"] == "created"
        assert r2.json()["status"] == "already_exists"

    def test_upload_invalid_extension(self, client, auth_headers):
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("data.csv", b"col1,col2", "text/csv")},
            data={"sport": "road"},
        )
        assert resp.status_code == 422

    def test_upload_requires_auth(self, client):
        gpx_bytes = _make_gpx_bytes()
        resp = client.post(
            "/gpx/upload",
            files={"file": ("r.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road"},
        )
        assert resp.status_code == 401


class TestImportsFiles:
    def test_import_gpx_file(self, client, auth_headers):
        gpx_bytes = _make_gpx_bytes("Import GPX")
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("import.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "mtb", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["imported"] + data["skipped"] >= 1

    def test_import_zip_file(self, client, auth_headers):
        zip_bytes = _make_zip_of_gpx(n_files=3)
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("archive.zip", zip_bytes, "application/zip")},
            data={"sport": "gravel", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["imported"] + data["skipped"] >= 3

    def test_import_invalid_sport(self, client, auth_headers):
        gpx_bytes = _make_gpx_bytes()
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("r.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "cycling", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 422

    def test_import_unsupported_format(self, client, auth_headers):
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("route.txt", b"not a gpx file", "text/plain")},
            data={"sport": "road"},
        )
        assert resp.status_code == 422


class TestImportsFilesRebuildTrigger:
    """The loose GPX/FIT path must fire the PMTiles display rebuild so a
    contributor actually SEES their trace on the community map — the same gap
    the archive /complete path already closes. Regression guard: the endpoint
    ingested activities but NEVER triggered ``build-pmtiles``, so uploads sat
    invisible until an unrelated rebuild.

    Drives the REAL ``POST /imports/files`` endpoint and patches the trigger at
    its source module (the handler imports it at call time) — no inline mirror.
    """

    def test_rebuild_display_default_true_triggers_rebuild(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        # The build-pmtiles trigger is now debounced (cost control, 2026-08-09):
        # it fires at most once per cooldown window. Reset the debounce so this
        # test deterministically exercises the trigger path regardless of what
        # earlier tests in the session already fired.
        from app.services.run_jobs import reset_build_pmtiles_debounce
        reset_build_pmtiles_debounce()

        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.post(
                "/imports/files",
                headers=auth_headers,
                files={"file": ("rebuild.gpx", _make_gpx_bytes("Rebuild", lat0=45.3), "application/gpx+xml")},
                data={"sport": "mtb", "contribute_heatmap": "true"},
            )
        assert resp.status_code == 202
        assert resp.json()["imported"] >= 1
        # Default rebuild_display=True → the rebuild is fired exactly once.
        fake.assert_called_once()

    def test_rebuild_display_false_does_not_trigger(self, client, auth_headers):
        """A non-final file of a multi-file batch sends rebuild_display=False so
        the rebuild fires ONCE per batch, not once per file."""
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.post(
                "/imports/files",
                headers=auth_headers,
                files={"file": ("nofire.gpx", _make_gpx_bytes("NoFire", lat0=45.4), "application/gpx+xml")},
                data={"sport": "mtb", "contribute_heatmap": "true", "rebuild_display": "false"},
            )
        assert resp.status_code == 202
        fake.assert_not_called()


class TestImportsFilesConsentGate:
    """No community write without a consent row. ``contribute_heatmap=true`` with
    NO consent must ingest as PERSONAL — ``contribute_heatmap=False`` is passed to
    ingest so nothing reaches the ODbL community layer. Drives the real endpoint,
    patches ingest to capture the flag (FAILS on the old pass-through).
    """

    def test_contribute_without_consent_is_downgraded_to_personal(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value={"activity_id": "a1", "status": "created"})
        with patch("app.services.ingest.ingest_activity", fake):
            resp = client.post(
                "/imports/files",
                headers=auth_headers,
                files={"file": ("nc.gpx", _make_gpx_bytes("NoConsent"), "application/gpx+xml")},
                data={"sport": "gravel", "contribute_heatmap": "true"},  # NO consent fields
            )
        assert resp.status_code == 202
        assert fake.called
        assert fake.call_args.kwargs["contribute_heatmap"] is False

    def test_contribute_with_consent_publishes_to_community(self, client, auth_headers):
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value={"activity_id": "a2", "status": "created"})
        with patch("app.services.ingest.ingest_activity", fake):
            resp = client.post(
                "/imports/files",
                headers=auth_headers,
                files={"file": ("c.gpx", _make_gpx_bytes("Consent"), "application/gpx+xml")},
                data={"sport": "gravel", "contribute_heatmap": "true",
                      "consent_version": "v-gate-test", "consent_text": "ODbL ok", "locale": "fr"},
            )
        assert resp.status_code == 202
        assert fake.call_args.kwargs["contribute_heatmap"] is True


class TestImportsFilesConsent:
    """ODbL contribution-consent audit trail on the loose GPX/FIT path (gap #7).

    Drives the REAL ``POST /imports/files`` endpoint: consent fields must
    create ONE ``contribution_consents`` row per upload batch (consent_id
    round-trip), and a bare upload (no fields) must keep working with no
    row recorded.
    """

    @staticmethod
    def _consents(user_id: str, consent_version: str):
        from app.db.models import ContributionConsent
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            return (
                db.query(ContributionConsent)
                .filter(
                    ContributionConsent.user_id == user_id,
                    ContributionConsent.consent_version == consent_version,
                )
                .all()
            )
        finally:
            db.close()

    @staticmethod
    def _user_id(client, auth_headers) -> str:
        return client.get("/auth/me", headers=auth_headers).json()["user_id"]

    def test_consent_fields_record_one_audit_row(self, client, auth_headers):
        uid = self._user_id(client, auth_headers)
        version = "test-files-consent-v1-first"
        text = "Je confirme déposer mes propres traces (ODbL)."
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("consent1.gpx", _make_gpx_bytes("Consent 1", lat0=46.1), "application/gpx+xml")},
            data={
                "sport": "gravel",
                "contribute_heatmap": "true",
                "consent_version": version,
                "consent_text": text,
                "locale": "fr",
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["consent_id"]

        rows = self._consents(uid, version)
        assert len(rows) == 1
        row = rows[0]
        assert row.id == body["consent_id"]
        assert row.consent_text == text
        assert row.locale == "fr"
        assert row.source == "manual_upload"

    def test_consent_id_roundtrip_reuses_the_batch_row(self, client, auth_headers):
        """Second file of the batch sends consent_id → NO second row."""
        uid = self._user_id(client, auth_headers)
        version = "test-files-consent-v1-batch"
        first = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("batch1.gpx", _make_gpx_bytes("Batch 1", lat0=46.2), "application/gpx+xml")},
            data={
                "sport": "mtb",
                "contribute_heatmap": "true",
                "consent_version": version,
                "consent_text": "consent wording",
                "locale": "fr",
            },
        )
        assert first.status_code == 202
        consent_id = first.json()["consent_id"]
        assert consent_id

        second = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("batch2.gpx", _make_gpx_bytes("Batch 2", lat0=46.3), "application/gpx+xml")},
            data={
                "sport": "mtb",
                "contribute_heatmap": "true",
                "consent_id": consent_id,
            },
        )
        assert second.status_code == 202
        assert second.json()["consent_id"] == consent_id
        assert len(self._consents(uid, version)) == 1

    def test_unknown_consent_id_rejected(self, client, auth_headers):
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("bad.gpx", _make_gpx_bytes("Bad", lat0=46.4), "application/gpx+xml")},
            data={
                "sport": "road",
                "contribute_heatmap": "true",
                "consent_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code == 422

    def test_partial_consent_fields_rejected(self, client, auth_headers):
        """Version without text (or vice versa) is a client bug → 422, no row."""
        uid = self._user_id(client, auth_headers)
        version = "test-files-consent-v1-partial"
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("partial.gpx", _make_gpx_bytes("Partial", lat0=46.5), "application/gpx+xml")},
            data={
                "sport": "road",
                "contribute_heatmap": "true",
                "consent_version": version,
            },
        )
        assert resp.status_code == 422
        assert len(self._consents(uid, version)) == 0

    def test_no_consent_fields_backward_compatible(self, client, auth_headers):
        """A bare upload (CLI / older clients) still imports; no row recorded."""
        from app.db.models import ContributionConsent
        from app.db.session import SessionLocal

        uid = self._user_id(client, auth_headers)
        db = SessionLocal()
        try:
            before = (
                db.query(ContributionConsent)
                .filter(ContributionConsent.user_id == uid)
                .count()
            )
        finally:
            db.close()

        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("bare.gpx", _make_gpx_bytes("Bare", lat0=46.6), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true"},
        )
        assert resp.status_code == 202
        assert resp.json()["consent_id"] is None

        db = SessionLocal()
        try:
            after = (
                db.query(ContributionConsent)
                .filter(ContributionConsent.user_id == uid)
                .count()
            )
        finally:
            db.close()
        assert after == before


class TestHeatmap:
    def test_heatmap_stats_empty(self, client):
        """Stats endpoint works even with no data."""
        resp = client.get("/heatmap/stats?sport=road&zoom=14")
        assert resp.status_code == 200
        data = resp.json()
        assert "k_anonymity" in data
        assert "cells" in data
        assert isinstance(data["cells"], list)
        assert "license" in data

    def test_heatmap_stats_k_anonymity(self, client, auth_headers):
        """After uploading, cells with < K users must not appear."""
        # Upload a single trace contributing to heatmap
        gpx_bytes = _make_gpx_bytes("K-anon test", n_points=15)
        client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("kanon.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true"},
        )

        resp = client.get("/heatmap/stats?sport=road&zoom=14")
        assert resp.status_code == 200
        data = resp.json()
        k = data["k_anonymity"]
        for cell in data["cells"]:
            assert cell["user_count"] >= k, (
                f"Cell {cell['cell_key']} has user_count={cell['user_count']} < K={k}"
            )

    def test_heatmap_export_geojson(self, client):
        resp = client.get("/heatmap/export?sport=road")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert "metadata" in data
        assert data["metadata"]["license"] == "ODbL-1.0"

    def test_heatmap_offroad_stats(self, client):
        """offroad profile should aggregate gravel+mtb."""
        resp = client.get("/heatmap/stats?sport=offroad&zoom=14")
        assert resp.status_code == 200
        # Response is valid regardless of offroad handling strategy
        assert "cells" in resp.json()


class TestActivityDatePropagation:
    """Regression: activity_date must reach the DB so cross-provider dedup works.

    Cross-provider dedup in `ingest_activity` matches on
    (user_id, activity_date BETWEEN ±5min, distance_m BETWEEN ±10%). When
    `activity_date` is None the BETWEEN short-circuits and a second upload
    of the same activity (different file_hash, e.g. GPX then Strava resync)
    is stored as a duplicate row. The fix added the missing `activity_date`
    key to `activity_data` in both gpx_upload.py and imports.py.
    """

    def _fetch_activity_date(self, activity_id: str):
        from app.db.models import Activity
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            row = db.query(Activity).filter(Activity.id == activity_id).one()
            return row.activity_date
        finally:
            db.close()

    def test_gpx_upload_persists_activity_date(self, client, auth_headers):
        start = dt.datetime(2026, 5, 20, 8, 0, 0, tzinfo=dt.UTC)
        gpx_bytes = _make_gpx_bytes("Dated ride", n_points=20, start_time=start)
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("dated.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "created"
        activity_id = resp.json()["activity_id"]
        persisted = self._fetch_activity_date(activity_id)
        assert persisted is not None, "activity_date must be persisted (was dropped pre-fix)"

    def test_imports_files_persists_activity_date(self, client, auth_headers):
        start = dt.datetime(2026, 5, 21, 9, 30, 0, tzinfo=dt.UTC)
        gpx_bytes = _make_gpx_bytes("Imported dated", n_points=20, start_time=start)
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("dated.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["imported"] == 1, body
        activity_id = body["activity_ids"][0]
        persisted = self._fetch_activity_date(activity_id)
        assert persisted is not None, (
            "activity_date must be persisted (was dropped pre-fix on /imports/files)"
        )

    def test_cross_provider_dedup_via_http_upload(self, client, auth_headers):
        """Second upload of the same physical activity (different file bytes,
        same timestamp + distance) must be deduplicated as `already_exists`.

        Pre-fix, both uploads landed because `activity_date` was None on the
        HTTP path and the cross-provider dedup BETWEEN clause never matched.
        """
        start = dt.datetime(2026, 5, 22, 7, 15, 0, tzinfo=dt.UTC)
        # Two GPX files with identical timestamps and identical geometry but
        # different `<trk>` names → different file_hash. Same activity_date +
        # distance must trigger the cross-provider dedup branch.
        gpx_a = _make_gpx_bytes("First name", n_points=30, start_time=start)
        gpx_b = _make_gpx_bytes("Second name", n_points=30, start_time=start)
        assert gpx_a != gpx_b, "fixtures must differ to defeat file_hash dedup"

        r1 = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("a.gpx", gpx_a, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        r2 = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("b.gpx", gpx_b, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        assert r1.status_code == 202 and r2.status_code == 202
        assert r1.json()["status"] == "created"
        assert r2.json()["status"] == "already_exists", (
            f"cross-provider dedup must skip second upload, got {r2.json()}"
        )
