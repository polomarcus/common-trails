"""CHEMINS COMMUNS — Backend API entry point."""
import contextlib
import datetime
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.responses import FileResponse, JSONResponse

from app.logging_config import setup_logging
from app.rate_limit import limiter

setup_logging()

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
# Read version from pyproject.toml (set by release-please)
try:
    import importlib.metadata as _meta
    _APP_VERSION = _meta.version("common-trails-backend")
except Exception:
    try:
        import tomllib
        _pyproject = Path(os.path.dirname(__file__)).parent / "pyproject.toml"
        _APP_VERSION = tomllib.loads(_pyproject.read_text()).get("project", {}).get("version", "dev") if _pyproject.exists() else "dev"
    except Exception:
        _APP_VERSION = "dev"

if _sentry_dsn:
    from app.sentry_scrub import before_send, before_send_transaction
    sentry_sdk.init(
        dsn=_sentry_dsn,
        release=_APP_VERSION,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        environment=os.environ.get("ENV", "production"),
        enable_tracing=True,
        send_default_pii=False,
        # Strip raw lat/lon from URLs, transaction names, span names, and
        # breadcrumb data. Without this, /routing?start_lat=...&start_lon=...
        # query params end up in Sentry event payloads — a 4dp coord
        # identifies an 11m square, often a user's home.
        before_send=before_send,
        before_send_transaction=before_send_transaction,
    )

logger = logging.getLogger(__name__)

# ── Demo seed data (routes/trips only — activities come from bundled fixture) ──
from app.seed_demo_data import DEMO_ROUTES as _DEMO_ROUTES


def _seed_default_user() -> str:
    """Create a default dev user (admin@admin / admin) on startup if missing.

    Uses a deterministic UUID derived from the email so the user_id is stable
    across restarts — existing JWTs remain valid after a hot-reload or restart.
    Returns the user_id of the admin user.
    """
    from app.api.auth import _hash_password
    from app.db.models import User
    from app.db.session import SessionLocal

    email = os.environ.get("ADMIN_EMAIL", "admin@admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    # Deterministic UUID5: same email → same UUID every restart
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"admin:{email}"))

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == uid).first()
        if not existing:
            user = User(
                id=uid,
                email=email,
                username="admin",
                hashed_password=_hash_password(password),
                is_admin=True,
            )
            db.add(user)
            db.commit()
    finally:
        db.close()

    return uid



