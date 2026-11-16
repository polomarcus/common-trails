"""Hors route déduit du terrain pour les sorties vélo au sport générique.

Le problème (mesuré en prod, 2026-11-16) : les sorties d'un compteur Garmin
réglé sur le profil « Vélo » générique arrivent en ``cycling/generic`` — sans
route, gravel ni VTT. L'import les classe ``road`` avec ``sport_is_default``.
Pour un compte qui roule surtout sur chemins, des centaines de sorties gravel /
VTT polluaient la couche Route.

Le signal : la part du parcours sur des voies **non goudronnées** d'OpenStreetMap
(pistes, sentiers, surfaces terre / gravier…). Une vraie sortie route y passe
très peu (référence prod : médiane 1 %, 3 sorties sur 4 sous 2 %) ; les sorties
par défaut d'un compte qui roule sur chemins sont à 42 % en médiane. Au-delà de
``OFFROAD_UNPAVED_SHARE`` (15 % par défaut) la sortie passe en ``offroad`` —
le filtre « Off-road (VTT+Gravel) » de la carte, PAS gravel ni VTT : le terrain
ne permet pas de les distinguer (les deux se chevauchent complètement).

``sport_is_default`` reste VRAI : c'est une déduction, pas la parole du
cycliste. La sortie reste listée dans « Sorties à préciser » et la moindre
action du propriétaire (gravel, VTT, route) remplace la déduction. ``offroad``
n'étant pas un sport éditable, ``sport='offroad' AND sport_is_default``
identifie exactement les sorties déduites.

Les données OSM ne vivent PAS en base : des tuiles de 0,5° précalculées hors
ligne (``app.cli.build_osm_surface_tiles``, depuis un extrait ``.osm.pbf``)
dans le bucket privé des uploads (``gs://<UPLOADS_BUCKET>/osm-surface/v1``,
surchargeable par ``OSM_SURFACE_URL``). Chaque tuile = deux ensembles de
cellules de ``GRID_DEG`` (≈ 22 m × 16 m) : goudronnées et non goudronnées.
Une sortie hors des tuiles couvertes n'est pas mesurée : elle reste telle quelle.
"""

from __future__ import annotations

import array
import json
import logging
import math
import os
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GRID_DEG = 0.0002
TILE_DEG = 0.5
FORMAT_VERSION = 1
_LAT_OFF = 1 << 19  # |lat| / GRID_DEG ≤ 450 000 < 2^19
_LON_OFF = 1 << 20  # |lon| / GRID_DEG ≤ 900 000 < 2^20
_LON_BITS = 21
_LON_MASK = (1 << _LON_BITS) - 1
_MAGIC = b"OSF1"

DEFAULT_THRESHOLD = 0.15
# A ride is measured only when enough of it is known: ≥ 60 % of its cells in
# covered tiles, and ≥ 50 classified cells (≈ 1 km) — same gates as the prod
# calibration of 2026-11-16.
MIN_COVERED_FRACTION = 0.6
MIN_KNOWN_CELLS = 50

UNPAVED_SURFACES = frozenset(
    {
        "unpaved",
        "gravel",
        "fine_gravel",
        "compacted",
        "dirt",
        "earth",
        "ground",
        "grass",
        "sand",
        "mud",
        "pebblestone",
        "rock",
        "rocks",
        "stone",
        "woodchips",
        "shells",
    }
)
PAVED_SURFACES = frozenset(
    {
        "asphalt",
        "paved",
        "concrete",
        "concrete:plates",
        "concrete:lanes",
        "paving_stones",
        "sett",
        "chipseal",
        "cobblestone",
        "unhewn_cobblestone",
        "metal",
        "bricks",
    }
)
PAVED_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
        "cycleway",
        "busway",
    }
)
IGNORED_HIGHWAYS = frozenset(
    {
        "proposed",
        "construction",
        "abandoned",
        "razed",
        "platform",
        "corridor",
        "elevator",
        "bus_stop",
        "raceway",
    }
)


# ── OSM way → surface class ──────────────────────────────────────────────────


