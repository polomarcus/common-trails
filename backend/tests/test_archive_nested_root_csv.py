"""Nested-root re-zipped Strava archive (fix/nested-archive-csv).

The real user trap (found with Paul's actual 817 MB archive): unzip the
Strava export, then macOS right-click → Compress the FOLDER. Everything
lands under a root prefix (``myexport/activities.csv`` +
``myexport/activities/1.gpx``) plus ``__MACOSX/`` junk and AppleDouble
``._*`` members. Old behaviour:

  * activities.csv detection was ROOT-ONLY → the nested CSV was never
    found → EVERY member fell back to ``fallback_sport`` → mass
    single-sport heatmap pollution AND out-of-scope (Yoga) members were
    ingested instead of skipped — the exact opposite of the
    "sport ambiguous → skip, never pollute" doctrine.
  * AppleDouble/``__MACOSX`` junk reached the GPX parser as bogus members.

These drive the REAL ``iter_zip_members`` and the REAL
``drain_pending_archives`` worker via the real endpoints — no inline
mirrors. The flat root-level archive is the control case (unchanged).
"""
import io
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


_CSV = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
    "1,2024-01-01,VTT session,MountainBikeRide,activities/mtb1.gpx\n"
    "2,2024-01-02,Morning spin,Ride,activities/road1.gpx\n"
    "3,2024-01-03,Zen,Yoga,activities/yoga1.gpx\n"
)


def _nested_zip() -> bytes:
    """Paul's exact structure: folder re-zipped on macOS — root prefix +
    __MACOSX mirror + AppleDouble ``._*`` members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("myexport/activities.csv", _CSV)
        zf.writestr("myexport/activities/mtb1.gpx", _gpx("VTT session", 44.10, 3.60))
        zf.writestr("myexport/activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("myexport/activities/yoga1.gpx", _gpx("Zen", 43.60, 3.88))
        zf.writestr("__MACOSX/myexport/._activities.csv", b"\x00\x05\x16\x07AppleDouble")
        zf.writestr("myexport/activities/._1.gpx", b"\x00\x05\x16\x07AppleDouble")
    return buf.getvalue()


def _flat_zip() -> bytes:
    """Control: the pristine root-level Strava export shape."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", _CSV)
        zf.writestr("activities/mtb1.gpx", _gpx("VTT session", 44.10, 3.60))
        zf.writestr("activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
        zf.writestr("activities/yoga1.gpx", _gpx("Zen", 43.60, 3.88))
    return buf.getvalue()


def _members(zip_bytes: bytes) -> list[tuple]:
    from app.services import archive_intake
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return list(archive_intake.iter_zip_members(zf))


def _sports_by_basename(members: list[tuple]) -> dict:
    return {name.rsplit("/", 1)[-1]: sport for name, _raw, sport in members}


class TestIterZipMembersNestedRoot:
    def test_nested_csv_is_found_and_sports_resolve(self):
        members = _members(_nested_zip())
        sports = _sports_by_basename(members)
        # CSV IS found at depth → authoritative sports apply.
        assert sports["mtb1.gpx"] == "mtb"
        assert sports["road1.gpx"] == "road"
        # Out-of-scope Yoga → None → the caller skips (never pollutes).
        assert sports["yoga1.gpx"] is None

    def test_macosx_and_appledouble_members_not_yielded(self):
        names = [name for name, _raw, _sport in _members(_nested_zip())]
        assert all("__MACOSX" not in n.split("/") for n in names)
        assert all(not n.rsplit("/", 1)[-1].startswith("._") for n in names)
        assert len(names) == 3  # exactly the real GPX members

    def test_flat_root_zip_unchanged_control(self):
        members = _members(_flat_zip())
        sports = _sports_by_basename(members)
        assert sports == {"mtb1.gpx": "mtb", "road1.gpx": "road", "yoga1.gpx": None}
        assert [name for name, _r, _s in members] == [
            "activities/mtb1.gpx", "activities/road1.gpx", "activities/yoga1.gpx",
        ]


def _junk_inflated_zip(real: int, junk: int) -> bytes:
    """`real` legitimate nested GPX members + `junk` macOS artefacts
    (alternating __MACOSX/ mirror entries and in-tree AppleDouble ._*)."""
    gpx_xml = _gpx("ride", 44.0, 4.0)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(real):
            zf.writestr(f"myexport/activities/{i}.gpx", gpx_xml)
        for i in range(junk):
            if i % 2:
                zf.writestr(f"__MACOSX/myexport/activities/._{i}.gpx", b"\x00\x05\x16\x07")
            else:
                zf.writestr(f"myexport/activities/._{i}.gpx", b"\x00\x05\x16\x07")
    return buf.getvalue()


class TestJunkMembersDontCountAgainstCap:
    """Prod failure 2026-07-13: Paul's re-zipped 817 MB export (2813 real
    activities) was rejected ``ZIP has too many members (6194 > 5000)`` —
    the ``__MACOSX/`` mirror + AppleDouble ``._*`` junk DOUBLED the raw
    entry count. Junk must be filtered BEFORE the MAX_ZIP_MEMBERS cap;
    the cap itself (on non-junk members) must NOT weaken."""

    def test_junk_inflated_legit_archive_passes_cap(self, monkeypatch):
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 40)
        # 30 real + 31 junk = 61 raw entries (> 40); 30 non-junk (<= 40).
        members = _members(_junk_inflated_zip(real=30, junk=31))
        assert len(members) == 30
        assert all(
            not n.rsplit("/", 1)[-1].startswith("._") for n, _r, _s in members
        )

    def test_nonjunk_count_over_cap_still_raises(self, monkeypatch):
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 40)
        # Error message reports the NON-JUNK count (45), not the raw 55.
        with pytest.raises(gpx_service.ZipBombError, match=r"\(45 > 40\)"):
            _members(_junk_inflated_zip(real=45, junk=10))

    def test_unsafe_path_on_nonjunk_member_still_raises(self):
        from app.services import archive_intake
        from app.services import gpx as gpx_service
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("activities/../evil.gpx", "x")
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf, \
                pytest.raises(gpx_service.ZipBombError, match="unsafe path"):
            list(archive_intake.iter_zip_members(zf))

    def test_parse_zip_of_gpx_same_junk_aware_cap(self, monkeypatch):
        """The OLD small-zip path had the identical all-members cap trap."""
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 40)
        results = gpx_service.parse_zip_of_gpx(_junk_inflated_zip(real=30, junk=31))
        ok = [r for r in results if "error" not in r]
        assert len(ok) == 30
        # Junk never reaches the parser (an AppleDouble ._x.gpx would have
        # landed as a per-member parse error before the fix).
        assert not [r for r in results if "error" in r]
        with pytest.raises(gpx_service.ZipBombError, match=r"\(45 > 40\)"):
            gpx_service.parse_zip_of_gpx(_junk_inflated_zip(real=45, junk=10))