def _seed_demo_routes(admin_id: str) -> None:
    """Seed public demo routes with forks for testing the GitHub-like model."""
    from app.db.models import Route, RouteFork, RouteVersion
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if db.query(Route).first():
            return  # already seeded

        seeded_ids: list[str] = []
        seeded_routes: dict[str, Route] = {}
        seeded_versions: dict[str, RouteVersion] = {}
        base_date = datetime.datetime(2025, 9, 1, tzinfo=datetime.UTC)

        for i, data in enumerate(_DEMO_ROUTES):
            route_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-route:{data['name']}"))
            version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-version:{data['name']}"))
            geojson = json.dumps({"type": "LineString", "coordinates": data["coords"]})
            created_at = base_date + datetime.timedelta(days=i * 8)

            version = RouteVersion(
                id=version_id,
                route_id=route_id,
                author_id=admin_id,
                message="Version initiale",
                geometry_geojson=geojson,
                distance_m=data["distance_m"],
                elevation_gain_m=data["elevation_gain_m"],
            )
            db.add(version)
            seeded_versions[version_id] = version

            route = Route(
                id=route_id,
                owner_id=admin_id,
                name=data["name"],
                description=data.get("description"),
                sport=data["sport"],
                visibility="public",
                status="published",
                forked_from_id=None,
                current_version_id=version_id,
                distance_m=data["distance_m"],
                elevation_gain_m=data["elevation_gain_m"],
                created_at=created_at,
            )
            db.add(route)
            seeded_ids.append(route_id)
            seeded_routes[route_id] = route

        # Add forks + upstream PRs for ALL routes to demo the GitHub-like model
        fork_user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "demo-fork-user"))
        fork_descriptions = [
            "Départ depuis le centre-ville, évite la zone industrielle.",
            "Variante par le chemin de crête — vue panoramique garantie.",
            "Boucle raccourcie idéale pour les sorties rapides en semaine.",
            "Extension vers le lac — 8 km de plus pour les amateurs.",
            "Passage alternatif par les vignes — moins de dénivelé.",
            "Variante hivernale : asphalte tout au long, zéro boue.",
            "Détour par le belvédère — arrêt photo obligatoire.",
            "Version longue avec ravitaillement au village.",
            "Boucle inverse — profil inversé, descente finale reposante.",
            "Raccourci voie verte — adapté aux vélos de route.",
            "Départ alternatif parking gratuit + 2 km de sentier en plus.",
            "Variante trail — remplace 4 km de route par single-track.",
        ]
        for i, parent_id in enumerate(seeded_ids):
            parent = seeded_routes[parent_id]
            parent_version_id = parent.current_version_id

            fork_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-fork:{parent_id}"))
            fork_ver_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-fork-ver:{parent_id}"))
            fork_name = f"Variante — {parent.name}"
            fork_date = base_date + datetime.timedelta(days=i * 8 + 14)

            # Fork geometry: slightly shifted start + end for realism
            parent_ver = seeded_versions[parent_version_id]
            orig_coords = json.loads(parent_ver.geometry_geojson)["coordinates"]
            shift_x, shift_y = (i % 3) * 0.003, (i % 4) * 0.002
            fork_coords = [[c[0] + shift_x, c[1] + shift_y] for c in orig_coords]
            fork_geojson = json.dumps({"type": "LineString", "coordinates": fork_coords})
            desc_idx = i % len(fork_descriptions)

            fork_ver = RouteVersion(
                id=fork_ver_id,
                route_id=fork_id,
                author_id=fork_user_id,
                message="Variante proposée",
                geometry_geojson=fork_geojson,
                distance_m=parent.distance_m,
                elevation_gain_m=parent.elevation_gain_m,
            )
            db.add(fork_ver)

            fork_route = Route(
                id=fork_id,
                owner_id=fork_user_id,
                name=fork_name,
                description=fork_descriptions[desc_idx],
                sport=parent.sport,
                visibility="public",
                status="published",
                forked_from_id=parent_id,
                current_version_id=fork_ver_id,
                distance_m=parent.distance_m,
                elevation_gain_m=parent.elevation_gain_m,
                created_at=fork_date,
            )
            db.add(fork_route)

            fork_record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-fork-rec:{parent_id}"))
            db.add(RouteFork(
                id=fork_record_id,
                parent_route_id=parent_id,
                fork_route_id=fork_id,
                forked_by_id=fork_user_id,
            ))

        # Flush routes, versions, forks so FK references are visible
        db.flush()

        db.commit()
    finally:
        db.close()


