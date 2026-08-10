"""Reclassify already-imported Strava-export activities' sport from the
CSV **Activity Name** (the rich signal), not the coarse Activity Type.

Strava bulk-export `activities.csv` "Activity Type" is almost all "Ride"
(→ road) even for gravel/MTB rides, and Activity Gear is blank. But the
**Activity Name** carries the real type ("Gravel paradise", "VTT vers
Lacan", ...): ~165 gravel + ~60 vtt/mtb in Paul's export. The importer
stores the GPX internal <name>, NOT the CSV Activity Name, so this must
read the CSV and key on Activity ID → activities.provider_activity_id.

Only refines CYCLING rides currently classified 'road' → gravel/mtb;
never touches running/other. Run AFTER import, BEFORE rebuild_heatmap:

    docker compose run --rm --no-deps -v "$PWD/data:/app/data:ro" \
        backend python -m scripts.reclassify_strava_sport /app/data/strava-export

Then: make heatmap-rebuild (regenerates heat_edges with the new sports).
"""
import csv
import os
import sys


def classify_from_name(name: str | None) -> str | None:
    """gravel/mtb from an activity name, else None (keep current sport)."""
    if not name:
        return None
    n = name.lower()
    if "gravel" in n:
        return "gravel"
    if "vtt" in n or "mtb" in n or "mountain bike" in n or "mountain" in n:
        return "mtb"
    return None


def main() -> None:
    export_dir = sys.argv[1] if len(sys.argv) > 1 else "/app/data/strava-export"
    csv_path = os.path.join(export_dir, "activities.csv")
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found")
        sys.exit(1)

    # filename-stem -> refined sport (only when the name says gravel/mtb).
    # MUST key on the CSV "Filename" stem, NOT "Activity ID": for many files
    # (esp. .fit) the Activity ID differs from the filename, and the importer
    # stores provider_activity_id = filename stem (filename.split('.')[0]).
    refine: dict[str, str] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            fn = (row.get("Filename") or "").strip()
            new = classify_from_name(row.get("Activity Name"))
            if fn and new:
                refine[os.path.basename(fn).split(".")[0]] = new

    print(f"CSV name-classified: {sum(1 for v in refine.values() if v=='gravel')} gravel, "
          f"{sum(1 for v in refine.values() if v=='mtb')} mtb candidates")

    from sqlalchemy import text

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        changed = {"gravel": 0, "mtb": 0}
        for aid, new in refine.items():
            # Only refine cycling rides currently 'road' (don't touch running/other).
            res = db.execute(text(
                "UPDATE activities SET sport=:s "
                "WHERE provider_activity_id=:aid AND sport='road'"
            ), {"s": new, "aid": aid})
            if res.rowcount:
                changed[new] += res.rowcount
        db.commit()
        print(f"Reclassified: {changed['gravel']} → gravel, {changed['mtb']} → mtb")
        rows = db.execute(text(
            "SELECT sport, count(*) FROM activities WHERE contribute_heatmap "
            "GROUP BY sport ORDER BY 2 DESC"
        )).fetchall()
        print("Cycling activities by sport now:", {r[0]: r[1] for r in rows})
    finally:
        db.close()


if __name__ == "__main__":
    main()
