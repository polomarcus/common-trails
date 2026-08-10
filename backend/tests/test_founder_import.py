"""Founder one-shot archive-import CLI (feat/founder-archive-import-cli).

Drives the REAL ``app.cli.founder_import.import_founder_archive`` function
against a LOCAL (filesystem) stand-in for the uploads bucket holding an
in-memory Strava-export-shaped .zip (2 GPX + 1 .fit.gz + activities.csv).

Pins the contract:
  * consent is RECORDED verbatim (user_id + version + text + locale, source=manual_upload).
  * every ingestible member is ingested with provenance source="manual_upload".
  * a .fit.gz member is handled (FIT parser stubbed like the sibling suite).
  * out-of-scope activities.csv rows (Yoga/…) are skipped, never ingested.
  * idempotent: a re-run imports 0 (file_hash dedup) and never double-counts.
  * dry-run writes NOTHING (no consent row, no activities).

Fails on absent code: importing ``app.cli.founder_import`` errors if the CLI
file does not exist.
"""
import gzip
import hashlib
import io
import json
import uuid
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


def _fit_gz(name: str) -> bytes:
    """Gzipped fake FIT bytes — parse_fit is stubbed (real FIT binary needs the
    Garmin SDK). Bytes carry ``name`` so distinct members hash distinctly."""
    return gzip.compress(b"FIT\x00" + name.encode())


def _fit_parsed_stub(raw: bytes) -> dict:
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