def _seed_demo_trips(admin_id: str) -> None:
    """Seed sample trips (bikepacking multi-day journeys) for the admin user."""
    from app.db.models import Route, Trip, TripPOI, TripStage
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if db.query(Trip).first():
            return  # already seeded

        # Find seeded route IDs by name for linking stages
        def _route_id_by_name(name: str) -> str | None:
            route = db.query(Route).filter(Route.name == name).first()
            return route.id if route else None

        base_date = datetime.datetime(2025, 10, 1, tzinfo=datetime.UTC)

        # ── Trip 1: Traversée des Garrigues (3 stages, gravel) ────────────
        trip1_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "demo-trip:traversee-garrigues"))
        trip1 = Trip(
            id=trip1_id,
            owner_id=admin_id,
            name="Traversée des Garrigues",
            slug="traversee-garrigues",
            description="Trois jours de gravel à travers les garrigues du Languedoc. "
                        "Bivouacs en nature, sources d'eau identifiées, passages techniques.",
            cover_image_url=None,
            sport="gravel",
            visibility="public",
            status="planned",
            region="Hérault, Languedoc",
            tags_json=json.dumps(["bikepacking", "gravel", "garrigue", "3-jours"]),
            total_distance_m=0,
            total_dplus_m=0,
            forked_from_id=None,
            created_at=base_date,
        )
        db.add(trip1)

        # Stages for trip 1
        trip1_routes = [
            ("Garrigue des Matelles — gravel loop", "Matelles → Saint-Martin-de-Londres"),
            ("Saint-Guilhem — gorges de l'Hérault", "Gorges de l'Hérault → Saint-Guilhem"),
            ("Camargue gravel — étangs sauvages", "Retour par les étangs"),
        ]
        for i, (route_name, title) in enumerate(trip1_routes):
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-stage:{trip1_id}:{i}"))
            lodging = ["camping", "refuge", "none"][i]
            db.add(TripStage(
                id=sid,
                trip_id=trip1_id,
                route_id=_route_id_by_name(route_name),
                day_index=i,
                title=title,
                description=None,
                estimated_time_min=[300, 360, 240][i],
                lodging_type=lodging,
                is_rest_day=False,
                created_at=base_date,
            ))

        # POIs for trip 1
        trip1_pois = [
            {"type": "water", "lon": 3.812, "lat": 43.695, "name": "Source des Matelles"},
            {"type": "viewpoint", "lon": 3.795, "lat": 43.708, "name": "Belvédère de l'Hortus"},
            {"type": "food", "lon": 3.590, "lat": 43.755, "name": "Épicerie Saint-Guilhem"},
            {"type": "water", "lon": 3.548, "lat": 43.758, "name": "Fontaine du village"},
            {"type": "camp", "lon": 3.830, "lat": 43.682, "name": "Bivouac garrigue"},
        ]
        for j, poi_data in enumerate(trip1_pois):
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-poi:{trip1_id}:{j}"))
            db.add(TripPOI(
                id=pid,
                trip_id=trip1_id,
                stage_id=None,
                type=poi_data["type"],
                lon=poi_data["lon"],
                lat=poi_data["lat"],
                name=poi_data["name"],
                notes=None,
                source="user",
                created_at=base_date,
            ))

        # ── Trip 2: Tour du Pic Saint-Loup (2 stages, road) ──────────────
        trip2_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "demo-trip:tour-pic-saint-loup"))
        trip2 = Trip(
            id=trip2_id,
            owner_id=admin_id,
            name="Tour du Pic Saint-Loup",
            slug="tour-pic-saint-loup",
            description="Week-end route autour du Pic Saint-Loup. "
                        "Montées régulières, descentes panoramiques, nuit en hôtel.",
            cover_image_url=None,
            sport="road",
            visibility="public",
            status="completed",
            region="Hérault, Pic Saint-Loup",
            tags_json=json.dumps(["route", "pic-saint-loup", "2-jours"]),
            total_distance_m=0,
            total_dplus_m=0,
            forked_from_id=None,
            created_at=base_date + datetime.timedelta(days=14),
        )
        db.add(trip2)

        trip2_routes = [
            ("Sortie route — Pic Saint-Loup", "Montpellier → Pic Saint-Loup"),
            ("Cols de l'Hérault — Boucle Sommières", "Boucle retour via Sommières"),
        ]
        for i, (route_name, title) in enumerate(trip2_routes):
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-stage:{trip2_id}:{i}"))
            db.add(TripStage(
                id=sid,
                trip_id=trip2_id,
                route_id=_route_id_by_name(route_name),
                day_index=i,
                title=title,
                description=None,
                estimated_time_min=[240, 300][i],
                lodging_type=["hotel", "none"][i],
                is_rest_day=False,
                created_at=base_date + datetime.timedelta(days=14),
            ))

        # ── Grandes Traversées (public reference collections) ────────────
        gt_collections = [
            {
                "slug": "gtmc-vtt",
                "name": "GTMC — Grande Traversée du Massif Central",
                "description": "1 360 km de VTT à travers le Massif Central, "
                               "de Clermont-Ferrand à Sète. "
                               "Balisage VTT-FFC, variantes gravel possibles.",
                "sport": "mtb",
                "region": "Massif Central (Auvergne → Languedoc)",
                "tags": ["grande-traversée", "vtt", "massif-central", "balisé"],
                "status": "planned",
            },
            {
                "slug": "gt-vtt-lozere",
                "name": "GT VTT Lozère",
                "description": "400 km de VTT en Lozère — Aubrac, Margeride, "
                               "Cévennes, Mont Lozère, Causse Méjean. "
                               "Boucle au départ de Mende.",
                "sport": "mtb",
                "region": "Lozère, Cévennes",
                "tags": ["grande-traversée", "vtt", "lozère", "balisé"],
                "status": "planned",
            },
            {
                "slug": "eurovelo-8-mediterranee",
                "name": "EuroVelo 8 — La Méditerranéenne",
                "description": "Tronçon français de l'EV8 : Perpignan → Menton. "
                               "Pistes cyclables littorales, traversée de la Camargue, "
                               "côte varoise et azuréenne.",
                "sport": "road",
                "region": "Méditerranée (Perpignan → Menton)",
                "tags": ["eurovelo", "route", "littoral", "balisé"],
                "status": "planned",
            },
            {
                "slug": "gr70-stevenson",
                "name": "GR 70 — Chemin de Stevenson",
                "description": "Le Puy-en-Velay → Saint-Jean-du-Gard, 272 km à travers "
                               "Velay, Gévaudan et Cévennes. "
                               "Classique du GR, adapté gravel sur pistes DFCI.",
                "sport": "gravel",
                "region": "Velay, Gévaudan, Cévennes",
                "tags": ["grande-traversée", "gravel", "gr", "cévennes"],
                "status": "planned",
            },
            {
                "slug": "grande-traversee-herault",
                "name": "Grande Traversée de l'Hérault VTT",
                "description": "Du Caroux aux étangs — 220 km de VTT à travers "
                               "l'arrière-pays héraultais. Garrigues, gorges, vignobles.",
                "sport": "mtb",
                "region": "Hérault",
                "tags": ["grande-traversée", "vtt", "hérault"],
                "status": "planned",
            },
        ]

        # Map GT collections to demo routes for stages
        gt_stages_map = {
            "gtmc-vtt": [
                ("VTT Pic Saint-Loup — trails techniques", "Étape 1 — Pic Saint-Loup"),
                ("Les Matelles Flow — bike park naturel", "Étape 2 — Matelles"),
                ("Mosson — nocturne hebdo", "Étape 3 — Garrigue Montpellier"),
            ],
            "gt-vtt-lozere": [
                ("VTT Pic Saint-Loup — trails techniques", "Étape 1 — Mont Lozère"),
                ("Les Matelles Flow — bike park naturel", "Étape 2 — Causse Méjean"),
            ],
            "eurovelo-8-mediterranee": [
                ("Palavas Express — aller-retour mer", "Étape 1 — Montpellier → Palavas"),
                ("Cols de l'Hérault — Boucle Sommières", "Étape 2 — Sommières → Nîmes"),
            ],
            "gr70-stevenson": [
                ("Garrigue des Matelles — gravel loop", "Étape 1 — Garrigues"),
                ("Causse du Larzac — grande traversée", "Étape 2 — Traversée du Larzac"),
                ("Saint-Guilhem — gorges de l'Hérault", "Étape 3 — Gorges de l'Hérault"),
            ],
            "grande-traversee-herault": [
                ("VTT Pic Saint-Loup — trails techniques", "Étape 1 — Caroux"),
                ("Garrigue des Matelles — gravel loop", "Étape 2 — Garrigues"),
                ("Camargue gravel — étangs sauvages", "Étape 3 — Étangs"),
            ],
        }

        gt_trip_objects = []
        for gt in gt_collections:
            gt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-trip:{gt['slug']}"))
            gt_trip = Trip(
                id=gt_id,
                owner_id=admin_id,
                name=gt["name"],
                slug=gt["slug"],
                description=gt["description"],
                cover_image_url=None,
                sport=gt["sport"],
                visibility="public",
                status=gt["status"],
                region=gt["region"],
                tags_json=json.dumps(gt["tags"]),
                total_distance_m=0,
                total_dplus_m=0,
                forked_from_id=None,
                created_at=base_date + datetime.timedelta(days=30),
            )
            db.add(gt_trip)
            gt_trip_objects.append(gt_trip)

            # Add stages from demo routes
            for i, (route_name, title) in enumerate(gt_stages_map.get(gt["slug"], [])):
                sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-stage:{gt_id}:{i}"))
                db.add(TripStage(
                    id=sid,
                    trip_id=gt_id,
                    route_id=_route_id_by_name(route_name),
                    day_index=i,
                    title=title,
                    description=None,
                    estimated_time_min=300,
                    lodging_type="camping" if i < 2 else "none",
                    is_rest_day=False,
                    created_at=base_date + datetime.timedelta(days=30),
                ))

        db.commit()

        # Recompute stats for seeded trips (need committed stages to query)
        for trip_obj in [trip1, trip2] + gt_trip_objects:
            db.refresh(trip_obj)
            stages = db.query(TripStage).filter(TripStage.trip_id == trip_obj.id).all()
            total_dist = 0.0
            total_dplus = 0.0
            for s in stages:
                if s.is_rest_day:
                    continue
                if s.route_id:
                    route = db.query(Route).filter(Route.id == s.route_id).first()
                    if route:
                        total_dist += route.distance_m or 0
                        total_dplus += route.elevation_gain_m or 0
            trip_obj.total_distance_m = total_dist
            trip_obj.total_dplus_m = total_dplus
        db.commit()
    finally:
        db.close()


