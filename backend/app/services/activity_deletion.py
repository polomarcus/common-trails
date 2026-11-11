"""GDPR activity deletion — remove one activity and take it off the community map.

1. DELETE the activity row — ``activity_cells`` cascades at the DB level
   (FK ``ON DELETE CASCADE``).
2. Fire the ``build-pmtiles`` rebuild (best-effort) so the deleted trace
   actually LEAVES the static ``heatmap-display.pmtiles`` on GCS. The build
   reads consented ``activities`` live, so a deleted activity drops out on the
   next rebuild — but there is NO periodic rebuild scheduler, so without this
   trigger the deletion would stay VISIBLE on the public map indefinitely
   (until an unrelated contribution happened to fire a rebuild). That is a GDPR
   exposure, so the removal path must trigger just like the addition paths do.

The former matched-pipeline tables (``heat_edges`` / ``heat_edge_contributors``
/ ``heat_cells``) are no longer read or written by any code; they are dropped
by the schema clean-up that follows the pipeline removal.
"""
import logging

from sqlalchemy.orm import Session

from app.db.models import Activity

logger = logging.getLogger(__name__)


def delete_user_activity(
    db: Session, user_id: str, activity_id: str, *, trigger_rebuild: bool = True,
) -> bool:
    """Delete ``activity_id`` if it belongs to ``user_id``.

    ``trigger_rebuild=False`` skips the per-deletion build-pmtiles ping (step
    2) — ONLY for a batch caller that fires ONE ping itself after its last
    deletion (e.g. ``app.cli.purge_strava_route_activities``), so N deletions
    don't start N Cloud Run builds.

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

    db.delete(act)
    db.commit()

    # Reflect the removal on the community map (step 2). Fire it best-effort:
    # never raises, and it is an env-gated no-op locally / in TEST_MODE (see
    # run_jobs._is_enabled). A missed ping is the same residual risk the
    # addition paths carry.
    if trigger_rebuild:
        try:
            from app.services.run_jobs import trigger_build_pmtiles_job

            trigger_build_pmtiles_job()
        except Exception:  # pragma: no cover - defense in depth
            logger.warning("build-pmtiles trigger failed after activity deletion", exc_info=True)

    logger.info("activity deleted: id=%s user=%s", activity_id, user_id)
    return True