# ── End-to-end: real endpoints + real drain worker ───────────────────────────


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
def _local_intake_dir(tmp_path, monkeypatch):
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _user_id(client, auth_headers) -> str:
    return client.get("/auth/me", headers=auth_headers).json()["user_id"]


def _init(client, auth_headers):
    return client.post(
        "/imports/strava-archive/init",
        headers=auth_headers,
        json={
            "sport": "road",  # fallback — must NOT override the CSV sports
            "consent": True,
            "consent_version": "strava-archive-2026-07-v1",
            "consent_text": "Je consens à contribuer mes traces (ODbL).",
            "locale": "fr",
            "filename": "export.zip",
        },
    )


class TestDrainNestedArchive:
    def test_worker_uses_csv_sports_and_skips_junk(self, client, auth_headers):
        uid = _user_id(client, auth_headers)
        from app.db.models import Activity, PendingArchive
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import drain_pending_archives

        body = _init(client, auth_headers).json()
        client.put(body["upload_url"], headers=auth_headers, content=_nested_zip())
        comp = client.post(
            "/imports/strava-archive/complete",
            headers=auth_headers,
            json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
        )
        assert comp.status_code == 202, comp.text

        summary = drain_pending_archives(limit=5, pace_seconds=0.0, skip_heat_computation=True)
        assert summary["archives"] == 1, summary
        assert summary["imported"] == 2, summary  # mtb + road from the CSV
        assert summary["skipped"] == 1, summary   # yoga → out-of-scope skip
        assert summary["failed"] == 0, summary    # junk never reached the parser

        db = SessionLocal()
        try:
            acts = db.query(Activity).filter(Activity.user_id == uid).all()
            # CSV sports applied — NOT the "road" fallback for everything.
            assert sorted(a.sport for a in acts) == ["mtb", "road"]
            arch = db.get(PendingArchive, body["archive_id"])
            assert arch.status == "done"
            assert arch.members_total == 3  # AppleDouble/__MACOSX not counted
        finally:
            db.close()
