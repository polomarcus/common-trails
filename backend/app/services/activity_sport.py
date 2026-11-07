"""Correct the sport of a user's OWN activities after import.

A wrong sport at import (e.g. MTB rides classified as road) is not cosmetic:
the community heatmap is split by sport, so the ride pollutes the wrong layer.
This service lets the owner fix it, for one activity or a batch.

What a change touches:

1. ``activities.sport`` — the source of truth. In RAW display mode (prod since
   the 2026-07-29 pivot) the PMTiles build reads ``activities.sport`` live, so
   this is all the community layer needs once rebuilt.
2. MATCHED mode only (legacy ``HEATMAP_DISPLAY_SOURCE != raw``): the activity's
   ``heat_edge_contributors`` rows live under edge keys that embed the OLD sport
   (``_edge_key``), i.e. in the old ``heat_edges`` partition. They are purged
   (same recompute as GDPR deletion, ``purge_activity_heat``) and the trace is
   re-written under the NEW sport with ``_update_heat_edges`` — then ONE
   deduplicated ``heat_edges_agg`` refresh. Community-eligible rows only (the
   only ones that ever wrote heat).
3. Fires the debounced ``build-pmtiles`` rebuild (the same ping
   ``/imports/files`` uses) when at least one changed row is community-eligible
   — personal-only rows never reach the static display. Debounced because a
   user looping PATCH must not fire N expensive rebuild jobs.

Derived caches deliberately NOT touched: ``display_coords`` (fingerprint =
max-span / bbox / version) and ``display_geojson`` are pure functions of the
geometry, never of the sport. The legacy ``heat_cells`` layer has no
per-activity attribution (same limitation as deletion — see
``activity_deletion``). ``/me/stats`` is computed live in SQL (no cache).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Activity
from app.services.activity_deletion import purge_activity_heat
from app.services.provenance import is_community_eligible
from app.services.raw_trace_display import raw_display_enabled

logger = logging.getLogger(__name__)


def _community_eligible(act: Activity) -> bool:
    """Python mirror of ``provenance.community_eligible_conditions``."""
    return is_community_eligible(
        source=act.source,
        contribute_heatmap=act.contribute_heatmap,
        has_geometry=bool(act.geometry_geojson),
        activity_date=act.activity_date,
    )


def change_activities_sport(
    db: Session, user_id: str, activity_ids: list[str], sport: str
) -> tuple[list[str], list[str]]:
    """Set ``sport`` on the activities of ``activity_ids`` OWNED by ``user_id``.

    Returns ``(found_ids, changed_ids)``: ``found_ids`` are the owned rows that
    exist (an id belonging to someone else is indistinguishable from a missing
    one — no existence leak); ``changed_ids`` is the subset whose sport actually
    differed (an idempotent re-PATCH changes nothing and triggers nothing).
    ``sport`` must already be validated against ``EDITABLE_SPORTS``.
    """
    if not activity_ids:
        return [], []
    rows = db.query(Activity).filter(Activity.user_id == user_id, Activity.id.in_(activity_ids)).all()
    found_ids = [a.id for a in rows]
    changed = [a for a in rows if a.sport != sport]
    if not changed:
        return found_ids, []

    matched_mode = not raw_display_enabled()
    touched_ways: set[int] = set()
    to_rewrite: list[tuple[str, str, datetime | None]] = []
    any_community = False
    for act in changed:
        old = act.sport
        act.sport = sport
        eligible = _community_eligible(act)
        any_community = any_community or eligible
        if matched_mode and eligible:
            touched_ways.update(purge_activity_heat(db, act.id))
            to_rewrite.append((act.id, act.geometry_geojson, act.activity_date))
        logger.info("activity sport changed: id=%s user=%s %s -> %s", act.id, user_id, old, sport)
    changed_ids = [a.id for a in changed]
    db.commit()

    if to_rewrite:
        # Re-write the heat contributions under the NEW sport partition. Each
        # call opens its own session + commits; the agg refresh is deferred
        # into ONE deduplicated pass (archive-drain batching pattern).
        from app.services.ingest import _maintain_heat_agg, _update_heat_edges

        for aid, geojson, act_date in to_rewrite:
            try:
                _update_heat_edges(
                    user_id,
                    sport,
                    geojson,
                    activity_date=act_date or datetime.now(UTC),
                    activity_id=aid,
                    collect_touched_ways=touched_ways,
                )
            except Exception:  # noqa: BLE001 — sport change already committed
                logger.warning("heat re-write failed after sport change: id=%s", aid, exc_info=True)
        if touched_ways:
            _maintain_heat_agg(db, sorted(touched_ways))

    if any_community:
        try:
            from app.services.run_jobs import (
                build_pmtiles_debounce_ok,
                trigger_build_pmtiles_job,
            )

            if build_pmtiles_debounce_ok():
                trigger_build_pmtiles_job()
            else:
                logger.info("build-pmtiles trigger debounced after sport change")
        except Exception:  # pragma: no cover - defense in depth
            logger.warning("build-pmtiles trigger failed after sport change", exc_info=True)

    return found_ids, changed_ids
