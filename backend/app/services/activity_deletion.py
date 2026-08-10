"""GDPR activity deletion — remove one activity AND its community heat contributions.

Per-activity heat attribution EXISTS (case A): since migration 0052 the
``heat_edge_contributors`` PK is ``(edge_key, user_id_hash, activity_id)``,
so the exact set of heat edges one activity contributed to is known.
Deletion is a recompute-from-source, mirroring the self-healing philosophy
of ``heat_edges_agg`` (#441):

1. DELETE this activity's contributor rows (``RETURNING edge_key``).
2. Recount ``pass_count`` / ``user_count`` on the touched ``heat_edges``
   from the REMAINING contributor rows (``pass_count`` == contributor-row
   count by construction since 0052; ``user_count`` == distinct hashes).
   ``forward_count`` / ``backward_count`` cannot be recomputed — direction
   is not stored per contributor — so they are clamped to the new
   ``pass_count`` (documented approximation).
3. DELETE ``heat_edges`` rows left with zero contributors.
4. DELETE the activity row — ``activity_cells`` + ``activity_photos``
   cascade at the DB level (FK ``ON DELETE CASCADE``).
5. Incrementally refresh ``heat_edges_agg`` for the touched
   ``osm_way_id``s via the existing ``recompute_heat_agg_for_ways`` hook
   (best-effort, never fails the deletion) — the live MVT fallback
   reflects the removal immediately.

6. Fire the ``build-pmtiles`` rebuild (best-effort) so the deleted trace
   actually LEAVES the static ``heatmap-display.pmtiles`` on GCS. In raw
   display mode the build reads consented ``activities`` live, so a deleted
   activity drops out on the next rebuild — but there is NO periodic rebuild
   scheduler, so without this trigger the deletion would stay VISIBLE on the
   public map indefinitely (until an unrelated contribution happened to fire a
   rebuild). That is a GDPR exposure, so the removal path must trigger just
   like the addition paths do.

Deliberately NOT done here:

* ``heat_cells`` / ``heat_cell_contributors`` (legacy cell layer, not part
  of the current display pipeline) have NO per-activity attribution (PK is
  ``cell_key, sport, user_id_hash``) — a user's cell contributions are only
  removed by a full rebuild or a full account deletion. See
  ``docs/gdpr-deletion-runbook.md``.
"""
import logging

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db.models import Activity

logger = logging.getLogger(__name__)


def _relation_exists(db: Session, name: str) -> bool:
    """True when a base table ``name`` exists in the connected database.

    Under the raw-trace pivot (2026-07-29) the community heat tables
    (``heat_edges`` / ``heat_edge_contributors``) are being dropped in prod —
    the display no longer reads them. GDPR deletion must still delete the
    activity (+ its FK-cascaded ``activity_cells`` / ``activity_photos``) even
    when the heat cleanup has no table to clean.
    """
    return bool(
        db.execute(
            sa_text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :n)"
            ),
            {"n": name},
        ).scalar()
    )