# ── Startup progress tracking ──────────────────────────────────────────────────
_STARTUP_STEPS = [
    "Chargement des activités",
    "Données de démo",
    "Réseau DFCI",
    "Sentiers balisés",
    "Enrichissement des edges",
]

startup_progress: dict = {
    "step": 0,
    "total": len(_STARTUP_STEPS),
    "label": _STARTUP_STEPS[0],
    "done": False,
}


async def _background_data_load(admin_id: str) -> None:
    """Load activities, DFCI, trails, and enrichment data in background after server is ready."""
    import time as _t

    # Guard 1: already completed (e.g., TestClient re-triggering lifespan)
    if startup_progress.get("done"):
        logger.info("Background data already loaded — skipping")
        return
    # Guard 2: explicit skip for test environments
    if os.environ.get("SKIP_BACKGROUND_LOAD", "").lower() == "true":
        logger.info("SKIP_BACKGROUND_LOAD=true — skipping all background data loading")
        startup_progress["done"] = True
        return

    _t0 = _t.monotonic()

    def _advance(step: int) -> None:
        startup_progress["step"] = step
        startup_progress["label"] = _STARTUP_STEPS[step] if step < len(_STARTUP_STEPS) else "Prêt"

    _advance(0)
    await _load_activities(_t0)
    _advance(1)
    await _seed_demo_data(admin_id)
    _advance(2)
    await _load_dfci(_t0)
    _advance(3)
    await _load_trails(_t0)
    await _load_gt_routes(_t0)
    # Ensure minigraph tables exist before enrichment queries them
    await _seed_minigraph_tables()
    _advance(4)
    # Edge enrichment is no longer a startup task. The matched-era OSM
    # enrichment (surface/dangerous-highway tagging of heat_edges) was removed
    # with the raw-trace pivot — the community map renders raw GPS traces, not
    # OSM-matched edges. Startup should never do unbounded batch work — it would
    # re-run on every cold start (Cloud Run scale-to-zero, OOM restart, deploy)
    # and waste CPU for ~minutes producing zero new rows on a populated DB.

    startup_progress["step"] = len(_STARTUP_STEPS)
    startup_progress["label"] = "Prêt"
    startup_progress["done"] = True

    from sqlalchemy import text as sa_text

    # Use pg_class.reltuples for instant estimates (startup log only, exact count unnecessary)
    from app.db.session import SessionLocal
    from app.services.ingest import get_dfci_edge_count, get_trail_edge_count
    db = SessionLocal()
    try:
        heat_n = db.execute(sa_text(
            "SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relname LIKE 'heat_edges_%' "
            "AND relname NOT LIKE '%idx%' AND relname NOT LIKE '%key%'"
        )).scalar() or 0
    finally:
        db.close()
    logger.info(
        "Background load DONE in %.1fs — heat=%d dfci=%d trails=%d",
        _t.monotonic() - _t0, heat_n, get_dfci_edge_count(),
        get_trail_edge_count(),
    )

    # Pre-warm caches so the first user never waits for cold queries
    await _prewarm_caches(_t0)