def _archive_zip() -> bytes:
    """Strava-export-shaped ZIP: activities.csv + 2 GPX + 1 .fit.gz + 1 Yoga (skip)."""
    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,2024-01-01,VTT session,MountainBikeRide,activities/mtb1.gpx\n"
        "2,2024-01-02,Morning spin,Ride,activities/road1.gpx\n"
        "3,2024-01-03,VTT gz,MountainBikeRide,activities/ride.fit.gz\n"
        "4,2024-01-04,Zen,Yoga,activities/yoga1.gpx\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/mtb1.gpx", _gpx("VTT session", 44.10, 3.60))
        zf.writestr("activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("activities/ride.fit.gz", _fit_gz("ride"))
        zf.writestr("activities/yoga1.gpx", _gpx("Zen", 43.60, 3.88))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
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
def _local_bucket(tmp_path, monkeypatch):
    """Force the local (filesystem) storage backend under a temp dir — the
    stand-in for UPLOADS_BUCKET."""
    from app.cli import founder_import
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")
    # Stub the binary FIT parser (the .fit.gz member is fake FIT bytes).
    monkeypatch.setattr("app.services.fit_parser.parse_fit", _fit_parsed_stub)
    assert founder_import._backend() == "local"


def _new_user() -> str:
    """Create a user directly and return its id (no HTTP needed)."""
    from app.db.models import User
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        u = User(
            email=f"founder_{uuid.uuid4().hex[:8]}@example.com",
            username=f"founder_{uuid.uuid4().hex[:6]}",
            hashed_password="x",
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _stage_archive(user_id: str) -> str:
    """Write the archive to the local bucket stand-in; return the bucket-key."""
    from app.services import archive_intake
    bucket_key = f"founder-import/{user_id}.zip"
    archive_intake.store_archive_local(bucket_key, _archive_zip())
    return bucket_key


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


class TestFounderImport:
    def test_records_consent_and_ingests_manual_upload(self):
        from app.cli.founder_import import import_founder_archive
        from app.db.models import Activity, ContributionConsent
        from app.db.session import SessionLocal

        uid = _new_user()
        key = _stage_archive(uid)

        summary = import_founder_archive(
            bucket_key=key,
            user_id=uid,
            consent_text="Je consens à contribuer mes traces (ODbL).",
            consent_version="founder-2026-07-13",
            locale="fr",
            pace_seconds=0.0,
            skip_heat_computation=True,
        )

        # 3 ingestible members (mtb GPX + road GPX + gravel FIT); Yoga skipped.
        assert summary["imported"] == 3, summary
        assert summary["failed"] == 0, summary
        assert summary["skipped"] >= 1, summary  # yoga out-of-scope
        assert summary["skipped_reasons"].get("out_of_scope") == 1, summary
        assert summary["consent_id"] is not None

        db = SessionLocal()
        try:
            consent = db.query(ContributionConsent).filter(
                ContributionConsent.id == summary["consent_id"]).one()
            assert consent.user_id == uid
            assert consent.source == "manual_upload"
            assert consent.consent_version == "founder-2026-07-13"
            assert "ODbL" in consent.consent_text
            assert consent.locale == "fr"

            acts = db.query(Activity).filter(Activity.user_id == uid).all()
            assert len(acts) == 3
            # #453 provenance separation: community-eligible source stamped.
            assert {a.source for a in acts} == {"manual_upload"}
            # GPX members → CSV sports; FIT member → gravel (device sport stub).
            assert sorted(a.sport for a in acts) == ["gravel", "mtb", "road"]
        finally:
            db.close()

    def test_fit_member_handled(self):
        """The .fit.gz member lands as a real Activity (gravel, manual_upload)."""
        from app.cli.founder_import import import_founder_archive
        from app.db.models import Activity
        from app.db.session import SessionLocal

        uid = _new_user()
        key = _stage_archive(uid)
        import_founder_archive(
            bucket_key=key, user_id=uid,
            consent_text="ODbL", consent_version="v1",
            pace_seconds=0.0, skip_heat_computation=True,
        )
        db = SessionLocal()
        try:
            gravel = db.query(Activity).filter(
                Activity.user_id == uid, Activity.sport == "gravel").all()
            assert len(gravel) == 1
            assert gravel[0].source == "manual_upload"
            assert gravel[0].name == "Garmin Activity"
        finally:
            db.close()

    def test_idempotent_on_rerun(self):
        from app.cli.founder_import import import_founder_archive
        from app.db.models import Activity, ContributionConsent

        uid = _new_user()
        key = _stage_archive(uid)
        kwargs = dict(
            bucket_key=key, user_id=uid,
            consent_text="ODbL", consent_version="v1",
            pace_seconds=0.0, skip_heat_computation=True,
        )
        first = import_founder_archive(**kwargs)
        assert first["imported"] == 3, first

        second = import_founder_archive(**kwargs)
        # file_hash dedup → nothing new; all 3 members skipped as already-existing.
        assert second["imported"] == 0, second
        assert second["failed"] == 0, second
        assert second["skipped_reasons"].get("already_exists") == 3, second

        # No duplicate activities; consent recorded once per run (audit trail).
        assert _count(Activity, user_id=uid) == 3
        assert _count(ContributionConsent, user_id=uid) == 2

    def test_dry_run_writes_nothing(self):
        from app.cli.founder_import import import_founder_archive
        from app.db.models import Activity, ContributionConsent

        uid = _new_user()
        key = _stage_archive(uid)
        summary = import_founder_archive(
            bucket_key=key, user_id=uid,
            consent_text="ODbL", consent_version="v1",
            pace_seconds=0.0, dry_run=True, skip_heat_computation=True,
        )
        assert summary["dry_run"] is True
        assert summary["imported"] == 0, summary
        assert summary["would_import"] == 3, summary
        assert summary["consent_id"] is None
        # Nothing persisted.
        assert _count(Activity, user_id=uid) == 0
        assert _count(ContributionConsent, user_id=uid) == 0

    def test_missing_object_raises(self):
        from app.cli.founder_import import import_founder_archive
        uid = _new_user()
        with pytest.raises(FileNotFoundError):
            import_founder_archive(
                bucket_key=f"founder-import/{uid}.zip",  # never staged
                user_id=uid, consent_text="ODbL", consent_version="v1",
                pace_seconds=0.0, skip_heat_computation=True,
            )
