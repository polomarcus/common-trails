"""Move to ``offroad`` the default-sport rides that run mostly off pavement.

See ``app.services.surface_inference`` for the why and the signal. Dry run by
default — nothing changes, the counts are printed per (anonymised) account::

    python -m app.cli.infer_offroad                 # dry run, every account
    python -m app.cli.infer_offroad --apply         # write + request a rebuild

One-shot by design: upload the tiles (``app.cli.build_osm_surface_tiles``),
run this, then delete the tiles — imports do not infer anything themselves.

``--threshold`` overrides ``OFFROAD_UNPAVED_SHARE`` (default 0.15);
``--url`` overrides where the OSM surface tiles are read.
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from app.services import surface_inference as si

log = logging.getLogger("infer_offroad")


def run(
    *, apply: bool, user_id: str | None = None, threshold: float | None = None, url: str | None = None
) -> si.InferenceReport:
    index = si.load_index(url)
    if index is None:
        raise SystemExit("no OSM surface tiles configured/found — nothing to do")
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rep = si.infer_offroad(db, index, user_id=user_id, apply=apply, min_share=threshold)
        if apply:
            db.commit()
    finally:
        db.close()

    cut = si.threshold() if threshold is None else threshold
    # Accounts are labelled by volume (U1 = most default rides), never by id:
    # this output lands in shared job logs.
    order = sorted(rep.per_user, key=lambda u: -sum(rep.per_user[u].values()))
    for i, uid in enumerate(order, 1):
        c: Counter = rep.per_user[uid]
        log.info(
            "U%d: %d default rides → offroad=%d road=%d unmeasured=%d",
            i,
            sum(c.values()),
            c["offroad"],
            c["road"],
            c["unmeasured"],
        )
    log.info(
        "%s threshold=%.2f examined=%d offroad=%d kept_road=%d unmeasured=%d changed=%d tiles=%d",
        "APPLY" if apply else "[dry-run]",
        cut,
        rep.examined,
        rep.offroad,
        rep.kept_road,
        rep.unmeasured,
        len(rep.changed_ids or []),
        len(index.tiles),
    )
    if apply and rep.changed_ids:
        from app.services.activity_sport import _request_community_rebuild

        _request_community_rebuild()
    return rep


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run).")
    parser.add_argument("--user-id", default=None, help="Only this account.")
    parser.add_argument("--threshold", type=float, default=None, help="Unpaved share cut (0–1).")
    parser.add_argument("--url", default=None, help="OSM surface tiles (gs://… or a directory).")
    args = parser.parse_args()
    run(apply=args.apply, user_id=args.user_id, threshold=args.threshold, url=args.url)