async def _prewarm_caches(t0: float) -> None:
    """Pre-warm heatmap summary + full graph caches after background data load."""
    import time as _t

    # 1. Heatmap summary cache
    try:
        import asyncio

        from app.api.heatmap import heatmap_summary
        # heatmap_summary is a SYNC def (returns JSONResponse) that runs sync DB
        # work — `await heatmap_summary()` awaited the JSONResponse itself
        # (TypeError). Run it in the threadpool so we don't block the event loop
        # AND don't await a non-awaitable.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, heatmap_summary)
        logger.info("Pre-warmed heatmap summary cache (%.1fs)", _t.monotonic() - t0)
    except Exception:
        logger.warning("Failed to pre-warm heatmap summary", exc_info=True)

    # Note: full graph pre-warm removed — frontend uses area.pb tiles on demand


async def _seed_minigraph_tables() -> None:
    """Ensure minigraph_edges/vertices tables exist (idempotent CREATE IF NOT EXISTS)."""
    import asyncio

    from app.db.seed_minigraph import seed_minigraph

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, seed_minigraph)
        logger.info("Minigraph seeded: %s", result)
    except Exception:
        logger.warning("Minigraph seed failed", exc_info=True)


async def _load_activities(t0: float) -> int:
    """Load persisted activities and populate heat_edges/heat_cells in PostGIS."""
    import asyncio
    import time as _t

    from app.services.ingest import load_persisted_activities

    loop = asyncio.get_event_loop()
    try:
        n_loaded = await loop.run_in_executor(None, load_persisted_activities)
    except Exception:
        logger.warning("Activity loading failed", exc_info=True)
        n_loaded = 0
    logger.info("Activities loaded: %d (%.1fs)", n_loaded, _t.monotonic() - t0)

    return n_loaded