def classify_way(tags: dict) -> str | None:
    """``'p'`` (goudronné), ``'u'`` (non goudronné) or ``None`` (inconnu).

    An explicit ``surface`` wins. Otherwise the highway type decides:
    ``track`` is unpaved unless ``tracktype=grade1``; ``path`` / ``bridleway``
    are unpaved — EXCEPT a ``path`` with ``bicycle=designated`` and no surface,
    which in France is very often an asphalt greenway (voie verte) left
    untagged: unknown rather than a false "unpaved". Footways, steps,
    pedestrian areas without a surface are unknown.
    """
    hw = tags.get("highway")
    if not hw or hw in IGNORED_HIGHWAYS:
        return None
    surface = (tags.get("surface") or "").split(";")[0].strip().lower()
    if surface in UNPAVED_SURFACES:
        return "u"
    if surface in PAVED_SURFACES:
        return "p"
    if hw == "track":
        return "p" if tags.get("tracktype") == "grade1" else "u"
    if hw == "bridleway":
        return "u"
    if hw == "path":
        return None if tags.get("bicycle") == "designated" else "u"
    if hw in PAVED_HIGHWAYS:
        return "p"
    return None


# ── Grid / tiles ─────────────────────────────────────────────────────────────


def cell_key(lon: float, lat: float) -> int:
    ilat = int(round(lat / GRID_DEG)) + _LAT_OFF
    ilon = int(round(lon / GRID_DEG)) + _LON_OFF
    return (ilat << _LON_BITS) | ilon


def key_lonlat(key: int) -> tuple[float, float]:
    ilat = (key >> _LON_BITS) - _LAT_OFF
    ilon = (key & _LON_MASK) - _LON_OFF
    return ilon * GRID_DEG, ilat * GRID_DEG


def tile_name(lon: float, lat: float) -> str:
    return f"t_{math.floor(lat / TILE_DEG)}_{math.floor(lon / TILE_DEG)}.bin"


def tile_of_key(key: int) -> str:
    lon, lat = key_lonlat(key)
    return tile_name(lon, lat)


def _dist_m(a, b) -> float:
    kx = 111_320.0 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * 110_540.0)


