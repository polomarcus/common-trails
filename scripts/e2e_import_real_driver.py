"""In-container driver for ``scripts/e2e-import-real.sh`` — LOCAL ONLY, never CI.

Runs the real archive pipeline on one export mounted at ``/input/archive.zip``
against the throwaway database the shell script started:

    alembic upgrade head → a fresh user + consent + ``pending_archives`` row
    (exactly what /init + PUT + /complete leave behind) → the real
    ``drain_pending_archives`` (heat computation ON, like the prod job)
    → counts, skip reasons, sports, emails, timings, peak RSS.

``REDROP=1`` drops the same archive a second time (idempotence check).
Nothing leaves the container: emails are captured, the rebuild ping is
captured, there is no bucket.
"""

from __future__ import annotations

import collections
import os
import resource
import subprocess
import sys
import time
import uuid

ARCHIVE = "/input/archive.zip"


def main() -> int:
    for attempt in range(10):  # the fresh PostGIS restarts once during init
        mig = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
        if mig.returncode == 0:
            break
        time.sleep(3)
    else:
        print(mig.stderr[-3000:], file=sys.stderr)
        return 1

    # The driver runs as /driver.py: make the mounted backend importable
    # (appended, so /app/alembic — the migrations dir — never shadows the
    # alembic package).
    sys.path.append("/app")
    from sqlalchemy import text as sa_text

    from app.db.models import PendingArchive, User
    from app.db.session import SessionLocal
    from app.jobs import ingest_pending_archives as drain_mod
    from app.services import archive_intake
    from app.services import email as email_service

    # Per-member tracebacks would flood the terminal on a real export: the
    # failures are aggregated below instead.
    import logging

    logging.getLogger().setLevel(logging.WARNING)
    for name in ("app", "app.jobs.ingest_pending_archives", "app.services"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    emails: list[tuple[str, str]] = []
    pings: list[float] = []
    reasons: collections.Counter = collections.Counter()
    errors: collections.Counter = collections.Counter()
    member_s: list[float] = []

    def fake_send(_to, subject, html):
        emails.append((subject, html))
        return True

    def fake_ping():
        pings.append(time.monotonic())
        return True

    real_iter = archive_intake.iter_zip_members
    real_ingest = drain_mod._ingest_member_bytes

    def spy_iter(zf, **kw):
        for name, raw, csv_sport in real_iter(zf, **kw):
            if csv_sport is None:
                reasons["unlisted (activities/ not in CSV)" if raw is None else "out of scope (CSV type)"] += 1
            yield name, raw, csv_sport

    def spy_ingest(**kw):
        t0 = time.monotonic()
        try:
            outcome, aid, err = real_ingest(**kw)
        except Exception as exc:
            errors[f"{type(exc).__name__}: {str(exc)[:90]}"] += 1
            raise
        finally:
            member_s.append(time.monotonic() - t0)
        if outcome == "skipped":
            if err is None:
                reasons["duplicate"] += 1
            elif err in ("no_geometry", "sport_unknown"):
                reasons[err] += 1
            else:
                reasons["out of scope (file sport)"] += 1
        return outcome, aid, err

    email_service.send_email = fake_send
    drain_mod.trigger_build_pmtiles_job = fake_ping
    archive_intake.iter_zip_members = spy_iter
    drain_mod._ingest_member_bytes = spy_ingest

    db = SessionLocal()
    try:
        user = User(email=f"real-import-{uuid.uuid4().hex[:8]}@example.com", username=f"ri_{uuid.uuid4().hex[:6]}")
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()

    def drop() -> str:
        bucket_key = f"{archive_intake.ARCHIVE_INTAKE_PREFIX}/{user_id}/{uuid.uuid4()}.zip"
        path = archive_intake.local_archive_path(bucket_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(ARCHIVE, path)  # no copy of a multi-GB export
        db = SessionLocal()
        try:
            consent_id = archive_intake.record_consent(
                db, user_id=user_id, consent_version="e2e-import-real",
                consent_text="local e2e-import-real run", locale="fr",
            )
            row = PendingArchive(
                user_id=user_id, consent_id=consent_id, source=archive_intake.ARCHIVE_PROVENANCE,
                storage_backend="local", bucket_key=bucket_key,
                fallback_sport=archive_intake.NO_SPORT_HINT, contribute_heatmap=True, status="uploaded",
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def run(label: str) -> None:
        emails.clear()
        pings.clear()
        reasons.clear()
        errors.clear()
        member_s.clear()
        archive_id = drop()
        t0 = time.monotonic()
        summary = drain_mod.drain_pending_archives(limit=1, pace_seconds=0.0)
        wall = time.monotonic() - t0
        db = SessionLocal()
        try:
            row = db.execute(sa_text(
                "SELECT status, members_total, imported, skipped, failed, last_error "
                "FROM pending_archives WHERE id = :id"), {"id": archive_id}).mappings().one()
            sports = db.execute(sa_text(
                "SELECT sport, count(*), count(*) FILTER (WHERE display_geojson IS NULL) "
                "FROM activities WHERE user_id = :u GROUP BY sport ORDER BY 2 DESC"), {"u": user_id}).all()
            routes = db.execute(sa_text(
                "SELECT count(*) FROM activities WHERE user_id = :u AND activity_date IS NULL"),
                {"u": user_id}).scalar()
        finally:
            db.close()
        n = len(member_s)
        print(f"\n══ {label} ══")
        print(f"archive   : {row['status']}  members={row['members_total']}  imported={row['imported']}  "
              f"skipped={row['skipped']}  failed={row['failed']}  error={row['last_error']}")
        print(f"drain     : {summary}")
        print(f"skipped by reason: {dict(reasons)}")
        if errors:
            print("failures  :")
            for msg, c in errors.most_common(10):
                print(f"   {c:5d}  {msg}")
        print("activities (user total) by sport [missing display_geojson]:")
        for sport, c, nodisp in sports:
            print(f"   {sport:10s} {c:6d}  [{nodisp}]")
        print(f"undated activities (route smell): {routes}")
        print(f"rebuild pings: {len(pings)}")
        for subject, html in emails:
            import re
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
            print(f"email     : {subject}\n            {text[:400]}")
        avg = (sum(member_s) / n * 1000) if n else 0.0
        print(f"timing    : wall {wall:.1f}s, {n} parsed members, avg {avg:.0f} ms/member, "
              f"max {max(member_s, default=0) * 1000:.0f} ms")
        print(f"peak RSS  : {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MiB")

    size = os.path.getsize(ARCHIVE)
    print(f"archive file: {size / 1e6:.1f} MB")
    run("first drop")
    if os.environ.get("REDROP") == "1":
        run("second drop (idempotence: expect imported=0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