async def _seed_demo_data(admin_id: str) -> None:
    """Seed demo routes + trips (idempotent). Skipped in production."""
    _test = os.environ.get("TEST_MODE", "false").lower() == "true"
    if not _test and os.environ.get("SEED_DEMO_DATA", "").lower() != "true":
        logger.info("Demo seed skipped (not TEST_MODE, SEED_DEMO_DATA not set)")
        return
    import asyncio

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _seed_demo_routes, admin_id)
        await loop.run_in_executor(None, _seed_demo_trips, admin_id)
    except Exception:
        logger.warning("Demo seed failed", exc_info=True)


async def _load_dfci(t0: float) -> tuple[int, int]:
    """Import DFCI fire-prevention tracks (IGN + Hérault) in parallel."""
    import asyncio
    import time as _t

    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal

    # Quick guard: skip import if DFCI already loaded (avoids duplicate accumulation)
    db = SessionLocal()
    try:
        dfci_count = db.execute(sa_text(
            "SELECT COUNT(*) FROM (SELECT 1 FROM dfci_edges LIMIT 1001) sub"
        )).scalar() or 0
    finally:
        db.close()
    if dfci_count > 1_000:
        logger.info("DFCI already loaded (%d edges), skipping import", dfci_count)
        return 0, 0

    from app.cli.import_dfci_herault import import_dfci_herault
    from app.cli.import_dfci_ign import import_dfci_ign

    dfci_results = await asyncio.gather(
        import_dfci_ign(),
        import_dfci_herault(),
        return_exceptions=True,
    )
    n_dfci_ign = dfci_results[0] if isinstance(dfci_results[0], int) else 0
    n_dfci_herault = dfci_results[1] if isinstance(dfci_results[1], int) else 0
    if isinstance(dfci_results[0], Exception):
        logger.warning("DFCI IGN import failed: %s", dfci_results[0])
    if isinstance(dfci_results[1], Exception):
        logger.warning("DFCI Hérault import failed: %s", dfci_results[1])
    logger.info("DFCI loaded: IGN=%d Hérault=%d (%.1fs)", n_dfci_ign, n_dfci_herault, _t.monotonic() - t0)
    return n_dfci_ign, n_dfci_herault


async def _load_trails(t0: float) -> int:
    """Import marked trails (GR, GT, PR, EV, GRP) from OSM route relations."""
    import time as _t

    from app.cli.import_trails import import_trails

    try:
        n_trails = await import_trails()
    except Exception:
        logger.warning("Trails import failed", exc_info=True)
        n_trails = 0
    logger.info("Trails loaded: %d (%.1fs)", n_trails, _t.monotonic() - t0)
    return n_trails


async def _load_gt_routes(t0: float) -> int:
    """Import complete GT/EV/GR trail routes as public Route objects."""
    import time as _t

    from app.cli.import_gt_routes import import_gt_routes

    try:
        n = await import_gt_routes()
    except Exception:
        logger.warning("GT routes import failed", exc_info=True)
        n = 0
    logger.info("GT routes loaded: %d (%.1fs)", n, _t.monotonic() - t0)
    return n


