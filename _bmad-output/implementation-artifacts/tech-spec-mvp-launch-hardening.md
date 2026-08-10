---
title: 'MVP Launch Hardening — Rate Limiting, Security Headers, Colorblind Accessibility, Open Graph'
slug: 'mvp-launch-hardening'
created: '2026-03-18'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['slowapi>=0.1.9', 'FastAPI>=0.111.0', 'Next.js 16.1.6 (static export)', 'MapLibre GL 4.3.2']
files_to_modify: ['backend/app/main.py', 'backend/pyproject.toml', 'backend/tests/test_security.py (new)', 'frontend/app/layout.tsx', 'frontend/app/discover/page.tsx', 'frontend/app/map/page.tsx', 'frontend/lib/constants.ts', 'frontend/public/og-image.jpg (new)']
code_patterns: ['FastAPI middleware after app creation (line 612)', 'APIRouter per module registered via app.include_router()', 'Next.js static metadata export', 'MapLibre addLayer paint expressions with ["get", "property"]']
test_patterns: ['pytest: backend/tests/test_auth.py (6 tests)', 'playwright: e2e/tests/', 'pure unit: frontend/lib/__tests__/']
---

# Tech-Spec: MVP Launch Hardening — Rate Limiting, Security Headers, Colorblind Accessibility, Open Graph

**Created:** 2026-03-18

## Overview

### Problem Statement

Common Trails is feature-complete for MVP (all 5 epics done) but has three launch-blocking gaps:

