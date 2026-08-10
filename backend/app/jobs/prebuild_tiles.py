"""Pre-generate heatmap MVT tiles as static files.

Generates tiles for z8-14 covering France, writes gzipped MVT to disk.
Serve with nginx or any static file server for zero-computation tile delivery.

Run:
    python -m app.jobs.prebuild_tiles
    python -m app.jobs.prebuild_tiles --sport offroad --min-zoom 10 --max-zoom 14
    python -m app.jobs.prebuild_tiles --bbox "3.5,43.4,4.1,43.8" --sport offroad

Output: /tmp/heatmap-tiles/{sport}/{z}/{x}/{y}.mvt.gz
"""
import argparse
import logging
import math
import os
import time

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

FRANCE_BBOX = (-5.5, 41.3, 9.6, 51.1)


def tiles_in_bbox(bbox: tuple, z: int) -> list[tuple[int, int]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    n = 2 ** z
    x_min = int((min_lon + 180) / 360 * n)
    x_max = int((max_lon + 180) / 360 * n)
    y_min = int((1 - math.log(math.tan(math.radians(min(85, max_lat))) + 1 / math.cos(math.radians(min(85, max_lat)))) / math.pi) / 2 * n)
    y_max = int((1 - math.log(math.tan(math.radians(max(-85, min_lat))) + 1 / math.cos(math.radians(max(-85, min_lat)))) / math.pi) / 2 * n)
    return [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]


def main(sports: list[str], min_zoom: int, max_zoom: int, bbox: tuple, output_dir: str) -> None:
    from app.api.heatmap import _generate_tile

    os.makedirs(output_dir, exist_ok=True)
    total = 0
    skipped = 0
    t0 = time.time()

    for z in range(min_zoom, max_zoom + 1):
        tiles = tiles_in_bbox(bbox, z)
        log.info("z%d: %d tiles x %d sports", z, len(tiles), len(sports))
        if len(tiles) > 50000:
            log.warning("z%d skipped (%d tiles too many)", z, len(tiles))
            continue

        for sport in sports:
            for x, y in tiles:
                path = os.path.join(output_dir, sport, str(z), str(x))
                file_path = os.path.join(path, f"{y}.mvt.gz")
                if os.path.exists(file_path):
                    skipped += 1
                    continue
                try:
                    gz = _generate_tile(sport, z, x, y)
                    if gz and len(gz) > 20:
                        os.makedirs(path, exist_ok=True)
                        with open(file_path, "wb") as f:
                            f.write(gz)
                        total += 1
                except Exception as e:
                    log.warning("Failed %s/%d/%d/%d: %s", sport, z, x, y, e)

                if (total + skipped) % 200 == 0 and (total + skipped) > 0:
                    elapsed = time.time() - t0
                    log.info("  %d generated, %d skipped (%.0f tiles/s)", total, skipped, (total + skipped) / elapsed)

    elapsed = time.time() - t0
    log.info("Done: %d tiles in %.0fs", total, elapsed)

    total_size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(output_dir) for f in fs)
    log.info("Output: %s (%.1f MB)", output_dir, total_size / 1024 / 1024)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-generate heatmap MVT tiles")
    parser.add_argument("--sport", default="offroad,road,gravel,mtb")
    parser.add_argument("--min-zoom", type=int, default=8)
    parser.add_argument("--max-zoom", type=int, default=14)
    parser.add_argument("--bbox", type=str, default=None, help="lon_min,lat_min,lon_max,lat_max")
    parser.add_argument("--output", default="/tmp/heatmap-tiles")
    args = parser.parse_args()

    sports = [s.strip() for s in args.sport.split(",")]
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else FRANCE_BBOX
    main(sports, args.min_zoom, args.max_zoom, bbox, args.output)