# DEM slope enrichment is baked at import time
# (import_dfci_ign / import_dfci_herault / import_trails). The matched-era
# heat-edges OSM enrichment CLI (app.cli.enrich_edges) was removed with the
# raw-trace pivot — the community map renders raw GPS traces, not OSM-matched
# edges, so per-edge OSM surface/highway tagging no longer applies.


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    import asyncio
    import time as _t
    _t0 = _t.monotonic()

    # M11: only seed admin user in dev/test mode
    _test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"
    if _test_mode:
        admin_id = _seed_default_user()
    else:
        admin_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"admin:{os.environ.get('ADMIN_EMAIL', 'admin@admin')}"))

    # Config-drift guard: in prod, loudly warn (don't crash) if the env vars
    # that power the event-driven Cloud Run job triggers are missing — a
    # `--set-env-vars` wipe silently drops us to the daily backstop scheduler.
    if not _test_mode and os.environ.get("ENV", "development") != "development":
        from app.services.run_jobs import warn_on_missing_job_config
        warn_on_missing_job_config()

    _elapsed = _t.monotonic() - _t0
    logger.info("READY in %.1fs — server accepting requests", _elapsed)

    # Load ALL data in background (activities, DFCI, trails, enrichment)
    # This doesn't block the startup probe — healthz responds immediately.
    bg_task = asyncio.create_task(_background_data_load(admin_id))

    yield

    bg_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await bg_task

    # Close the shared httpx client used by the Strava integration.
    from app.services.strava_client import aclose_http_client
    await aclose_http_client()

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.collections import collections_public_router
from app.api.collections import router as collections_router
from app.api.export import router as export_router
from app.api.geocode import router as geocode_router
from app.api.gpx_upload import router as gpx_upload_router
from app.api.health import router as health_router
from app.api.heatmap import router as heatmap_router
from app.api.imports import router as imports_router
from app.api.integrations_strava import router as strava_router
from app.api.integrations_strava_webhook import router as strava_webhook_router
from app.api.internal_artefacts import router as internal_artefacts_router
from app.api.internal_ingest import router as internal_ingest_router
from app.api.internal_strava_health import router as internal_strava_health_router
from app.api.internal_strava_webhook import router as internal_strava_webhook_router
from app.api.me_activities import router as me_activities_router
from app.api.me_photos import router as me_photos_router
from app.api.me_stats import router as me_stats_router
from app.api.notifications import router as notifications_router
from app.api.routes import router as routes_router
from app.api.share import router as share_router
from app.api.tags_export import router as tags_export_router
from app.api.trips import router as trips_router

app = FastAPI(
    title="CHEMINS COMMUNS API",
    description="Open source cycling map — route, gravel, VTT, off-road.",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter

app.add_middleware(GZipMiddleware, minimum_size=1000)

def _extract_origin(url: str) -> str:
    """Extract scheme+host+port from a URL (CORS origin has no path)."""
    from urllib.parse import urlparse
    p = urlparse(url)
    origin = f"{p.scheme}://{p.hostname}"
    if p.port and p.port not in (80, 443):
        origin += f":{p.port}"
    return origin

# H9: Read allowed CORS origins from env var. In production, CORS_ORIGINS must be set explicitly.
_test_mode_cors = os.environ.get("TEST_MODE", "false").lower() == "true"
_cors_env = os.environ.get("CORS_ORIGINS", "")
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
elif _test_mode_cors:
    _frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3787")
    _cors_origins = list({
        _extract_origin(_frontend_url),
        "http://localhost:3787",
        "http://localhost:3000",
    })
else:
    _frontend_url = os.environ.get("FRONTEND_URL", "")
    _cors_origins = [_extract_origin(_frontend_url)] if _frontend_url else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request, exc):
    retry_after = getattr(exc, "retry_after", 60)
    return JSONResponse(
        status_code=429,
        content={"detail": "Trop de requêtes — réessayez dans quelques secondes."},
        headers={"Retry-After": str(retry_after)},
    )

