"""Migration 0070 — the database leftovers of the retired Strava API sync are gone.

The suite runs against a database at ``alembic upgrade head``: the three
tables, the enum type and ``activities.total_photo_count`` must be absent,
what has to be KEPT must still be there, and the "Ma heatmap" staleness
trigger must no longer read the dropped column (an UPDATE on ``activities``
would otherwise fail in production).
"""
import uuid

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sa_text

from app.db.models import Base
from app.db.session import SessionLocal, engine

DROPPED_TABLES = ("integration_accounts", "import_jobs", "activity_photos")


def test_strava_api_tables_enum_and_column_are_dropped():
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    for table in DROPPED_TABLES:
        assert table not in tables, f"{table} should have been dropped by 0070"
        assert table not in Base.metadata.tables, f"{table} still has an ORM model"

    columns = {c["name"] for c in insp.get_columns("activities")}
    assert "total_photo_count" not in columns
    # Kept: activities imported through the API still exist and display.
    assert {"provider", "source", "provider_activity_id", "geometry_source"} <= columns

    with engine.connect() as conn:
        assert conn.execute(
            sa_text("SELECT count(*) FROM pg_type WHERE typname = 'import_job_status_enum'")
        ).scalar() == 0
        trigger_src = conn.execute(
            sa_text("SELECT prosrc FROM pg_proc WHERE proname = 'my_heatmap_bump_upd'")
        ).scalar()
    assert trigger_src and "total_photo_count" not in trigger_src


def test_update_on_activities_still_works_with_triggers_active():
    """The staleness trigger body only runs once the feature is activated:
    switch it on inside a transaction, UPDATE an activity, roll everything
    back. Fails with "column o.total_photo_count does not exist" if the
    trigger function was not re-created before the column was dropped."""
    db = SessionLocal()
    aid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        db.execute(sa_text("UPDATE my_heatmap_config SET enabled = true WHERE id = 1"))
        db.execute(
            sa_text(
                "INSERT INTO activities (id, user_id, provider, sport, name) "
                "VALUES (:id, :uid, 'strava', 'road', 'before')"
            ),
            {"id": aid, "uid": uid},
        )
        db.execute(sa_text("UPDATE activities SET name = 'after' WHERE id = :id"), {"id": aid})
        rev = db.execute(
            sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :uid"), {"uid": uid}
        ).scalar()
        assert rev == 2  # 1 on INSERT, +1 on the displayed-column UPDATE
    finally:
        db.rollback()
        db.close()
