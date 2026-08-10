#!/usr/bin/env python3
"""Seed realistic public routes for the Montpellier region (discover page).

Creates 8 public routes covering road, gravel, MTB, off-road and running
in the Montpellier / Hérault area (~3.6–4.2°E, 43.4–43.9°N).

Usage:
    python3 scripts/seed_routes.py
    python3 scripts/seed_routes.py --api http://localhost:8787
    python3 scripts/seed_routes.py --clear   # drop existing seeded routes first

After running, open http://localhost:3787/discover/ to see the routes.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "http://localhost:8787"

# ---------------------------------------------------------------------------
# Route fixtures — real-ish GPS coords for Montpellier / Hérault region
# ---------------------------------------------------------------------------

ROUTES = [
    # ── Road ────────────────────────────────────────────────────────────────
    {
        "name": "Montpellier → Palavas-les-Flots",
        "sport": "road",
        "visibility": "public",
        "description": "Liaison plate vers la mer via la voie verte du Lez. Idéal pour les sorties rapides.",
        "distance_m": 17400,
        "elevation_gain_m": 28,
        "coordinates": [
            [3.8770, 43.6076], [3.8830, 43.5950], [3.8890, 43.5820],
            [3.8960, 43.5680], [3.9020, 43.5560], [3.9080, 43.5440],
            [3.9170, 43.5310], [3.9250, 43.5200], [3.9290, 43.5270],
        ],
    },
    {
        "name": "Tour du Lez — Boucle Nord",
        "sport": "road",
        "visibility": "public",
        "description": "Boucle remontant la vallée du Lez jusqu'à Prades-le-Lez. Peu de dénivelé, parfait pour rouler vite.",
        "distance_m": 34000,
        "elevation_gain_m": 145,
        "coordinates": [
            [3.8770, 43.6076], [3.8800, 43.6300], [3.8820, 43.6500],
            [3.8850, 43.6750], [3.8900, 43.6950], [3.8970, 43.7100],
            [3.9100, 43.7200], [3.9250, 43.7150], [3.9350, 43.7000],
            [3.9300, 43.6800], [3.9150, 43.6600], [3.9000, 43.6450],
            [3.8900, 43.6300], [3.8820, 43.6150], [3.8770, 43.6076],
        ],
    },

    # ── Gravel ──────────────────────────────────────────────────────────────
    {
        "name": "Garrigue Gravel — Boucle des Mattes",
        "sport": "gravel",
        "visibility": "public",
        "description": "Boucle dans la garrigue montpelliéraine entre Restinclières et Buzignargues. Chemins blancs et sous-bois.",
        "distance_m": 42000,
        "elevation_gain_m": 680,
        "coordinates": [
            [3.8770, 43.6900], [3.9000, 43.7050], [3.9200, 43.7200],
            [3.9400, 43.7350], [3.9600, 43.7450], [3.9800, 43.7400],
            [4.0000, 43.7250], [4.0150, 43.7100], [4.0050, 43.6900],
            [3.9850, 43.6750], [3.9650, 43.6600], [3.9400, 43.6650],
            [3.9200, 43.6700], [3.9000, 43.6800], [3.8770, 43.6900],
        ],
    },
    {
        "name": "Pic Saint-Loup — Traversée Gravel",
        "sport": "gravel",
        "visibility": "public",
        "description": "Liaison gravel au pied du Pic Saint-Loup, entre Saint-Mathieu et Valflaunès. Vue dégagée sur le pic.",
        "distance_m": 28500,
        "elevation_gain_m": 520,
        "coordinates": [
            [3.8620, 43.7620], [3.8500, 43.7700], [3.8380, 43.7800],
            [3.8250, 43.7900], [3.8150, 43.7980], [3.8050, 43.8050],
            [3.8000, 43.8150], [3.8100, 43.8200], [3.8250, 43.8150],
            [3.8400, 43.8050], [3.8550, 43.7950], [3.8700, 43.7850],
            [3.8800, 43.7750], [3.8750, 43.7680], [3.8620, 43.7620],
        ],
    },

    # ── MTB ─────────────────────────────────────────────────────────────────
    {
        "name": "Pic Saint-Loup — Boucle VTT",
        "sport": "mtb",
        "visibility": "public",
        "description": "Boucle technique autour du Pic Saint-Loup. Single tracks et sentiers rocheux. Niveau intermédiaire.",
        "distance_m": 21000,
        "elevation_gain_m": 650,
        "coordinates": [
            [3.8620, 43.7620], [3.8490, 43.7700], [3.8380, 43.7790],
            [3.8290, 43.7870], [3.8220, 43.7950], [3.8180, 43.8040],
            [3.8240, 43.8120], [3.8350, 43.8090], [3.8460, 43.8010],
            [3.8560, 43.7940], [3.8640, 43.7860], [3.8700, 43.7760],
            [3.8680, 43.7680], [3.8620, 43.7620],
        ],
    },
    {
        "name": "Gorges de l'Hérault — VTT Enduro",
        "sport": "mtb",
        "visibility": "public",
        "description": "Descente technique dans les gorges de l'Hérault depuis Saint-Guilhem-le-Désert. Passages engagés.",
        "distance_m": 18500,
        "elevation_gain_m": 780,
        "coordinates": [
            [3.5400, 43.7400], [3.5350, 43.7300], [3.5280, 43.7200],
            [3.5220, 43.7100], [3.5180, 43.6980], [3.5210, 43.6870],
            [3.5280, 43.6770], [3.5380, 43.6720], [3.5480, 43.6780],
            [3.5540, 43.6880], [3.5580, 43.6990], [3.5550, 43.7100],
            [3.5500, 43.7220], [3.5460, 43.7330], [3.5400, 43.7400],
        ],
    },

    # ── Off-road ────────────────────────────────────────────────────────────
    {
        "name": "Camargue Gardoise — Piste 4x4",
        "sport": "offroad",
        "visibility": "public",
        "description": "Piste en bord de Camargue entre Le Grau-du-Roi et Aigues-Mortes. Sable et graviers, superbe à vélo de plage.",
        "distance_m": 24000,
        "elevation_gain_m": 12,
        "coordinates": [
            [4.1350, 43.5150], [4.1200, 43.5250], [4.1050, 43.5350],
            [4.0900, 43.5400], [4.0750, 43.5380], [4.0600, 43.5320],
            [4.0450, 43.5280], [4.0300, 43.5270], [4.0200, 43.5320],
            [4.0150, 43.5400],
        ],
    },

    # ── Running ─────────────────────────────────────────────────────────────
    {
        "name": "Trail Hortus — Rando Montpellier",
        "sport": "running",
        "visibility": "public",
        "description": "Boucle trail sur le massif de l'Hortus, vue panoramique sur la plaine de Montpellier et la Méditerranée.",
        "distance_m": 14500,
        "elevation_gain_m": 480,
        "coordinates": [
            [3.7950, 43.7350], [3.7880, 43.7450], [3.7820, 43.7560],
            [3.7780, 43.7660], [3.7800, 43.7760], [3.7870, 43.7840],
            [3.7970, 43.7870], [3.8060, 43.7820], [3.8100, 43.7720],
            [3.8080, 43.7620], [3.8030, 43.7530], [3.7980, 43.7440],
            [3.7950, 43.7350],
        ],
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(url: str, data: dict | None = None, token: str | None = None,
             method: str = "GET") -> dict:
    body = json.dumps(data).encode() if data else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()[:300]
        raise RuntimeError(f"HTTP {e.code}: {body_text}") from e


def _register_or_login(api: str, email: str, password: str, username: str) -> str:
    try:
        data = _request(f"{api}/auth/register",
                        {"email": email, "password": password, "username": username}, method="POST")
        return data["access_token"]
    except RuntimeError:
        form = urllib.parse.urlencode({"username": email, "password": password}).encode()
        req = urllib.request.Request(
            f"{api}/auth/login", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["access_token"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed public routes for Montpellier area")
    parser.add_argument("--api", default=API_URL, help="Backend API URL")
    args = parser.parse_args()

    # Health check
    try:
        _request(f"{args.api}/healthz")
    except Exception as e:
        print(f"✗ Cannot reach backend at {args.api}: {e}", file=sys.stderr)
        sys.exit(1)

    # Register seed author
    token = _register_or_login(
        args.api,
        email="seed_routes@example.com",
        password="SeedRoutes!1",
        username="seed_routes",
    )
    print(f"\nCHEMINS COMMUNS — Route Seeder ({args.api})")
    print(f"  Seeding {len(ROUTES)} routes in Montpellier / Hérault area\n")

    ok = 0
    for route in ROUTES:
        coords = route["coordinates"]
        geojson = json.dumps({"type": "LineString", "coordinates": coords})
        payload = {
            "name": route["name"],
            "sport": route["sport"],
            "visibility": route["visibility"],
            "description": route.get("description"),
            "geometry_geojson": geojson,
            "distance_m": route.get("distance_m"),
            "elevation_gain_m": route.get("elevation_gain_m"),
        }
        try:
            result = _request(f"{args.api}/routes", payload, token=token, method="POST")
            dist_km = (route.get("distance_m") or 0) / 1000
            elev = route.get("elevation_gain_m") or 0
            print(f"  ✓ [{route['sport']:8s}] {route['name'][:50]:50s}  {dist_km:.0f}km  +{elev:.0f}m")
            ok += 1
        except RuntimeError as e:
            print(f"  ✗ {route['name']}: {e}")

    print(f"\n{ok}/{len(ROUTES)} routes seeded.")
    if ok > 0:
        print(f"  → Open http://localhost:3787/discover/ to browse them")
        print(f"  → Tip: select 'Tout' profile to see all sports")


if __name__ == "__main__":
    main()