_is_prod = os.environ.get("ENV", "development") != "development"


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    ct = response.headers.get("content-type", "")
    if "text/html" not in ct:
        # Strict CSP for API responses only; frontend HTML needs scripts/styles
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if _is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(health_router)
app.include_router(auth_router, prefix="/auth")
app.include_router(strava_router, prefix="/integrations/strava")
# Strava push webhooks — public endpoint, no auth (Strava can't send tokens).
# The router declares its own /integrations/strava/webhook paths so no prefix.
app.include_router(strava_webhook_router)
app.include_router(routes_router)
app.include_router(imports_router)
app.include_router(heatmap_router)
app.include_router(geocode_router)
app.include_router(gpx_upload_router)
app.include_router(me_stats_router)
app.include_router(me_activities_router)
app.include_router(me_photos_router)
app.include_router(notifications_router)
app.include_router(tags_export_router)
app.include_router(share_router)
app.include_router(collections_router)
app.include_router(collections_public_router)
app.include_router(trips_router)
app.include_router(admin_router)
app.include_router(internal_ingest_router)
app.include_router(internal_artefacts_router)
app.include_router(export_router)
app.include_router(internal_strava_webhook_router)
app.include_router(internal_strava_health_router)

# ── Frontend static file serving (baked into Docker image) ────────────────────
_FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/app/static-frontend"))

# Middleware: serve frontend HTML for browser navigation that conflicts with API paths.
# Without this, GET /collections from a browser returns JSON instead of the collections page.
_FRONTEND_PAGES = {"collections", "routes", "discover", "stats", "strava", "account", "activities", "admin", "methode", "privacy"}


# The Next.js static export references content-HASHED chunks from the HTML
# entry (index.html / map.html / …). If the browser heuristically caches the
# HTML (which it does when NO Cache-Control is sent), a returning visitor keeps
# an OLD HTML → old chunk refs → a stale JS bundle even after a fresh deploy.
# That stranded users on a broken build after a bad deploy window (2026-08-08:
# the heatmap URL was empty in the cached bundle → "no heatmap on /map"). Fix:
# HTML always revalidates (cheap 304 via etag), hashed assets stay immutable.
_HTML_HEADERS = {"Cache-Control": "no-cache"}


def _html_response(path: "Path") -> FileResponse:
    return FileResponse(path, media_type="text/html", headers=_HTML_HEADERS)


@app.middleware("http")
async def frontend_html_priority(request: Request, call_next):
    """If a browser navigates to a known frontend page, serve the HTML file
    instead of letting the API router handle it."""
    if _FRONTEND_DIR.is_dir() and request.method == "GET":
        path = request.url.path.strip("/").split("/")[0]
        accept = request.headers.get("accept", "")
        if path in _FRONTEND_PAGES and "text/html" in accept:
            html_file = _FRONTEND_DIR / f"{path}.html"
            if html_file.is_file():
                return _html_response(html_file)
    return await call_next(request)


if _FRONTEND_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    class _ImmutableStaticFiles(StaticFiles):
        """/_next assets are content-hashed → cache them hard + immutable."""
        def file_response(self, *args, **kwargs):  # type: ignore[override]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    # Serve /_next/static assets with immutable cache (hashed filenames).
    _next_dir = _FRONTEND_DIR / "_next"
    if _next_dir.is_dir():
        app.mount("/_next", _ImmutableStaticFiles(directory=_next_dir), name="next-static")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Catch-all: serve frontend static files with clean URL support."""
        # Try exact file (e.g. /favicon.ico, /robots.txt, /og-card.svg,
        # service workers). Not content-hashed → revalidate (no-cache) so a
        # deploy can never strand a stale service worker or asset.
        file_path = _FRONTEND_DIR / path
        if file_path.is_file() and ".." not in path:
            return FileResponse(file_path, headers=_HTML_HEADERS)
        # Clean URL: /map → map.html, /discover → discover.html
        html_path = _FRONTEND_DIR / f"{path}.html"
        if html_path.is_file() and ".." not in path:
            return _html_response(html_path)
        # Directory index: /me/routes → me/routes/index.html
        index_path = _FRONTEND_DIR / path / "index.html"
        if index_path.is_file() and ".." not in path:
            return _html_response(index_path)
        # SPA fallback → index.html
        index = _FRONTEND_DIR / "index.html"
        if index.is_file():
            return _html_response(index)
        return JSONResponse({"detail": "Not Found"}, status_code=404)