def _recompute_touched_heat_edges(db: Session, edge_keys: list[str]) -> list[int]:
    """Recompute ``pass_count`` / ``user_count`` on ``edge_keys`` from the
    REMAINING ``heat_edge_contributors`` rows, then drop edges with zero
    contributors. Returns the touched ``osm_way_id``s (for a later agg
    refresh). Does NOT commit; a no-op when the heat tables are absent
    (raw-trace pivot).

    Shared by per-activity GDPR deletion (after removing an activity's
    contributor rows) AND by account merge (after rewriting a merged
    account's ``user_id_hash`` — same-person rows must collapse to one
    distinct hash so ``user_count`` doesn't over-count a K-anonymity user).
    """
    if not edge_keys or not _relation_exists(db, "heat_edges"):
        return []
    # Capture touched ways BEFORE any heat_edges row disappears, so the agg
    # recompute also covers ways whose last edge is deleted below
    # (self-healing: zero remaining rows → stale agg row removed).
    way_ids = [
        row[0]
        for row in db.execute(
            sa_text(
                "SELECT DISTINCT osm_way_id FROM heat_edges "
                "WHERE edge_key = ANY(:keys) AND osm_way_id IS NOT NULL"
            ),
            {"keys": edge_keys},
        )
    ]

    db.execute(
        sa_text("""
            WITH remaining AS (
                SELECT edge_key,
                       COUNT(*) AS pc,
                       COUNT(DISTINCT user_id_hash) AS uc
                FROM heat_edge_contributors
                WHERE edge_key = ANY(:keys)
                GROUP BY edge_key
            )
            UPDATE heat_edges he
            SET pass_count = r.pc,
                user_count = r.uc,
                forward_count = LEAST(he.forward_count, r.pc),
                backward_count = LEAST(he.backward_count, r.pc)
            FROM remaining r
            WHERE he.edge_key = r.edge_key
        """),
        {"keys": edge_keys},
    )

    db.execute(
        sa_text("""
            DELETE FROM heat_edges
            WHERE edge_key = ANY(:keys)
              AND NOT EXISTS (
                  SELECT 1 FROM heat_edge_contributors c
                  WHERE c.edge_key = heat_edges.edge_key
              )
        """),
        {"keys": edge_keys},
    )
    return way_ids


def purge_activity_heat(db: Session, activity_id: str) -> list[int]:
    """Remove one activity's ``heat_edge_contributors`` rows and recompute the
    touched ``heat_edges``. Returns touched ``osm_way_id``s for a later agg
    refresh. Does NOT commit; safe (returns ``[]``) when the community heat
    tables are gone (raw-trace pivot).

    Extracted from ``delete_user_activity`` so the SAME community-heat cleanup
    runs both on GDPR per-activity deletion AND when an account merge drops a
    duplicate ride — a merged activity must never leak orphan heat rows.
    """
    edge_keys: list[str] = []
    if _relation_exists(db, "heat_edge_contributors"):
        touched = db.execute(
            sa_text(
                "DELETE FROM heat_edge_contributors WHERE activity_id = :aid "
                "RETURNING edge_key"
            ),
            {"aid": activity_id},
        ).fetchall()
        edge_keys = list({row[0] for row in touched})
    return _recompute_touched_heat_edges(db, edge_keys)


def delete_user_activity(db: Session, user_id: str, activity_id: str) -> bool:
    """Delete ``activity_id`` if it belongs to ``user_id``.

    Returns False when the activity doesn't exist OR belongs to another
    user (the caller answers 404 either way — no existence leak).
    """
    act = (
        db.query(Activity)
        .filter(Activity.id == activity_id, Activity.user_id == user_id)
        .first()
    )
    if act is None:
        return False

    # Community heat cleanup — SKIPPED when the tables are gone (raw-trace
    # pivot). The activity + its cascaded private rows are still deleted below.
    way_ids = purge_activity_heat(db, activity_id)

    db.delete(act)
    db.commit()

    if way_ids:
        # Best-effort + commits on its own; a failure here never undoes the
        # deletion (drift check / next backfill are the safety net).
        from app.services.ingest import _maintain_heat_agg

        _maintain_heat_agg(db, way_ids)

    # Reflect the removal on the community map (step 6). The static PMTiles
    # reads consented activities live in raw mode, so the deleted trace only
    # leaves the map on the next build-pmtiles run — and there is NO periodic
    # rebuild scheduler, so an untriggered deletion would stay VISIBLE
    # indefinitely (GDPR exposure). Fire it best-effort: never raises, and it is
    # an env-gated no-op locally / in TEST_MODE (see run_jobs._is_enabled). A
    # missed ping is the same residual risk the addition paths carry.
    try:
        from app.services.run_jobs import trigger_build_pmtiles_job

        trigger_build_pmtiles_job()
    except Exception:  # pragma: no cover - defense in depth
        logger.warning("build-pmtiles trigger failed after activity deletion", exc_info=True)

    logger.info(
        "activity deleted: id=%s user=%s ways_recomputed=%d",
        activity_id, user_id, len(way_ids),
    )
    return True
