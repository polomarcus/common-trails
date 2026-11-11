"""Flag activities whose sport is only a generic default.

A Strava export's ``activities.csv`` calls most bike rides just "Ride": the
importer files them under ``road`` unless the name says gravel / VTT. Many are
in fact gravel or MTB rides and pollute the road layer of the community
heatmap. ``activities.sport_is_default`` records "nobody actually said road"
so the rider can be asked to classify those rides (and do it in bulk).

* New rows: set at ingest by the sport cascade
  (``strava_utils.resolve_sport_cascade_with_default``).
* Existing rows — conservative backfill. An explicit pick is never a default,
  and loose files posted to ``/imports/files`` WITH the sport the rider chose
  look exactly like archive members on the row itself (same ``source``,
  ``provider`` and ``geometry_source``), so the row alone cannot tell them
  apart. TRUE is therefore set only for ``sport = 'road'`` file activities
  (``source = 'manual_upload'``, ``provider = 'file'``) that are TIED TO AN
  ARCHIVE:

  - whole-archive drain (``pending_archives``): the owner has a ``done``
    archive that imported something, and ``activities.created_at`` falls
    inside that archive's life — from its ``created_at`` (``/init``, before
    the upload) to its ``updated_at`` (the terminal transition, stamped by the
    drain) + 1 minute of clock margin. Archives whose life exceeds 48 h
    (requeued long after, stuck) are ignored: a wide window would catch
    unrelated uploads;
  - legacy per-member queue (``pending_archive_files``): the exact
    ``activity_id`` link, when the queued activities.csv sport was ``road``.

  What this can still get wrong: a loose file the rider uploaded with "Route"
  picked WHILE their own archive was being uploaded / drained (flagged by
  mistake — one click confirms it), and archives outside the 48 h rule (not
  flagged). No stored column says whether a past ``road`` came from a generic
  "Ride" or from a FIT ``sub_sport = road``; both are flagged.
  ``sport`` itself is never touched.

"Ma heatmap" files: the column is NOT in the staleness trigger's watched list
(``my_heatmap_bump_upd``, see 0070), so the backfill bumps no ``data_rev`` and
no per-user file is rebuilt. The statement-level trigger would still fire once
on the backfill UPDATE and compare every watched column of every touched row
(detoasting ``display_geojson``) only to conclude nothing changed — it is
disabled for the duration of the backfill, inside this transaction, which
already holds the table lock taken by ADD COLUMN.

Revision ID: 0071
Revises: 0070
"""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

_TRIGGER = "activities_my_heatmap_upd"

BACKFILL_WHERE = """
    a.sport = 'road' AND a.source = 'manual_upload' AND a.provider = 'file'
    AND (
      EXISTS (
        SELECT 1 FROM pending_archives pa
         WHERE pa.user_id = a.user_id
           AND pa.status = 'done'
           AND pa.imported > 0
           AND pa.updated_at - pa.created_at <= interval '48 hours'
           AND a.created_at >= pa.created_at
           AND a.created_at <= pa.updated_at + interval '1 minute'
      )
      OR EXISTS (
        SELECT 1 FROM pending_archive_files f
         WHERE f.activity_id = a.id AND f.status = 'done' AND f.sport = 'road'
      )
    )
"""


def _set_update_trigger(enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_trigger
             WHERE tgname = '{_TRIGGER}' AND tgrelid = 'activities'::regclass
          ) THEN
            ALTER TABLE activities {action} TRIGGER {_TRIGGER};
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # ADD COLUMN takes an ACCESS EXCLUSIVE lock: instant once granted (a
    # constant default is not a table rewrite), but give up after 5 s rather
    # than queue every query behind a long read — the entrypoint retries.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS sport_is_default "
        "boolean NOT NULL DEFAULT false"
    )
    _set_update_trigger(False)
    op.execute(f"UPDATE activities a SET sport_is_default = true WHERE {BACKFILL_WHERE}")
    _set_update_trigger(True)
    # Count + list of a rider's default-sport activities (GET
    # /me/activities/default-sport, on every /map visit): tiny partial index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activities_user_sport_default "
        "ON activities (user_id) WHERE sport_is_default"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP INDEX IF EXISTS ix_activities_user_sport_default")
    op.execute("ALTER TABLE activities DROP COLUMN IF EXISTS sport_is_default")
