"""Download SRTM/Copernicus HGT tiles for a bounding box.

Pulls 3-arcsecond HGT files from viewfinderpanoramas.org (consolidated,
void-filled, no auth, no API key). Tiles end up in ``DEM_DIR`` (default
``${DATA_DIR}/dem``) where ``app.services.local_dem`` expects them.

## Viewfinder layout

Viewfinder distributes HGT in **per-sheet** zips, not per-tile. Each
sheet covers a 4° lat × 6° lon block (24 1°-tiles per sheet). URL:

    https://viewfinderpanoramas.org/dem3/<SHEET>.zip

Sheet naming:
- Latitude band: a single letter. The relevant rows for our use case:
  - F = 16-20°N,  G = 20-24°N,  H = 24-28°N,  I = 28-32°N
  - J = 32-36°N,  K = 36-40°N,  L = 40-44°N,  M = 44-48°N
  - N = 48-52°N,  O = 52-56°N,  P = 56-60°N,  Q = 60-64°N
  Southern hemisphere uses lowercase (a..z mirroring); we don't cover
  that band so it's not encoded here.
- Longitude zone: 1-60, where zone N spans lon `[6(N-31), 6(N-30))`.
  Zone 30 covers -6° to 0°; zone 31 covers 0° to 6°; zone 32 covers
  6° to 12°. So France lies in zones 30-32, Spain in 29-31, Italy in
  31-33, Switzerland in 31-32.

For France (lat 41-52, lon -5 to 10) the sheets are:
``L30, L31, M30, M31, M32, N30, N31, N32`` — about 8 sheets,
~400-500 MB total. Each zip is fetched once and decompressed; the
HGT files inside are kept, the zip is discarded.

## Usage

    python -m app.cli.download_dem --bbox 41,-5,52,10        # France
    python -m app.cli.download_dem --region france
    python -m app.cli.download_dem --bbox 41,-5,52,10 --dry-run

## Caching

If all HGT tiles within a sheet's range already exist in DEM_DIR, the
sheet zip is skipped. Otherwise the zip is fetched and only the
missing HGTs are written; existing files are left alone.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import string
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


_VIEWFINDER_BASE = "https://viewfinderpanoramas.org/dem3"

# Approximate bboxes for known import regions — keep aligned with
# REGIONS in import_osm_roads.py but quantised to whole degrees with
# 1° padding so tile coverage is generous at the edges.
REGION_BBOXES: dict[str, tuple[int, int, int, int]] = {
    # (lat_min, lon_min, lat_max, lon_max)
    "france": (41, -6, 52, 10),
    "occitanie": (42, -1, 45, 5),
    "paca": (42, 4, 46, 8),
    "ara": (43, 3, 47, 8),
    "cataluna": (40, 0, 43, 4),
    "italia-nord-ovest": (43, 6, 47, 12),  # widened east to cover Lombardia past Brescia (up to ~lon 11.4)
    "switzerland": (45, 5, 48, 11),
}


def _tile_name(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}.hgt"


def _sheet_for(lat_floor: int, lon_floor: int) -> str:
    """Return the viewfinder sheet name covering a 1° tile.

    Viewfinder's northern-hemisphere sheets use letters where A is the
    equatorial belt (0-4°N), B is 4-8°N, …, K is 40-44°N (Northern Spain
    / Southern France), L is 44-48°N (Northern France), M is 48-52°N
    (UK / Northern Germany). Empirically: ``row_index = lat // 4``.

    Longitude zone runs 1-60: zone N covers ``[-180 + 6*(N-1), -180 +
    6*N)``. Zone 31 = 0..6°E, zone 30 = -6..0°E, zone 32 = 6..12°E.
    """
    if lat_floor < 0:
        # Southern hemisphere not in scope for the European beta;
        # let it raise rather than emit a wrong URL.
        raise ValueError(f"southern hemisphere not supported: lat={lat_floor}")
    row_index = lat_floor // 4
    if row_index >= 26:
        raise ValueError(f"latitude beyond northern viewfinder grid: lat={lat_floor}")
    letter = string.ascii_uppercase[row_index]
    zone = (lon_floor + 180) // 6 + 1
    return f"{letter}{zone:02d}"


def _sheets_for_bbox(bbox: tuple[int, int, int, int]) -> set[str]:
    """Enumerate all viewfinder sheets that intersect the bbox."""
    lat_min, lon_min, lat_max, lon_max = bbox
    sheets: set[str] = set()
    for lat in range(lat_min, lat_max):
        for lon in range(lon_min, lon_max):
            sheets.add(_sheet_for(lat, lon))
    return sheets


def _tiles_for_bbox(bbox: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    lat_min, lon_min, lat_max, lon_max = bbox
    return [(lat, lon) for lat in range(lat_min, lat_max) for lon in range(lon_min, lon_max)]


def _download_sheet(sheet: str, wanted_tiles: set[str], dem_dir: Path) -> int:
    """Fetch one viewfinder sheet zip and extract the wanted HGTs.

    Returns the number of HGT files written from this sheet. If every
    wanted tile already exists on disk the request is skipped.
    """
    missing = {t for t in wanted_tiles if not (dem_dir / t).exists()}
    if not missing:
        logger.info("sheet %s: all %d tiles already present, skipping", sheet, len(wanted_tiles))
        return 0

    url = f"{_VIEWFINDER_BASE}/{sheet}.zip"
    logger.info("GET %s (need %d of %d tiles)", url, len(missing), len(wanted_tiles))
    try:
        with urllib.request.urlopen(url, timeout=300) as resp:
            data = resp.read()
    except Exception as exc:
        logger.warning("sheet %s not available: %s", sheet, exc)
        return 0

    written = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name
                if name in missing:
                    target = dem_dir / name
                    with zf.open(info) as src, target.open("wb") as dst:
                        dst.write(src.read())
                    size_kb = target.stat().st_size / 1024
                    logger.info("  wrote %s (%.0f KB)", name, size_kb)
                    written += 1
    except zipfile.BadZipFile:
        logger.warning("sheet %s: bad zip", sheet)
        return 0
    return written


def run(bbox: tuple[int, int, int, int], dem_dir: Path, dry_run: bool = False) -> None:
    lat_min, lon_min, lat_max, lon_max = bbox
    dem_dir.mkdir(parents=True, exist_ok=True)

    tiles = _tiles_for_bbox(bbox)
    sheets = _sheets_for_bbox(bbox)
    logger.info(
        "Target: %d tiles in %d sheets for bbox lat=[%d,%d], lon=[%d,%d] → %s",
        len(tiles), len(sheets), lat_min, lat_max, lon_min, lon_max, dem_dir,
    )

    if dry_run:
        # Group tiles by sheet for a readable log
        from collections import defaultdict
        by_sheet: dict[str, list[str]] = defaultdict(list)
        for lat, lon in tiles:
            by_sheet[_sheet_for(lat, lon)].append(_tile_name(lat, lon))
        for sheet in sorted(by_sheet):
            logger.info("  %s.zip → %d tiles", sheet, len(by_sheet[sheet]))
        return

    total_written = 0
    for sheet in sorted(sheets):
        wanted = {
            _tile_name(lat, lon) for lat, lon in tiles
            if _sheet_for(lat, lon) == sheet
        }
        total_written += _download_sheet(sheet, wanted, dem_dir)

    have = sum(1 for lat, lon in tiles if (dem_dir / _tile_name(lat, lon)).exists())
    logger.info("Done: wrote %d new tiles. %d/%d tiles now on disk.",
                total_written, have, len(tiles))


def _parse_bbox(s: str) -> tuple[int, int, int, int]:
    parts = [int(float(x)) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be lat_min,lon_min,lat_max,lon_max")
    return parts[0], parts[1], parts[2], parts[3]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download HGT tiles for a bbox")
    parser.add_argument("--bbox", type=_parse_bbox, help="lat_min,lon_min,lat_max,lon_max (integers)")
    parser.add_argument("--region", choices=sorted(REGION_BBOXES.keys()),
                        help="Shorthand bbox for a known import region")
    parser.add_argument("--dem-dir", default=None,
                        help="Where to write HGT files (default DEM_DIR env or DATA_DIR/dem)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.bbox and not args.region:
        parser.error("provide --bbox or --region")
    bbox = args.bbox or REGION_BBOXES[args.region]

    dem_dir = Path(args.dem_dir or os.environ.get("DEM_DIR")
                   or os.path.join(os.environ.get("DATA_DIR", "/app/data"), "dem"))
    run(bbox, dem_dir, dry_run=args.dry_run)
