#!/usr/bin/env python3
"""Seed the local heatmap with K test users to meet K-anonymity threshold.

Each user uploads one or more GPX files with contribute_heatmap=true.
After K users have contributed overlapping cells, those cells become
visible in the heatmap API (user_count >= K = 5 by default).

Usage:
    # Basic — seed with default fixtures (Montpellier VTT, 5 users)
    python3 scripts/seed_heatmap.py

    # With a specific GPX file
    python3 scripts/seed_heatmap.py --gpx e2e/fixtures/gpx/montpellier_mtb.gpx --sport mtb

    # Custom API + more users
    python3 scripts/seed_heatmap.py --api http://localhost:8787 --users 7

    # Seed multiple GPX fixtures (different sports)
    python3 scripts/seed_heatmap.py --all-fixtures

After running, open http://localhost:3787/map/ and enable the Heatmap layer.
"""
import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "e2e" / "fixtures" / "gpx"

# Default GPX seeds: (filename, sport) — chosen for geographic overlap
DEFAULT_SEEDS = [
    ("montpellier_mtb.gpx", "mtb"),
    ("mtb_pic_st_loup.gpx", "mtb"),
    ("gravel_garrigue.gpx", "gravel"),
    ("road_montpellier_palavas.gpx", "road"),
    ("mtb_herault_gorges.gpx", "mtb"),
]


# ── HTTP helpers (stdlib only, no external deps) ─────────────────────────────

def _json_request(url: str, data: Optional[dict] = None, token: Optional[str] = None,
                  method: str = "GET") -> dict:
    body = json.dumps(data).encode() if data else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:200]}") from e


def _upload_gpx(api: str, token: str, gpx_path: pathlib.Path, sport: str) -> dict:
    """POST /gpx/upload with multipart/form-data (stdlib only)."""
    boundary = "----SeederBoundaryXK9"
    gpx_bytes = gpx_path.read_bytes()

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{gpx_path.name}"\r\n'
        f"Content-Type: application/gpx+xml\r\n\r\n"
    ).encode() + gpx_bytes + b"\r\n"
    body += field("sport", sport)
    body += field("contribute_heatmap", "true")
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{api}/gpx/upload",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}") from e


# ── User management ───────────────────────────────────────────────────────────

def _register_or_login(api: str, index: int, password: str = "SeedHeatmap!1") -> str:
    """Register a seed user (or log in if already exists). Returns JWT token."""
    email = f"seed_{index:02d}@example.com"
    username = f"seed_{index:02d}"

    try:
        data = _json_request(
            f"{api}/auth/register",
            {"email": email, "password": password, "username": username},
            method="POST",
        )
        return data["access_token"]
    except RuntimeError:
        # Already registered — log in via OAuth2 form
        body = urllib.parse.urlencode({"username": email, "password": password}).encode()
        req = urllib.request.Request(
            f"{api}/auth/login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["access_token"]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed local heatmap with test data")
    parser.add_argument("--api", default="http://localhost:8787", help="Backend API URL")
    parser.add_argument("--gpx", default=None, help="Single GPX file to use for all users")
    parser.add_argument("--sport", default="mtb", help="Sport when --gpx is set")
    parser.add_argument("--users", "-k", type=int, default=5,
                        help="Number of seed users (default=5, matching K-anonymity threshold)")
    parser.add_argument("--all-fixtures", action="store_true",
                        help="Cycle through all default fixture files")
    args = parser.parse_args()

    # Verify backend is reachable
    try:
        _json_request(f"{args.api}/healthz")
    except Exception as e:
        print(f"✗ Cannot reach backend at {args.api}: {e}", file=sys.stderr)
        print("  Make sure 'docker compose up -d' is running.", file=sys.stderr)
        sys.exit(1)

    # Determine seeds
    if args.gpx:
        gpx_path = pathlib.Path(args.gpx)
        if not gpx_path.exists():
            print(f"✗ GPX file not found: {args.gpx}", file=sys.stderr)
            sys.exit(1)
        seeds = [(gpx_path, args.sport)] * args.users
    elif args.all_fixtures:
        seeds = []
        for fname, sport in DEFAULT_SEEDS * args.users:
            p = FIXTURES_DIR / fname
            if p.exists():
                seeds.append((p, sport))
            if len(seeds) >= args.users:
                break
    else:
        # Default: all users upload the Montpellier MTB trace (guaranteed cell overlap)
        mtb = FIXTURES_DIR / "montpellier_mtb.gpx"
        if not mtb.exists():
            print(f"✗ Default fixture not found: {mtb}", file=sys.stderr)
            sys.exit(1)
        seeds = [(mtb, "mtb")] * args.users

    print(f"\nCHEMINS COMMUNS — Heatmap Seeder")
    print(f"  API:   {args.api}")
    print(f"  Users: {args.users}")
    print(f"  Seeds: {', '.join(str(p.name) for p, _ in seeds[:5])}")
    print()

    ok = 0
    for i, (gpx_path, sport) in enumerate(seeds, start=1):
        print(f"  [{i:02d}/{args.users}] seed_{i:02d} — ", end="", flush=True)
        try:
            token = _register_or_login(args.api, i)
            result = _upload_gpx(args.api, token, gpx_path, sport)
            cells = result.get("cells_indexed", 0)
            status = result.get("status", "?")
            print(f"{status}  {gpx_path.name} ({sport})  → {cells} cells")
            ok += 1
        except Exception as e:
            print(f"ERROR — {e}")

    # Summary
    print()
    try:
        sports_to_check = list({s for _, s in seeds})
        total_visible = 0
        for sport in sports_to_check:
            resp = _json_request(f"{args.api}/heatmap/stats?sport={sport}&zoom=14")
            visible = len(resp.get("cells", []))
            k = resp.get("k_anonymity", 5)
            total_visible += visible
            icon = "✓" if visible > 0 else "·"
            print(f"  {icon} Heatmap [{sport:7s}] — {visible} visible cells (K={k})")
        print()
        if total_visible > 0:
            print(f"✓ {total_visible} heatmap cells seeded successfully!")
            print(f"  → Open http://localhost:3787/map/ and enable the Heatmap layer")
        else:
            print(f"⚠ No cells visible yet.")
            print(f"  Cells only appear when user_count >= K ({k}).")
            print(f"  Try increasing --users above {k}.")
    except Exception as e:
        print(f"Could not query heatmap stats: {e}")


if __name__ == "__main__":
    main()