1. **No rate limiting or security headers** — Backend has only CORS + GZip middleware. All endpoints (auth, graph, imports, Strava trigger) are unprotected against abuse. No `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, or CSP headers. Clickjacking, MIME sniffing, auth brute-force, and DDoS via graph endpoint are all trivially exploitable. On serverless (Cloud Run), rate limiting is also **cost control** — a single bot can autoscale instances and spike the GCP bill.

2. **Colorblind accessibility incomplete** — Elevation profile slope colors are already luminance-ordered (good). Heatmap uses blue→purple→amber opacity ramp (partially accessible). But sport-colored routes on discover page + sport pills use color-only encoding with no icon/pattern differentiation for CVD users.

3. **No Open Graph / social meta tags** — Layout has only `title` + `description`. Shared route links on WhatsApp/Twitter/Facebook show no preview card. Given word-of-mouth growth strategy (PRD target: 15 users via clubs), social sharing previews are critical for virality. The first impression of the app happens OUTSIDE the app — in the messaging app where the link is shared. A blank card next to a Komoot preview card with a map thumbnail looks amateur.

### Solution

Three targeted changes:

1. **slowapi rate limiting + security headers middleware** — Tiered rate limits (per-IP for anonymous, per-user for authenticated). Security headers via FastAPI middleware. CSP tuned for MapLibre.

2. **Colorblind accessibility pass** — Add icon/pattern cues where color is the sole differentiator. Verify heatmap + elevation contrast. No full palette toggle (luminance-based design already handles most cases).

3. **Static Open Graph image + meta tags** — Add og:image (static heatmap screenshot), og:description, og:type, twitter:card to layout metadata. No dynamic per-route images (incompatible with `output: 'export'`).

### Scope

**In Scope:**
- slowapi installation + rate limits on **anonymous endpoints only** (auth, graph)
- 3 security headers middleware: X-Frame-Options, X-Content-Type-Options, HSTS
- Sport `line-dasharray` on discover map route layer (redundant encoding for CVD users)
- **Map legend** — always-visible mini-legend (bottom-left, semi-transparent). Map page: 3 rows (heatmap gradient, DFCI, mes traces). Discover page: 1 row (heatmap gradient). Inline JSX, no shared component. Static — no interaction needed for first-time users to understand colors.
- Static og:image (1200×630, composed) + Twitter Card meta tags in layout.tsx
- pytest tests for rate limiting + security headers
- Playwright check for OG meta tags

**Out of Scope:**
- Redis-backed rate limiting (in-memory fine for single Cloud Run instance MVP)
- Rate limiting on authenticated endpoints (bots can't reach them — defer to post-MVP)
- Content-Security-Policy (high complexity, marginal benefit for static SPA — defer)
- `X-XSS-Protection` (deprecated), `Referrer-Policy`, `Permissions-Policy` (irrelevant)
- Dynamic per-route OG images (incompatible with `output: 'export'`)
- Colorblind palette toggle (unnecessary — luminance ramp + dash patterns suffice)
- Full WCAG 2.1 AA audit
- Cookie consent banner (app uses localStorage only)

## Context for Development

### Codebase Patterns

- Backend middleware registered in `main.py` after `app = FastAPI(...)` (line 605). Current stack: GZipMiddleware (line 612) → CORSMiddleware (line 629). New middleware goes between these.
- All routes use `APIRouter` with prefix/tags, registered via `app.include_router()` (lines 637-654)
- Auth router: `app.include_router(auth_router, prefix="/auth")` (line 638) → endpoints are `/auth/login`, `/auth/register`
- Graph router: `app.include_router(graph_tiles_router)` (line 646) → endpoint is `/routing/graph/{sport}/full.json`
- Routes router: `app.include_router(routes_router)` (line 641) → endpoint is `/routes` (public list)
- Frontend metadata: static `export const metadata: Metadata` in `layout.tsx` (line 6-12)
- Sport colors: centralized in `frontend/lib/constants.ts` — `SPORT_COLORS` (line 2), `SPORT_ICONS` (line 11), `SPORT_LABELS` (line 20)
- Discover map layer: `discover-routes-line` (line 357-370 in discover/page.tsx) — uses `['get', 'color']` from GeoJSON properties
- Sport color in GeoJSON features: set at line 331 `color: SPORT_COLORS[r.sport] ?? '#2d6a4f'`
- RouteCard sport badge: colored pill at line 88-93 of `RouteCard.tsx` — `background: color` with sport text

### Files to Reference

| File | Purpose | Key Lines |
| ---- | ------- | --------- |
| `backend/app/main.py` | Middleware stack, app creation | L605 (app), L612 (GZip), L629 (CORS), L637-654 (routers) |
| `backend/pyproject.toml` | Dependencies | L10-26 (deps list) |
| `backend/app/api/auth.py` | Login/register endpoints | L139 (register), L163 (login) |
| `backend/app/api/graph_tiles.py` | Full graph endpoint | L404 (`/{sport}/full.json`) |
| `backend/tests/test_auth.py` | Auth tests (6 tests) | Existing pattern to follow |
| `frontend/app/layout.tsx` | Root metadata export | L6-12 (metadata object) |
| `frontend/lib/constants.ts` | `SPORT_COLORS`, `SPORT_ICONS`, `SPORT_LABELS` | L2, L11, L20 |
| `frontend/app/discover/page.tsx` | Discover map route layer | L331 (GeoJSON color), L357-370 (addLayer paint) |
| `frontend/components/RouteCard.tsx` | Sport badge pill | L52 (color), L88-93 (badge JSX) |
| `frontend/lib/elevation.ts` | `slopeColor()` — already CVD-safe | L7-15 |

### Technical Decisions

**Rate Limiting:**
- **slowapi** (standard FastAPI rate limiter, wraps `limits` library)
- In-memory storage (default) — acceptable for MVP with min-instances=1 on Cloud Run
- **Limiter initialization** in `main.py` (global):
  ```python
  from slowapi import Limiter, _rate_limit_exceeded_handler
  from slowapi.util import get_remote_address
  from slowapi.errors import RateLimitExceeded

  _rate_limit_enabled = os.environ.get("RATELIMIT_ENABLED", "true").lower() != "false"
  limiter = Limiter(key_func=get_remote_address, enabled=_rate_limit_enabled)
  app.state.limiter = limiter
  ```
- **Custom 429 handler** (slowapi returns plain text by default — must override):
  ```python
  @app.exception_handler(RateLimitExceeded)
  async def rate_limit_handler(request, exc):
      return JSONResponse(
          status_code=429,
          content={"detail": "Trop de requêtes — réessayez dans quelques secondes."},
      )
  ```
- Rate limit **anonymous endpoints only** (bots can't reach authenticated endpoints):
  - `/auth/login`, `/auth/register`: 5/min per IP (credential stuffing protection — bcrypt at 200ms/attempt × 50k = 10,000s CPU)
  - `/routing/graph/{sport}/full.json`: 10/min per IP (500KB payload, GZip CPU cost, Cloud Run billing spike). User persona validated: real users never hit 10 graph reloads/min.
  - `/routes` (public list via `routes_router`): 30/min per IP (lighter payload but still abusable)
- Decorators applied on route functions: `@limiter.limit("5/minute")` on login/register, `@limiter.limit("10/minute")` on full graph, `@limiter.limit("30/minute")` on list_routes
- Key function: `get_remote_address` for all rate-limited endpoints (all anonymous)
- Authenticated endpoint rate limiting deferred — requires JWT parsing in key function, bots don't have tokens
- **Test env**: `RATELIMIT_ENABLED=false` in test suite to avoid flaky tests. One dedicated test enables it explicitly to verify 429 behavior.

**Security Headers (3 only — first principles analysis cut the rest):**
- `X-Frame-Options: DENY` — prevent clickjacking (login form could be iframed)
- `X-Content-Type-Options: nosniff` — prevent MIME confusion (GPX files served)
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — **production only** (HSTS on localhost breaks dev workflow — browser remembers and refuses HTTP)
  ```python
  _is_prod = os.environ.get("ENV", "development") != "development"
  # X-Frame-Options and X-Content-Type-Options: always
  # HSTS: only when _is_prod
  ```
- **CSP deferred** — high complexity to get right with MapLibre (tiles from various CDNs, web workers, blob URLs, inline styles). Marginal benefit for a static SPA with no user-generated HTML. Revisit post-MVP.
- **Skipped:** `X-XSS-Protection` (deprecated), `Referrer-Policy` (no sensitive URL params), `Permissions-Policy` (no camera/mic/geolocation)

**Colorblind Accessibility (first principles: one paint property change):**
- Elevation profile: already luminance-ordered — **no changes needed**
- Heatmap: blue→purple→amber with opacity ramp — **no changes needed** (luminance carries the information, not hue; verified under deuteranopia simulation)
- Sport colors on discover map: **the only real gap** — green (MTB) and amber (gravel) are indistinguishable under deuteranopia. Fix: add distinct `line-dasharray` per sport as redundant encoding channel.
- **Critical: MapLibre `line-dasharray` is a layout property, NOT data-driven.** Cannot use `['match', ['get', 'sport'], ...]`. Must use **5 separate layers** with sport-specific filters sharing one source:
  ```
  discover-routes-road:    filter: ['==', ['get', 'sport'], 'road']     (solid)
  discover-routes-gravel:  filter: ['==', ['get', 'sport'], 'gravel']   dasharray: [8, 4]
  discover-routes-mtb:     filter: ['==', ['get', 'sport'], 'mtb']      dasharray: [2, 4]
  discover-routes-offroad: filter: ['==', ['get', 'sport'], 'offroad']  dasharray: [8, 4, 2, 4]
  discover-routes-running: filter: ['==', ['get', 'sport'], 'running']  dasharray: [4, 4]
  ```
  All 5 share `discover-routes` source. Color still `['get', 'color']`. ~25 lines replaces original 1 layer.
- Sport pills: already have emoji icons — **sufficient for CVD users**
- RouteCard sport badge (L88-93): currently a colored pill with sport text. **Keep the colored background** (sighted users benefit) but **add emoji prefix** inside the pill for CVD redundancy: `🪨 gravel` instead of just `gravel`. The pill is too small for color alone to carry meaning.
- Scope: discover page route layer only. Route editor uses single solid line — no conflict.
- No palette toggle needed. No heatmap changes. ~15 lines of code total.

**Map Legend (reverse-engineered from "user understands every color in 5 seconds"):**
- Target: first-time user builds correct mental model without clicking anything
- **Always visible**, bottom-right, semi-transparent background (rgba), small font (11px)
- **Static content** — no dynamic visibility based on layer toggles (all layers ON by default; users who toggle layers off already understand what they are)
- **Position: bottom-left** (bottom-right conflicts with MapLibre attribution + zoom controls)
- **Map page** — 3 rows:
  - Gradient swatch `linear-gradient(to right, #3b82f6, #a855f7, #f59e0b)` (40×3px) + "Popularité"
  - Red/white dashed swatch (CSS `border-top: 2px dashed #dc2626`) + "Pistes DFCI"
  - Pink/rose swatch (solid 2px line) + "Mes traces"
- **Discover page** — 1 row:
  - Gradient swatch + "Popularité" (routes explained by card list with emoji + sport label, no legend duplication needed)
- **NOT a shared component** — inline JSX per page (~10 lines each). A shared component is overengineered for 3 static lines.
- Sport colors NOT in legend — sport differentiation handled by discover card list (emoji + labels) and dash patterns on map lines. Legend serves layer comprehension, not sport comprehension.

**Open Graph:**
- Static `og:image` — **Paul provides** `og-image.jpg` (dev agent adds meta tags only, cannot take screenshots)
- Size: 1200×630px (Facebook/Twitter/WhatsApp standard)
- Image quality matters: should show vibrant heatmap trails around Montpellier with app name overlay. Paul creates in Figma/Canva from a map screenshot.
- `og:type: website`, `og:locale: fr_FR`
- `og:description`: **action-oriented, not poetic** — 'Planifiez vos sorties gravel et VTT sur les traces de la communauté' (user persona feedback: tells users what they can DO, not what the project IS)
- `twitter:card: summary_large_image`
- All defined in root layout metadata export

## Implementation Plan

### Tasks

Tasks are ordered by dependency (backend first, then frontend, then tests).

---

#### Task 1: Add slowapi dependency

- File: `backend/pyproject.toml`
- Action: Add `"slowapi>=0.1.9"` to `dependencies` list (after `sentry-sdk` line 25)

#### Task 2: Initialize rate limiter + security headers middleware in main.py

- File: `backend/app/main.py`
- Action — imports (top of file, after existing imports ~line 12):
  ```python
  from slowapi import Limiter
  from slowapi.errors import RateLimitExceeded
  from slowapi.util import get_remote_address
  from starlette.responses import JSONResponse
  ```
- Action — limiter init (after `app = FastAPI(...)` at line 605, before GZip middleware):
  ```python
  _rate_limit_enabled = os.environ.get("RATELIMIT_ENABLED", "true").lower() != "false"
  limiter = Limiter(key_func=get_remote_address, enabled=_rate_limit_enabled)
  app.state.limiter = limiter

  @app.exception_handler(RateLimitExceeded)
  async def _rate_limit_handler(request, exc):
      return JSONResponse(status_code=429, content={"detail": "Trop de requêtes — réessayez dans quelques secondes."})
  ```
- Action — security headers middleware (after CORS middleware, ~line 635):
  ```python
  _is_prod = os.environ.get("ENV", "development") != "development"

  @app.middleware("http")
  async def add_security_headers(request, call_next):
      response = await call_next(request)
      response.headers["X-Frame-Options"] = "DENY"
      response.headers["X-Content-Type-Options"] = "nosniff"
      if _is_prod:
          response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
      return response
  ```
- Action — export `limiter` so route files can import it:
  ```python
  # At module level, accessible as `from app.main import limiter`
  ```
- Notes: `limiter` must be defined before routers are included (decorators reference it at import time). The `Request` parameter is required by slowapi decorators — route functions that get rate-limited must accept `request: Request`.

#### Task 3: Add rate limit decorators to auth endpoints

- File: `backend/app/api/auth.py`
- Action — import at top:
  ```python
  from starlette.requests import Request
  from app.main import limiter
  ```
- Action — add decorator + `request` param to `register` (line 139):
  ```python
  @router.post("/register", response_model=LoginResponse, status_code=201)
  @limiter.limit("5/minute")
  async def register(request: Request, body: RegisterRequest, db: ...):
  ```
- Action — add decorator + `request` param to `login` (line 163):
  ```python
  @router.post("/login", response_model=LoginResponse)
  @limiter.limit("5/minute")
  async def login(request: Request, form: ..., db: ...):
  ```
- Notes: `request: Request` must be the first positional parameter for slowapi to extract the IP. Circular import risk: `auth.py` imports from `main.py`. If circular, move limiter to a separate `app/rate_limit.py` module.

#### Task 4: Add rate limit decorator to graph endpoint

- File: `backend/app/api/graph_tiles.py`
- Action — import at top:
  ```python
  from starlette.requests import Request
  from app.main import limiter
  ```
- Action — add decorator + `request` param to `get_full_graph` (line 404):
  ```python
  @router.get("/{sport}/full.json")
  @limiter.limit("10/minute")
  async def get_full_graph(request: Request, sport: str):
  ```

#### Task 5: Add rate limit decorator to public routes list

- File: `backend/app/api/routes.py`
- Action — import at top:
  ```python
  from starlette.requests import Request
  from app.main import limiter
  ```
- Action — add decorator + `request` param to `list_routes` function:
  ```python
  @limiter.limit("30/minute")
  ```
- Notes: Find the `list_routes` function (the one handling `GET /routes`). Add `request: Request` as first param.

#### Task 6: Add RATELIMIT_ENABLED=false to test environment

- File: `docker-compose.yml` (backend service environment)
- Action: Add `RATELIMIT_ENABLED: "false"` to backend environment variables in the test/dev docker-compose
- Notes: Prevents rate limits from interfering with test suite. One dedicated test overrides this.

#### Task 7: Create security + rate limit tests

- File: `backend/tests/test_security.py` (new)
- Action — create test file with:
  ```python
  # test_security_headers: GET /healthz → verify X-Frame-Options: DENY, X-Content-Type-Options: nosniff
  # test_hsts_not_in_dev: GET /healthz with ENV=development → verify no Strict-Transport-Security
  # test_hsts_in_prod: GET /healthz with ENV=production → verify Strict-Transport-Security present
  # test_rate_limit_login: override RATELIMIT_ENABLED=true, POST /auth/login 6 times → 6th returns 429 with French message
  # test_rate_limit_graph: override RATELIMIT_ENABLED=true, GET /routing/graph/road/full.json 11 times → 11th returns 429
  ```
- Notes: Follow `test_auth.py` pattern (TestClient, class-based). For rate limit tests, use `monkeypatch` to set `RATELIMIT_ENABLED=true` and reinitialize limiter, OR create a separate test that imports the app with the env var pre-set.

#### Task 8: Replace single discover route layer with 5 sport-filtered layers

- File: `frontend/app/discover/page.tsx`
- Action — replace the single `discover-routes-line` layer (lines 357-370) with 5 layers:
  ```typescript
  const SPORT_DASH: Record<string, number[] | undefined> = {
    road: undefined,       // solid
    gravel: [8, 4],        // long dash
    mtb: [2, 4],           // dots
    offroad: [8, 4, 2, 4], // dash-dot
    running: [4, 4],       // short dash
  };

  for (const [sport, dash] of Object.entries(SPORT_DASH)) {
    map.addLayer({
      id: `discover-routes-${sport}`,
      type: 'line',
      source: 'discover-routes',
      filter: ['==', ['get', 'sport'], sport],
      paint: {
        'line-color': ['get', 'color'],
        'line-width': 3,
        'line-opacity': 0.8,
      },
      layout: {
        'line-cap': 'round',
        'line-join': 'round',
        ...(dash ? { 'line-dasharray': dash } : {}),
      },
    });
  }
  ```
- Action — also update the highlight layer to sit above all 5 layers (no change needed if it was already added after)
- Action — update click handler: the `queryRenderedFeatures` layers array must include all 5 layer IDs instead of just `'discover-routes-line'`
- Notes: The `discover-routes` source remains unchanged. All 5 layers share it.

#### Task 9: Add emoji prefix to RouteCard sport badge

- File: `frontend/components/RouteCard.tsx`
- Action — change the sport badge text (line 92) from `{route.sport}` to `{icon} {route.sport}`:
  ```tsx
  <span style={{
    padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 700,
    background: color, color: '#fff', textTransform: 'uppercase',
  }}>
    {icon} {route.sport}
  </span>
  ```
- Notes: `icon` is already defined at line 53 as `SPORT_ICONS[route.sport]`. Just reference it in the badge.

#### Task 10: Add constants for sport dash patterns

- File: `frontend/lib/constants.ts`
- Action — add after `SPORT_META` definition:
  ```typescript
  /** Sport → MapLibre line-dasharray for CVD-accessible differentiation. undefined = solid. */
  export const SPORT_DASH: Record<string, number[] | undefined> = {
    road: undefined,
    gravel: [8, 4],
    mtb: [2, 4],
    offroad: [8, 4, 2, 4],
    running: [4, 4],
  };
  ```
- Notes: Centralizes dash patterns for reuse if needed on other pages.

#### Task 11: Add inline map legend to map page

- File: `frontend/app/map/page.tsx`
- Action — add a static positioned legend div (bottom-left of map, above any existing bottom-left elements):
  ```tsx
  {/* Map legend — always visible, no interaction */}
  <div style={{
    position: 'absolute', bottom: 8, left: 8, zIndex: 5,
    background: 'rgba(255,255,255,0.88)', borderRadius: 8,
    padding: '6px 10px', fontSize: 11, color: '#444',
    display: 'flex', flexDirection: 'column', gap: 4,
    backdropFilter: 'blur(4px)', border: '1px solid rgba(0,0,0,0.08)',
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 40, height: 3, borderRadius: 2, background: 'linear-gradient(to right, #3b82f6, #a855f7, #f59e0b)' }} />
      <span>Popularité</span>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 40, height: 0, borderTop: '2px dashed #dc2626' }} />
      <span>Pistes DFCI</span>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 40, height: 3, borderRadius: 2, background: '#e11d48' }} />
      <span>Mes traces</span>
    </div>
  </div>
  ```
- Notes: Position must not overlap with activity detail panel (bottom-left) or sidebar. Check z-index. If activity detail is showing, legend can be hidden or shifted.

#### Task 12: Add inline map legend to discover page

- File: `frontend/app/discover/page.tsx`
- Action — add a 1-row legend inside the map container (bottom-left):
  ```tsx
  <div style={{
    position: 'absolute', bottom: 8, left: 8, zIndex: 5,
    background: 'rgba(255,255,255,0.88)', borderRadius: 8,
    padding: '4px 10px', fontSize: 11, color: '#444',
    display: 'flex', alignItems: 'center', gap: 6,
    backdropFilter: 'blur(4px)', border: '1px solid rgba(0,0,0,0.08)',
  }}>
    <div style={{ width: 40, height: 3, borderRadius: 2, background: 'linear-gradient(to right, #3b82f6, #a855f7, #f59e0b)' }} />
    <span>Popularité</span>
  </div>
  ```

#### Task 13: Add Open Graph meta tags to layout

- File: `frontend/app/layout.tsx`
- Action — expand the `metadata` export:
  ```typescript
  export const metadata: Metadata = {
    title: 'CHEMINS COMMUNS — Common Trails',
    description: 'Planifiez vos sorties gravel et VTT sur les traces de la communauté.',
    icons: {
      icon: `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/icon.svg`,
    },
    openGraph: {
      title: 'CHEMINS COMMUNS — Common Trails',
      description: 'Planifiez vos sorties gravel et VTT sur les traces de la communauté.',
      type: 'website',
      locale: 'fr_FR',
      images: [{
        url: `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/og-image.jpg`,
        width: 1200,
        height: 630,
        alt: 'Chemins Communs — carte communautaire des traces cyclistes',
      }],
    },
    twitter: {
      card: 'summary_large_image',
      title: 'CHEMINS COMMUNS — Common Trails',
      description: 'Planifiez vos sorties gravel et VTT sur les traces de la communauté.',
      images: [`${process.env.NEXT_PUBLIC_BASE_PATH || ''}/og-image.jpg`],
    },
  };
  ```
- Notes: Paul must provide `/public/og-image.jpg` (1200×630px). If image doesn't exist yet, meta tags still work — they'll just show no image until Paul adds it.

#### Task 14: Add OG image placeholder

- File: `frontend/public/og-image.jpg` (new — Paul provides)
- Action: Paul creates a 1200×630px image showing the heatmap around Montpellier with "Chemins Communs" overlay. Place in `frontend/public/`.
- Notes: **Blocker for social sharing previews.** Meta tags in Task 13 reference this file. If Paul hasn't created it yet, add a TODO comment in layout.tsx.

---

### Acceptance Criteria

#### Rate Limiting

- [ ] AC1: Given a client IP, when it sends 6 POST requests to `/auth/login` within 1 minute, then the 6th request returns HTTP 429 with `{"detail": "Trop de requêtes — réessayez dans quelques secondes."}`
- [ ] AC2: Given a client IP, when it sends 11 GET requests to `/routing/graph/road/full.json` within 1 minute, then the 11th request returns HTTP 429 with the same French message
- [ ] AC3: Given a client IP, when it sends 31 GET requests to `/routes` within 1 minute, then the 31st request returns HTTP 429
- [ ] AC4: Given `RATELIMIT_ENABLED=false` in environment, when any number of requests are sent to rate-limited endpoints, then all requests succeed (no 429)
- [ ] AC5: Given the existing test suite runs with `RATELIMIT_ENABLED=false`, when `pytest -q` is executed, then all existing tests pass without 429 interference

#### Security Headers

- [ ] AC6: Given any HTTP request to the API, when the response is received, then it contains `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff`
- [ ] AC7: Given `ENV=production`, when an HTTP request is sent, then the response contains `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- [ ] AC8: Given `ENV=development` (default), when an HTTP request is sent, then the response does NOT contain `Strict-Transport-Security`

#### Colorblind Accessibility

- [ ] AC9: Given the discover page with routes from multiple sports, when viewed under Chrome DevTools deuteranopia simulation, then road/gravel/MTB routes are distinguishable by line pattern (solid vs dashed vs dotted)
- [ ] AC10: Given a RouteCard for a gravel route, when rendered, then the sport badge shows `🪨 gravel` (emoji + text), not just `gravel`
- [ ] AC11: Given the discover map has routes, when clicking on a route line, then the popup appears correctly (click handler queries all 5 sport layer IDs)

#### Map Legend

- [ ] AC12: Given the map page on first load (all layers ON), when the user looks at the bottom-left corner, then a 3-row legend is visible showing Popularité gradient, Pistes DFCI dashed red, Mes traces pink — without any click or hover
- [ ] AC13: Given the discover page, when the map is visible, then a 1-row legend shows the Popularité gradient in the bottom-left corner

#### Open Graph

- [ ] AC14: Given the landing page HTML source, when inspected, then it contains `<meta property="og:image" content="...og-image.jpg">`, `<meta property="og:description" content="Planifiez vos sorties gravel et VTT...">`, and `<meta name="twitter:card" content="summary_large_image">`
- [ ] AC15: Given the layout metadata `description` field, when checked, then it reads `'Planifiez vos sorties gravel et VTT sur les traces de la communauté.'` (action-oriented, not the old poetic version)

## Additional Context

### Dependencies

- `slowapi>=0.1.9` — FastAPI rate limiting (pip install). Wraps the `limits` library. In-memory storage by default.
- `frontend/public/og-image.jpg` — Paul provides manually (1200×630px). Blocker for AC14 visual preview but not for meta tag code.
- No other new dependencies needed.

### Testing Strategy

- **pytest** (`backend/tests/test_security.py` — new):
  - `test_security_headers_present` — AC6
  - `test_hsts_production_only` — AC7, AC8
  - `test_rate_limit_login_429` — AC1 (override `RATELIMIT_ENABLED=true`)
  - `test_rate_limit_graph_429` — AC2 (override `RATELIMIT_ENABLED=true`)
  - `test_rate_limit_disabled_in_test` — AC4, AC5
- **Playwright** (add to existing E2E suite):
  - `test_og_meta_tags` — AC14 (check page source for og:image, og:description, twitter:card)
- **Manual**:
  - Chrome DevTools → Rendering → Emulate vision deficiencies → Deuteranopia — verify AC9
  - WhatsApp link preview test — share URL and verify card shows image + description

### Notes

- **High-risk item**: Circular import between `main.py` (limiter) and route files (import limiter). If this occurs, extract limiter to `app/rate_limit.py` and import from there in both `main.py` and route files.
- **slowapi + Request param**: Rate-limited endpoints must have `request: Request` as first parameter. This is a breaking change to function signatures — verify no other code calls these functions directly.
- **Legend z-index**: Map page bottom-left may conflict with the activity detail panel. Legend should have lower z-index than the panel, or hide when panel is open.
- **HSTS preload**: Not adding `preload` directive — that requires submission to the HSTS preload list and is irreversible. Can be added post-MVP if desired.
- **Future**: CSP header (deferred), Redis-backed rate limiting (when scaling beyond 1 instance), dynamic per-route OG images (requires server-side rendering or Cloud Function).