def densify(coords: Iterable, step_m: float) -> Iterable[tuple[float, float]]:
    """Yield the polyline's points with at most ``step_m`` between them."""
    prev = None
    for p in coords:
        cur = (float(p[0]), float(p[1]))
        if prev is None:
            yield cur
        else:
            n = max(1, int(_dist_m(prev, cur) // step_m) + 1)
            for i in range(1, n + 1):
                t = i / n
                yield prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t
        prev = cur


def rasterize(coords: Iterable, step_m: float = 5.0) -> set[int]:
    """Unique grid cells a polyline passes through."""
    return {cell_key(x, y) for x, y in densify(coords, step_m)}


def encode_tile(paved: Iterable[int], unpaved: Iterable[int]) -> bytes:
    bp = zlib.compress(array.array("q", sorted(paved)).tobytes(), 6)
    bu = zlib.compress(array.array("q", sorted(unpaved)).tobytes(), 6)
    return _MAGIC + len(bp).to_bytes(8, "little") + bp + bu


def decode_tile(blob: bytes) -> tuple[frozenset[int], frozenset[int]]:
    if blob[:4] != _MAGIC:
        raise ValueError("not an OSM surface tile")
    n = int.from_bytes(blob[4:12], "little")

    def _unpack(b: bytes) -> frozenset[int]:
        a = array.array("q")
        a.frombytes(zlib.decompress(b))
        return frozenset(a)

    return _unpack(blob[12 : 12 + n]), _unpack(blob[12 + n :])


def tiles_from_ways(ways: Iterable[tuple[dict, list]]) -> dict[str, tuple[set[int], set[int]]]:
    """``{tile name: (paved cells, unpaved cells)}`` from ``(tags, coords)``
    OSM ways — the offline tile build (``app.cli.build_osm_surface_tiles``)."""
    tiles: dict[str, tuple[set[int], set[int]]] = {}
    for tags, coords in ways:
        c = classify_way(tags)
        if c is None or len(coords) < 2:
            continue
        for k in rasterize(coords):
            name = tile_of_key(k)
            if name not in tiles:
                tiles[name] = (set(), set())
            tiles[name][0 if c == "p" else 1].add(k)
    return tiles


def manifest_for(tile_names: Iterable[str], **extra) -> dict:
    return {
        "format": FORMAT_VERSION,
        "grid_deg": GRID_DEG,
        "tile_deg": TILE_DEG,
        "tiles": sorted(tile_names),
        **extra,
    }


# ── Index (lazy tile loading) ────────────────────────────────────────────────


class SurfaceIndex:
    """The covered tiles (from the manifest) + an LRU of decoded tiles.

    A dense tile (around Montpellier) decodes to ~600 k cells ≈ 40 MB of
    Python ints: 16 tiles keep the worst case well under the jobs' memory.

    ``loader(name) -> bytes | None`` reads one object next to the manifest
    (GCS blob or local file)."""

    def __init__(self, manifest: dict, loader: Callable[[str], bytes | None], *, cache_tiles: int = 16):
        if manifest.get("format") != FORMAT_VERSION or manifest.get("grid_deg") != GRID_DEG:
            raise ValueError("unsupported OSM surface manifest")
        self.manifest = manifest
        self.tiles = frozenset(manifest.get("tiles", ()))
        self._loader = loader
        self._cache: dict[str, tuple[frozenset[int], frozenset[int]]] = {}
        self._cache_tiles = cache_tiles

    def _tile(self, name: str):
        hit = self._cache.pop(name, None)
        if hit is None:
            blob = self._loader(name)
            hit = decode_tile(blob) if blob else (frozenset(), frozenset())
        self._cache[name] = hit  # re-insert = most recent
        while len(self._cache) > self._cache_tiles:
            self._cache.pop(next(iter(self._cache)))
        return hit

    def covers_key(self, key: int) -> bool:
        return tile_of_key(key) in self.tiles

    def _lookup(self, key: int) -> tuple[bool, bool]:
        name = tile_of_key(key)
        if name not in self.tiles:
            return False, False
        paved, unpaved = self._tile(name)
        return key in paved, key in unpaved

    def classify_cell(self, key: int) -> str:
        """``P`` / ``U`` / ``A`` (ambiguous) / ``N`` (no way nearby).

        The cell itself first; when it holds no way, its 8 neighbours (GPS
        error, ≈ 20 m) — a side that is only paved or only unpaved decides."""
        ip, iu = self._lookup(key)
        if ip != iu:
            return "P" if ip else "U"
        if ip:
            return "A"
        pa = ua = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                p, u = self._lookup(key + (dy << _LON_BITS) + dx)
                pa, ua = pa or p, ua or u
        if pa != ua:
            return "P" if pa else "U"
        return "A" if pa else "N"


@dataclass(frozen=True)
class Share:
    unpaved: float  # unpaved / (paved + unpaved)
    known: int  # classified cells (paved + unpaved)
    cells: int  # all cells of the ride


def unpaved_share(coords, index: SurfaceIndex) -> Share | None:
    """Share of the ride on unpaved ways, or ``None`` when not measurable
    (outside the covered tiles, or too little of it on known ways)."""
    if not coords or len(coords) < 2:
        return None
    cells = rasterize(coords)
    covered = [k for k in cells if index.covers_key(k)]
    if not cells or len(covered) / len(cells) < MIN_COVERED_FRACTION:
        return None
    c = Counter(index.classify_cell(k) for k in covered)
    known = c["P"] + c["U"]
    if known < MIN_KNOWN_CELLS:
        return None
    return Share(unpaved=c["U"] / known, known=known, cells=len(cells))


# ── Configured index (prod) ──────────────────────────────────────────────────


def threshold() -> float:
    try:
        v = float(os.environ.get("OFFROAD_UNPAVED_SHARE", DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD
    return v if 0 < v <= 1 else DEFAULT_THRESHOLD


def surface_url() -> str | None:
    """``OSM_SURFACE_URL`` (``gs://bucket/prefix`` or a local directory), else
    the uploads bucket's ``osm-surface/v1`` when ``UPLOADS_BUCKET`` is set,
    else ``None`` (feature off: nothing is inferred)."""
    url = os.environ.get("OSM_SURFACE_URL", "").strip()
    if url:
        return url.rstrip("/")
    bucket = os.environ.get("UPLOADS_BUCKET", "").strip()
    return f"gs://{bucket}/osm-surface/v1" if bucket else None


def _loader_for(url: str) -> Callable[[str], bytes | None]:
    if url.startswith("gs://"):
        bucket_name, _, prefix = url[5:].partition("/")
        from app.services.gpx_archive import _get_client

        bucket = _get_client().bucket(bucket_name)

        def _gcs(name: str) -> bytes | None:
            blob = bucket.blob(f"{prefix}/{name}" if prefix else name)
            try:
                return blob.download_as_bytes()
            except Exception:  # noqa: BLE001 — a missing tile = unknown terrain
                logger.warning("OSM surface object %s unreadable", name, exc_info=True)
                return None

        return _gcs

    def _local(name: str) -> bytes | None:
        try:
            with open(os.path.join(url, name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    return _local


def load_index(url: str | None = None) -> SurfaceIndex | None:
    """The index at ``url`` (default :func:`surface_url`), or ``None`` when
    unconfigured / no manifest — callers then infer nothing."""
    url = url or surface_url()
    if not url:
        return None
    loader = _loader_for(url)
    raw = loader("manifest.json")
    if not raw:
        logger.info("no OSM surface manifest at %s — offroad inference off", url)
        return None
    try:
        return SurfaceIndex(json.loads(raw), loader)
    except (ValueError, TypeError, AttributeError):
        logger.warning("invalid OSM surface manifest at %s", url, exc_info=True)
        return None


# ── Inference over the default-sport rides ──────────────────────────────────


@dataclass
class InferenceReport:
    examined: int = 0
    unmeasured: int = 0
    kept_road: int = 0
    offroad: int = 0
    changed_ids: list[str] | None = None
    per_user: dict | None = None  # user_id -> Counter(outcome)


def infer_offroad(
    db,
    index: SurfaceIndex,
    *,
    user_id: str | None = None,
    apply: bool = False,
    min_share: float | None = None,
) -> InferenceReport:
    """Examine the ``road`` + ``sport_is_default`` rides (of ``user_id``, or
    all) and move to ``offroad`` those whose unpaved share is ≥ ``min_share``.

    ``apply=False`` (dry run) changes nothing. Only rows STILL ``road`` +
    default at write time are updated, so a concurrent owner choice wins.
    Committing and the rebuild ping are the caller's job (it knows whether a
    rebuild is already coming)."""
    from sqlalchemy import text as sa_text

    from app.services.raw_trace_display import max_span_km, plausible_bbox, prepared_coords

    cut = threshold() if min_share is None else min_share
    rep = InferenceReport(changed_ids=[], per_user={})
    where = "sport = 'road' AND sport_is_default AND geometry_geojson IS NOT NULL"
    params: dict = {}
    if user_id is not None:
        where += " AND user_id = :u"
        params["u"] = user_id
    ids = [
        (str(r[0]), r[1])
        for r in db.execute(sa_text(f"SELECT id, user_id FROM activities WHERE {where} ORDER BY id"), params)
    ]
    span, clip = max_span_km(), plausible_bbox()
    to_offroad: list[str] = []
    for aid, uid in ids:
        # One row at a time: geometry_geojson can be MBs — never all in RAM.
        row = db.execute(
            sa_text("SELECT geometry_geojson, display_coords FROM activities WHERE id = :id"),
            {"id": aid},
        ).first()
        rep.examined += 1
        per = rep.per_user.setdefault(uid, Counter())
        coords = prepared_coords(row[0], row[1], span, clip) if row else None
        share = unpaved_share(coords, index) if coords else None
        if share is None:
            rep.unmeasured += 1
            per["unmeasured"] += 1
        elif share.unpaved >= cut:
            rep.offroad += 1
            per["offroad"] += 1
            to_offroad.append(aid)
        else:
            rep.kept_road += 1
            per["road"] += 1
    if apply and to_offroad:
        for i in range(0, len(to_offroad), 500):
            chunk = to_offroad[i : i + 500]
            res = db.execute(
                sa_text(
                    "UPDATE activities SET sport = 'offroad' "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) AND sport = 'road' AND sport_is_default "
                    "RETURNING id"
                ),
                {"ids": chunk},
            )
            rep.changed_ids.extend(str(r[0]) for r in res)
    return rep


def infer_offroad_after_import(user_id: str) -> int:
    """Best-effort hook at the end of an archive drain: infer this user's
    default-sport rides. Returns the number moved to ``offroad``; never
    raises (an inference problem must not fail a user's import)."""
    try:
        index = load_index()
        if index is None:
            return 0
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            rep = infer_offroad(db, index, user_id=user_id, apply=True)
            db.commit()
        finally:
            db.close()
        if rep.changed_ids:
            logger.info(
                "offroad inferred for %d/%d default-sport rides of user %s (unmeasured=%d)",
                len(rep.changed_ids),
                rep.examined,
                user_id,
                rep.unmeasured,
            )
        return len(rep.changed_ids)
    except Exception:  # noqa: BLE001
        logger.warning("offroad inference after import failed for user %s", user_id, exc_info=True)
        try:
            import sentry_sdk

            sentry_sdk.capture_exception()
        except Exception:  # noqa: BLE001  # pragma: no cover
            pass
        return 0
