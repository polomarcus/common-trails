'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import TopNav from '@/components/TopNav';
import PlaceSearch from '@/components/PlaceSearch';
import EmailLoginForm from '@/components/EmailLoginForm';
import { HERO_MAP_CENTER, HERO_MAP_ZOOM, DFCI_COLOR, DFCI_COLOR_WHITE, DFCI_LINE_WIDTH, DFCI_DASH_ARRAY } from '@/lib/routing-style';
import { API_URL } from '@/lib/api-client';
import { isAuthenticated, setAuthState, clearAuthState } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import { fetchCommunityStats, getPmtilesUrl } from '@/lib/cdn-cache';
import { type CommunityStats, formatStatValue } from '@/lib/community-stats';
import {
  communityTrailsSourceSpec, communityTrailsHeatLayerSpec, communityTrailsLineLayerSpec,
  applyCommunityHeatSportFilter, LINE_CRISP_MINZOOM, COMMUNITY_TRAILS_SOURCE,
} from '@/lib/community-heatmap-layers';

const Map = dynamic(() => import('@/components/Map'), { ssr: false });

// ── Auth Section (slide-in panel) ─────────────────────────────────────────────

function AuthSection({ onLogin }: { onLogin: (token: string) => void }) {
  const { t, locale } = useI18n();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      let resp: Response;
      if (mode === 'register') {
        resp = await fetch(`${API_URL}/auth/register`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ email, password, username }),
        });
      } else {
        const form = new URLSearchParams();
        form.append('username', email);
        form.append('password', password);
        resp = await fetch(`${API_URL}/auth/login`, {
          method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          credentials: 'include',
          body: form.toString(),
        });
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        const detail = data.detail;
        throw new Error(Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg || String(e)).join(', ') : detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setAuthState(data.user_id);
      onLogin(data.user_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      {/* Passwordless magic-link — the primary path (create account or log in). */}
      <div style={{ marginBottom: 16 }}>
        <p style={{ color: '#fff', fontSize: 14, fontWeight: 700, margin: '0 0 2px' }}>{t('emailAuth.title')}</p>
        <p style={{ color: 'rgba(255,255,255,0.45)', fontSize: 12, margin: '0 0 12px' }}>{t('emailAuth.subtitle')}</p>
        <EmailLoginForm variant="dark" />
      </div>
      {/* Legacy email+password login — DEV ONLY. Public auth is magic-link only
          (real accounts are passwordless); the backend /auth/login stays for the
          seeded admin, it is just not surfaced on the public home. */}
      {process.env.NODE_ENV === 'development' && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0' }}>
            <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.12)' }} />
            <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)' }}>{t('common.or')}</span>
            <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.12)' }} />
          </div>
          <div style={{ display: 'flex', gap: 4, marginBottom: 18, background: 'rgba(255,255,255,0.06)', borderRadius: 8, padding: 3 }}>
            {(['login', 'register'] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} data-testid={`mode-${m}`}
                style={{
                  flex: 1, padding: '8px', border: 'none', borderRadius: 6, cursor: 'pointer',
                  fontSize: 13, fontWeight: mode === m ? 700 : 400, transition: 'all 0.15s',
                  background: mode === m ? '#fff' : 'transparent',
                  color: mode === m ? '#1a4731' : 'rgba(255,255,255,0.6)',
                }}>
                {m === 'login' ? t('home.auth.login') : t('home.auth.register')}
              </button>
            ))}
          </div>
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {mode === 'register' && (
              <input type="text" placeholder={t('home.auth.username')} value={username} onChange={(e) => setUsername(e.target.value)}
                data-testid="input-username" required
                style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', fontSize: 13, background: 'rgba(255,255,255,0.08)', color: '#fff', outline: 'none' }} />
            )}
            <input type="email" placeholder={t('home.auth.email')} value={email} onChange={(e) => setEmail(e.target.value)}
              data-testid="input-email" required
              style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', fontSize: 13, background: 'rgba(255,255,255,0.08)', color: '#fff', outline: 'none' }} />
            <input type="password" placeholder={t('home.auth.password')} value={password} onChange={(e) => setPassword(e.target.value)}
              data-testid="input-password" required
              style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', fontSize: 13, background: 'rgba(255,255,255,0.08)', color: '#fff', outline: 'none' }} />
            {error && <p style={{ color: '#ff6b6b', fontSize: 12, margin: 0 }} data-testid="auth-error">{error}</p>}
            <button type="submit" disabled={loading} data-testid="auth-submit"
              style={{
                padding: '11px', border: 'none', borderRadius: 8, cursor: loading ? 'not-allowed' : 'pointer',
                fontWeight: 700, fontSize: 14, marginTop: 2, opacity: loading ? 0.7 : 1,
                background: '#fff', color: '#1a4731',
              }}>
              {loading ? '…' : mode === 'login' ? t('home.auth.loginBtn') : t('home.auth.registerBtn')}
            </button>
          </form>
          <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', marginTop: 8, textAlign: 'center' }}>
            Dev : <code style={{ background: 'rgba(255,255,255,0.1)', padding: '1px 4px', borderRadius: 3 }}>admin@admin</code> / <code style={{ background: 'rgba(255,255,255,0.1)', padding: '1px 4px', borderRadius: 3 }}>admin</code>
          </p>
        </>
      )}
    </div>
  );
}

// ── Sport pills ─────────────────────────────────────────────────────────────

const SPORT_PILLS: { key: string; icon: string; labelKey: string }[] = [
  { key: 'all', icon: '🔥', labelKey: 'common.all' },
  { key: 'road', icon: '🚴', labelKey: 'sport.road' },
  { key: 'gravel', icon: '🪨', labelKey: 'sport.gravel' },
  { key: 'mtb', icon: '⛰️', labelKey: 'sport.mtb' },
  { key: 'offroad', icon: '🌿', labelKey: 'sport.offroad' },
  { key: 'running', icon: '🏃', labelKey: 'sport.running' },
];

// ── Data-export links (step 2) ────────────────────────────────────────────────
// Where a rider grabs their raw archive to contribute. Strava = its official
// bulk-export help page (Settings → Download Request); Garmin = the Manage-your-
// Data export page (locale-aware). GPX needs no link — users already have theirs.
const STRAVA_EXPORT_URL = 'https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export';
const garminExportUrl = (locale: string) =>
  locale === 'fr'
    ? 'https://www.garmin.com/fr-FR/account/datamanagement/exportdata'
    : 'https://www.garmin.com/en-US/account/datamanagement/exportdata';

// Render the step-2 description, substituting the {strava}/{garmin} placeholders
// (kept in the translation string so word order stays translatable) with real
// external links. Everything else renders as plain text.
function renderStep2Desc(desc: string, stravaLabel: string, garminLabel: string, locale: string): React.ReactNode {
  const linkStyle: React.CSSProperties = { color: '#2d6a4f', fontWeight: 700, textDecoration: 'underline' };
  return desc.split(/(\{strava\}|\{garmin\})/g).map((part, i) => {
    if (part === '{strava}') {
      return (
        <a key={i} href={STRAVA_EXPORT_URL} target="_blank" rel="noopener noreferrer" style={linkStyle} data-testid="step2-strava">
          {stravaLabel}
        </a>
      );
    }
    if (part === '{garmin}') {
      return (
        <a key={i} href={garminExportUrl(locale)} target="_blank" rel="noopener noreferrer" style={linkStyle} data-testid="step2-garmin">
          {garminLabel}
        </a>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

// ── Calque overlay card — public discovery of the gpx.studio/VisuGPX layer ────
function CalqueCard() {
  const { t } = useI18n();
  const url = 'https://tiles.chemins-communs.fr/raster/tiles.json';
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(url).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
      () => {},
    );
  };
  return (
    <div style={{
      marginTop: 12, background: '#f4faf7', borderRadius: 16, padding: '20px 24px',
      border: '1px solid #d8e6df',
    }}>
      <h3 style={{ fontSize: 16, fontWeight: 800, color: '#1a4731', margin: '0 0 4px' }}>
        {t('home.calque.title')}
      </h3>
      <p style={{ fontSize: 13, color: '#666', margin: '0 0 10px', lineHeight: 1.5 }}>
        {t('home.calque.intro')}
      </p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'stretch', flexWrap: 'wrap' }}>
        <code style={{
          flex: '1 1 260px', fontSize: 11.5, background: '#fff', border: '1px solid #cfe0d8',
          borderRadius: 8, padding: '8px 10px', wordBreak: 'break-all', color: '#2d6a4f',
        }}>{url}</code>
        <button
          type="button"
          onClick={copy}
          data-testid="home-calque-copy"
          style={{
            flexShrink: 0, fontSize: 13, fontWeight: 700, padding: '0 16px', borderRadius: 8,
            border: 'none', background: '#2d6a4f', color: '#fff', cursor: 'pointer',
          }}
        >{copied ? t('home.calque.copied') : t('home.calque.copy')}</button>
      </div>
    </div>
  );
}

// ── Main Page — Full-screen map hero ──────────────────────────────────────────

export default function HomePage() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const [loggedIn, setLoggedIn] = useState<boolean>(isAuthenticated);
  const [activeSport, setActiveSport] = useState('all');
  // Ref mirror of activeSport so the async PMTiles setup can apply the filter
  // the user selected while the source was still loading.
  const activeSportRef = useRef('all');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  const layersReady = useRef(false);
  const [disconnectedBanner, setDisconnectedBanner] = useState(false);

  // Show disconnected banner + skip auto-redirect when coming from logout
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('disconnected') === '1') {
      setDisconnectedBanner(true);
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // NOTE: since the compliance pivot, the home `/` is the pivot showcase for
  // EVERYONE (hero heatmap-first, contribute CTA, "how it works"). A logged-in
  // visitor arriving on `/` STAYS on the home — they reach /map themselves via
  // the explore-map CTA. (We used to auto-redirect already-logged-in users to
  // /map, which defeated the showcase.)

  // Handle OAuth callbacks
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('strava') === 'connected') {
      router.replace('/strava?status=connected');
      return;
    }
    if (params.get('garmin') === 'connected') {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [router]);

  const handleLogin = () => {
    setLoggedIn(true);
    // Explicit login action → /map by design (only ARRIVING on / already
    // logged-in stays on the home; the login form still goes to the map).
    router.push('/map');
  };
  const handleLogout = async () => {
    try {
      await fetch(`${API_URL}/auth/logout`, { method: 'DELETE', credentials: 'include' });
    } catch { /* best-effort */ }
    clearAuthState();
    setLoggedIn(false);
  };

  // Community stats for the stats banner (contributeurs / traces / km de
  // chemins). Fetched from the pre-built static `stats.json` (DB-free,
  // cold-start-proof — see lib/community-stats.ts + build_pmtiles.py), with a
  // live-API fallback baked into fetchCommunityStats. Stays null → "—" while
  // loading and on error, so the hero never blanks or shifts layout.
  const [communityStats, setCommunityStats] = useState<CommunityStats | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchCommunityStats()
      .then((s) => { if (!cancelled && s) setCommunityStats(s); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Sport chip → layer filter on the static PMTiles layers (no network, no
  // per-visitor DB queries — the live MVT endpoint must NOT be hit from here).
  const applySportFilter = useCallback((sport: string) => {
    const m = mapRef.current;
    if (!m || !layersReady.current) return;
    // Shared SSOT with /map (useMapLayerSync) so both filter identically.
    try { applyCommunityHeatSportFilter(m, sport); } catch { /* silent */ }
  }, []);

  // Load DFCI trails
  const loadDfci = useCallback(async () => {
    const m = mapRef.current;
    if (!m || !m.getSource('dfci-trails')) return;
    try {
      const resp = await fetch(`${API_URL}/heatmap/dfci`);
      if (!resp.ok) return;
      const geojson = await resp.json();
      m.getSource('dfci-trails')?.setData(geojson);
    } catch { /* silent */ }
  }, []);

  // Sport pill click
  const handleSportChange = (sport: string) => {
    setActiveSport(sport);
    activeSportRef.current = sport;
    applySportFilter(sport);
  };

  // Map load callback
  const handleMapLoad = useCallback((mapInstance: unknown) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    mapRef.current = m;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__heroMapInstance = m; // E2E hook (see e2e/tests/home-hero-pmtiles.spec.ts)
    m.scrollZoom.disable();

    // DFCI trails source + layers (red & white)
    m.addSource('dfci-trails', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
    m.addLayer({
      id: 'dfci-trails-casing',
      type: 'line',
      source: 'dfci-trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': DFCI_COLOR_WHITE,
        'line-width': DFCI_LINE_WIDTH + 2,
        'line-opacity': 0.9,
      },
    });
    m.addLayer({
      id: 'dfci-trails-line',
      type: 'line',
      source: 'dfci-trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': DFCI_COLOR,
        'line-width': DFCI_LINE_WIDTH,
        'line-opacity': 0.9,
        'line-dasharray': DFCI_DASH_ARRAY,
      },
    });

    // Community heatmap — the STATIC PMTiles binary, styled as heat LINES like
    // /map (shared specs in lib/community-heatmap-layers.ts). Display doctrine:
    // PMTiles is the PRIMARY display artefact; the live /heatmap/tiles MVT
    // endpoint is a DB-backed fallback that the home (most-visited page) must
    // never hit. If the PMTiles file is unreachable the hero simply stays a
    // plain basemap — no fog, no per-visitor DB load.
    (async () => {
      try {
        const url = await getPmtilesUrl();
        if (!url || mapRef.current !== m || m._removed) return;
        if (m.getSource(COMMUNITY_TRAILS_SOURCE)) return;
        m.addSource(COMMUNITY_TRAILS_SOURCE, communityTrailsSourceSpec(url));
        // Hero background tuning: visible immediately, slightly translucent
        // (it sits behind the title/CTAs + dark gradient). The raster-style
        // DENSITY heatmap (over heat_points) is the hero visual — a clean
        // purple→orange field, no diffuse pâté — from minzoom 6 so the map is
        // never empty at the hero's default z10. The crisp line layer is added
        // from LINE_CRISP_MINZOOM (lowered 14→9 on 2026-08-07): at the hero's
        // default zoom the tippecanoe-thinned raster alone dotted out in sparse
        // areas, so the continuous line draws the corridors from regional zoom.
        // Inserted below the DFCI layers so the red/white DFCI tracks keep
        // rendering on top, like before.
        m.addLayer(communityTrailsHeatLayerSpec({ visible: true, minzoom: 6, opacityMult: 0.9 }),
          'dfci-trails-casing');
        m.addLayer(
          communityTrailsLineLayerSpec({ visible: true, minzoom: LINE_CRISP_MINZOOM, opacityMult: 0.9 }),
          'dfci-trails-casing');
        layersReady.current = true;
        applySportFilter(activeSportRef.current);
      } catch { /* silent — hero keeps the plain basemap */ }
    })();

    loadDfci();
  }, [applySportFilter, loadDfci]);

  return (
    <>
      <style>{`
        @keyframes heroSlideUp {
          from { opacity: 0; transform: translateY(24px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes heroPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(155, 89, 182, 0.4); }
          50% { box-shadow: 0 0 20px 4px rgba(155, 89, 182, 0.15); }
        }
        .hero-pill { transition: all 0.15s ease; }
        .hero-pill:hover { transform: translateY(-1px); }
        .hero-pill-active { background: rgba(255,255,255,0.95) !important; color: #1a1a2e !important; }
        .hero-cta { transition: all 0.2s ease; }
        .hero-cta:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3) !important; }
        input::placeholder { color: rgba(255,255,255,0.35); }
        @media (max-width: 640px) {
          .hero-wrapper {
            height: auto !important;
            min-height: 100vh !important;
            overflow-y: auto !important;
            display: flex !important;
            flex-direction: column !important;
          }
          .hero-map-bg {
            position: relative !important;
            inset: auto !important;
            width: 100% !important;
            height: 45vh !important;
            flex-shrink: 0;
          }
          .hero-gradient {
            background: linear-gradient(to top, rgba(10,15,30,0.95) 0%, rgba(10,15,30,0.4) 60%, transparent 100%) !important;
          }
          .hero-content {
            position: relative !important;
            bottom: auto !important;
            left: auto !important;
            padding: 0 20px 12px !important;
            max-width: 100% !important;
            margin-top: -60px;
            z-index: 8;
          }
          .hero-content h1 { font-size: 26px !important; margin-bottom: 6px !important; }
          .hero-content .hero-tagline { font-size: 13px !important; margin-bottom: 8px !important; }
          .hero-content .hero-subtitle { display: none; }
          .hero-content .hero-search { display: none; }
          .hero-content .hero-badges { display: none; }
          .hero-auth {
            position: relative !important;
            bottom: auto !important;
            right: auto !important;
            width: auto !important;
            max-width: 100% !important;
            border-radius: 12px !important;
            margin: 8px 20px 24px !important;
            padding: 16px !important;
          }
        }
      `}</style>

      {/* Disconnected banner */}
      {disconnectedBanner && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1000,
          background: '#2d6a4f', color: '#fff', textAlign: 'center',
          padding: '10px 40px 10px 16px', fontSize: 14, fontWeight: 500,
        }}>
          {t('home.hero.disconnected')}
          <button
            onClick={() => setDisconnectedBanner(false)}
            style={{
              position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', color: '#fff', fontSize: 18, cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>
      )}

      <div className="hero-wrapper" style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden' }}>

        {/* TopNav (absolute over the map) */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10 }}>
          <TopNav />
        </div>

        {/* Full-screen MapLibre background */}
        <Map
          center={HERO_MAP_CENTER}
          zoom={HERO_MAP_ZOOM}
          className="hero-map-bg"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
          onMapReady={handleMapLoad}
        />

        {/* Dark gradient overlay — bottom-left for content legibility */}
        <div className="hero-gradient" style={{
          position: 'absolute', inset: 0, zIndex: 5,
          background: 'linear-gradient(to top right, rgba(10,15,30,0.95) 0%, rgba(10,15,30,0.85) 30%, rgba(10,15,30,0.5) 55%, rgba(10,15,30,0.15) 75%)',
          pointerEvents: 'none',
        }} />

        {/* ── Bottom-left content overlay ────────────────────────────────── */}
        <div className="hero-content" style={{
          position: 'absolute', bottom: 0, left: 0, zIndex: 8,
          padding: '32px 36px',
          maxWidth: 520,
          animation: 'heroSlideUp 0.8s ease both',
          pointerEvents: 'auto',
        }}>
          {/* Sport filter pills */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
            {SPORT_PILLS.map((s) => (
              <button
                key={s.key}
                onClick={() => handleSportChange(s.key)}
                className={`hero-pill ${activeSport === s.key ? 'hero-pill-active' : ''}`}
                style={{
                  padding: '5px 12px',
                  borderRadius: 20,
                  border: '1px solid rgba(255,255,255,0.25)',
                  background: activeSport === s.key ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.08)',
                  color: activeSport === s.key ? '#1a1a2e' : 'rgba(255,255,255,0.8)',
                  fontSize: 12,
                  fontWeight: activeSport === s.key ? 700 : 500,
                  cursor: 'pointer',
                  backdropFilter: 'blur(8px)',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}
              >
                <span style={{ fontSize: 13 }}>{s.icon}</span> {t(s.labelKey)}
              </button>
            ))}
          </div>

          {/* Title block */}
          <h1 style={{
            fontSize: 'clamp(28px, 5vw, 44px)',
            fontWeight: 900,
            color: '#fff',
            letterSpacing: '-0.02em',
            lineHeight: 1.1,
            margin: '0 0 8px',
            textShadow: '0 2px 20px rgba(0,0,0,0.4)',
          }}>
            Chemins Communs
          </h1>
          <p className="hero-tagline" style={{
            fontSize: 14, color: 'rgba(255,255,255,0.65)', lineHeight: 1.5,
            margin: '0 0 10px', maxWidth: 440,
          }}>
            {t('home.hero.quote')}
          </p>
          <p className="hero-subtitle" style={{
            fontSize: 13, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5,
            margin: '0 0 16px', maxWidth: 440,
          }}>
            {t('home.hero.subtitle')}
          </p>

          {/* Place search */}
          <div className="hero-search" style={{ marginBottom: 16 }}>
            <PlaceSearch
              variant="dark"
              onSelect={(lon, lat) => {
                mapRef.current?.flyTo({ center: [lon, lat], zoom: 14, speed: 1.2 });
              }}
              biasCenter={HERO_MAP_CENTER as [number, number]}
            />
          </div>

          {/* CTA buttons — PRIMARY is the onboarding action (create account +
              import), secondary is browse. The hero heatmap behind is the hook;
              the buttons drive conversion (Paul, 2026-08-08: push signup+import). */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <Link href="/strava" data-testid="go-to-contribute" className="hero-cta" style={{
              padding: '12px 24px',
              background: '#fff',
              color: '#1a1a2e',
              borderRadius: 10,
              fontWeight: 800,
              fontSize: 14,
              textDecoration: 'none',
              boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {t('home.hero.contribute')}
            </Link>
            <Link href="/map" data-testid="go-to-map" className="hero-cta" style={{
              padding: '12px 24px',
              background: 'rgba(255,255,255,0.1)',
              color: '#fff',
              border: '1.5px solid rgba(255,255,255,0.3)',
              borderRadius: 10,
              fontWeight: 700,
              fontSize: 14,
              textDecoration: 'none',
              backdropFilter: 'blur(8px)',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {t('home.hero.exploreMap')}
            </Link>
          </div>


          {/* Logged-in: compact user bar */}
          {loggedIn && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: 'rgba(255,255,255,0.45)' }}>
              <span>{t('home.hero.connected')}</span>
              <span style={{ opacity: 0.3 }}>·</span>
              <button onClick={handleLogout} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.45)', cursor: 'pointer', fontSize: 12, textDecoration: 'underline', padding: 0 }}>
                {t('home.hero.logout')}
              </button>
            </div>
          )}

          {/* ODbL badge */}
          <div className="hero-badges" style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            marginTop: 12,
            padding: '4px 10px',
            background: 'rgba(255,255,255,0.06)',
            borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.1)',
          }}>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>⚖️ ODbL 1.0</span>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>{t('home.badge.anonymized')}</span>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>{t('home.badge.openSource')}</span>
          </div>
        </div>

        {/* ── Auth panel (bottom-right, always visible when not logged in) ── */}
        {!loggedIn && (
          <div className="hero-auth" style={{
            position: 'absolute', bottom: 32, right: 32, zIndex: 8,
            width: 320, maxWidth: '90vw',
            background: 'rgba(15,20,35,0.92)',
            backdropFilter: 'blur(16px)',
            borderRadius: 16,
            padding: '24px',
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 16px 48px rgba(0,0,0,0.4)',
            animation: 'heroSlideUp 0.3s ease both',
            pointerEvents: 'auto',
          }}>
            <div style={{ marginBottom: 14 }}>
              <h3 style={{ fontSize: 16, fontWeight: 800, color: '#fff', margin: '0 0 4px' }}>{t('home.hero.join')}</h3>
              <p style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.55)', margin: 0, lineHeight: 1.5 }}>{t('home.hero.joinSub')}</p>
            </div>
            <AuthSection onLogin={handleLogin} />
          </div>
        )}
      </div>

      {/* ── Section 1: Comment ça marche — 3-step + routing principles ─ */}
      <section style={{ background: '#fff', padding: '72px 24px 64px', borderTop: '1px solid #e0e0e0' }}>
        <div style={{ maxWidth: 960, margin: '0 auto' }}>
          <h2 style={{ fontSize: 26, fontWeight: 800, color: '#1a4731', textAlign: 'center', margin: '0 0 12px' }}>
            {t('home.section.howItWorks')}
          </h2>
          <p style={{ fontSize: 14, color: '#888', textAlign: 'center', margin: '0 0 36px', maxWidth: 520, marginLeft: 'auto', marginRight: 'auto' }}>
            {t('home.section.howItWorksSub')}
          </p>

          {/* 3-step flow — Paul's narrative (2026-08-07): profitez d'une heatmap
              → exportez vos données (Strava/Garmin/GPX) → construisez de
              meilleures traces. Step 2 renders real export links (see
              renderStep2Desc: splits the {strava}/{garmin} placeholders). */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 32, marginBottom: 48 }}>
            {[
              { n: '1', title: t('home.step1.title'), desc: t('home.step1.desc') as React.ReactNode },
              { n: '2', title: t('home.step2.title'), desc: renderStep2Desc(t('home.step2.desc'), t('home.step2.strava'), t('home.step2.garmin'), locale) },
              { n: '3', title: t('home.step3.title'), desc: t('home.step3.desc') as React.ReactNode },
            ].map((step) => (
              <div key={step.n} style={{ textAlign: 'center' }}>
                <div style={{
                  width: 48, height: 48, borderRadius: '50%', background: '#2d6a4f', color: '#fff',
                  fontSize: 22, fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  margin: '0 auto 16px',
                }}>
                  {step.n}
                </div>
                <h3 style={{ fontSize: 17, fontWeight: 700, color: '#1a4731', margin: '0 0 6px' }}>{step.title}</h3>
                <p style={{ fontSize: 14, color: '#666', lineHeight: 1.5, margin: 0 }}>{step.desc}</p>
              </div>
            ))}
          </div>

          {/* Onboarding conversion CTA — after the "how it works" explanation,
              give the clear action (Paul, 2026-08-08: push account + import). */}
          <div style={{ textAlign: 'center', marginBottom: 40 }}>
            <p style={{ fontSize: 15, color: '#555', margin: '0 0 14px', fontWeight: 600 }}>
              {t('home.onboarding.ctaTitle')}
            </p>
            <Link href="/strava" data-testid="onboarding-cta" style={{
              display: 'inline-flex', alignItems: 'center', gap: 8,
              padding: '14px 30px', background: '#2d6a4f', color: '#fff',
              borderRadius: 12, textDecoration: 'none', fontSize: 15, fontWeight: 800,
              boxShadow: '0 6px 20px rgba(45,106,79,0.28)',
            }}>
              {t('home.onboarding.cta')}
            </Link>
            <p style={{ fontSize: 12.5, color: '#999', margin: '10px 0 0' }}>
              {t('home.onboarding.ctaSub')}
            </p>
          </div>

          {/* (The old "bonus: heatmap-guided routing" card was removed 2026-08-07
              — internal routing is decommissioned; the product is the heatmap +
              a trace source you export into real routers, which the calque card
              below now conveys.) */}

          {/* Calque — public discovery of the raster overlay (gpx.studio/VisuGPX).
              Mirrors the members-only export modal's calque box, but on the
              public home so anyone can grab the tiles URL. */}
          <CalqueCard />
        </div>
      </section>

      {/* ── Section 2: Feature highlights — alternating rows ──────── */}
      <section style={{ background: '#f8faf9', padding: '64px 24px', borderTop: '1px solid #e8ede9' }}>
        <div style={{ maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 56 }}>

          {/* Row A — Heatmap communautaire */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 40, alignItems: 'center' }}>
            <div>
              <h3 style={{ fontSize: 22, fontWeight: 800, color: '#1a4731', margin: '0 0 10px' }}>
                {t('home.community.title')}
              </h3>
              {t('home.community.text').split('\n\n').map((para, i) => (
                <p key={i} style={{ fontSize: 14, color: '#555', lineHeight: 1.7, margin: i === 0 ? '0 0 12px' : 0 }}>
                  {para}
                </p>
              ))}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {/* Gradient line visual — popularity colors */}
              <svg viewBox="0 0 320 80" width="100%" style={{ maxWidth: 320 }}>
                <defs>
                  {/* Matches the live heatmap ramp (lib/community-heatmap-layers.ts):
                      dark plum (rare) → hot pink → orange (very popular). */}
                  <linearGradient id="heat-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#7a2058" />
                    <stop offset="25%" stopColor="#a83275" />
                    <stop offset="50%" stopColor="#d63384" />
                    <stop offset="75%" stopColor="#f06595" />
                    <stop offset="100%" stopColor="#ff8c42" />
                  </linearGradient>
                </defs>
                <path d="M 20 55 Q 80 20, 160 40 T 300 30" fill="none" stroke="url(#heat-grad)" strokeWidth="5" strokeLinecap="round" />
                <path d="M 30 65 Q 100 45, 180 55 T 290 45" fill="none" stroke="url(#heat-grad)" strokeWidth="3" strokeLinecap="round" opacity="0.5" />
                <path d="M 50 35 Q 120 55, 200 30 T 310 50" fill="none" stroke="url(#heat-grad)" strokeWidth="2" strokeLinecap="round" opacity="0.3" />
              </svg>
            </div>
          </div>

          {/* Row B — Profil d'élévation & surfaces (reversed) */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 40, alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', order: 0 }}>
              {/* Slope color bars visual */}
              <svg viewBox="0 0 320 80" width="100%" style={{ maxWidth: 320 }}>
                {/* Elevation profile silhouette */}
                <path d="M 10 70 L 40 55 L 80 45 L 120 50 L 160 30 L 200 25 L 230 35 L 260 20 L 290 40 L 310 60 L 310 70 Z"
                  fill="#e8f5e9" stroke="none" />
                {/* Slope-colored segments */}
                <line x1="10" y1="70" x2="40" y2="55" stroke="#4caf50" strokeWidth="3" strokeLinecap="round" />
                <line x1="40" y1="55" x2="80" y2="45" stroke="#4caf50" strokeWidth="3" strokeLinecap="round" />
                <line x1="80" y1="45" x2="120" y2="50" stroke="#8bc34a" strokeWidth="3" strokeLinecap="round" />
                <line x1="120" y1="50" x2="160" y2="30" stroke="#ff9800" strokeWidth="3" strokeLinecap="round" />
                <line x1="160" y1="30" x2="200" y2="25" stroke="#ff5722" strokeWidth="3" strokeLinecap="round" />
                <line x1="200" y1="25" x2="230" y2="35" stroke="#8bc34a" strokeWidth="3" strokeLinecap="round" />
                <line x1="230" y1="35" x2="260" y2="20" stroke="#e91e63" strokeWidth="3" strokeLinecap="round" />
                <line x1="260" y1="20" x2="290" y2="40" stroke="#4caf50" strokeWidth="3" strokeLinecap="round" />
                <line x1="290" y1="40" x2="310" y2="60" stroke="#4caf50" strokeWidth="3" strokeLinecap="round" />
              </svg>
            </div>
            <div style={{ order: 1 }}>
              <h3 style={{ fontSize: 22, fontWeight: 800, color: '#1a4731', margin: '0 0 10px' }}>{t('home.elevation.title')}</h3>
              <p style={{ fontSize: 14, color: '#555', lineHeight: 1.7, margin: 0 }}>
                {t('home.elevation.desc')}
              </p>
            </div>
          </div>

          {/* Row C — DFCI + sentiers balisés */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 40, alignItems: 'center' }}>
            <div>
              <h3 style={{ fontSize: 22, fontWeight: 800, color: '#1a4731', margin: '0 0 10px' }}>{t('home.trails.title')}</h3>
              <p style={{ fontSize: 14, color: '#555', lineHeight: 1.7, margin: 0 }}>
                {t('home.trails.desc')}
              </p>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {/* DFCI red-white dashed + GR red-white solid */}
              <svg viewBox="0 0 350 80" width="100%" style={{ maxWidth: 350 }}>
                {/* DFCI line (red-white dashed) */}
                <line x1="20" y1="30" x2="300" y2="30" stroke="#fff" strokeWidth="5" strokeLinecap="round" />
                <line x1="20" y1="30" x2="300" y2="30" stroke="#c0392b" strokeWidth="3" strokeLinecap="round" strokeDasharray="12,6" />
                <text x="310" y="34" fontSize="10" fill="#888" fontWeight="600">DFCI</text>
                {/* GR line (red-white solid) */}
                <line x1="20" y1="55" x2="300" y2="55" stroke="#fff" strokeWidth="5" strokeLinecap="round" />
                <line x1="20" y1="55" x2="300" y2="55" stroke="#c0392b" strokeWidth="3" strokeLinecap="round" />
                <text x="310" y="59" fontSize="10" fill="#888" fontWeight="600">GR</text>
              </svg>
            </div>
          </div>

        </div>
      </section>

      {/* ── Section 3: Stats banner ──────────────────────────────── */}
      <section style={{ background: '#1a4731', padding: '40px 24px' }}>
        <div style={{
          maxWidth: 720, margin: '0 auto',
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24, textAlign: 'center',
        }}>
          {[
            { value: formatStatValue(communityStats?.contributors, locale), label: t('home.stats.contributors') },
            { value: formatStatValue(communityStats?.traces, locale), label: t('home.stats.traces') },
            { value: formatStatValue(communityStats?.km, locale), label: t('home.stats.km') },
          ].map((s) => (
            <div key={s.label}>
              <div style={{ fontSize: 36, fontWeight: 900, color: '#fff', lineHeight: 1.1 }}>{s.value}</div>
              <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.6)', marginTop: 4 }}>{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Section 4: Philosophy + CTA ──────────────────────────── */}
      <section style={{ background: '#14352a', padding: '56px 24px', color: '#fff' }}>
        <div style={{ maxWidth: 700, margin: '0 auto', textAlign: 'center' }}>
          <h2 style={{ fontSize: 22, fontWeight: 800, margin: '0 0 10px' }}>{t('home.philosophy.title')}</h2>
          <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.8)', lineHeight: 1.7, margin: '0 0 28px' }}>
            {t('home.openSource.text')}
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
            <Link href="/strava" data-testid="contribute-footer-cta" style={{
              padding: '13px 28px', background: '#fff', color: '#1a4731',
              borderRadius: 10, textDecoration: 'none', fontSize: 15, fontWeight: 800,
              boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
            }}>
              {t('home.hero.contribute')}
            </Link>
            <Link href="/map" style={{
              padding: '12px 20px', background: 'rgba(255,255,255,0.1)', color: '#fff',
              borderRadius: 8, textDecoration: 'none', fontSize: 13, fontWeight: 600,
              border: '1px solid rgba(255,255,255,0.2)',
            }}>
              {t('home.hero.exploreMap')}
            </Link>
            <Link href="/methode" style={{
              padding: '12px 20px', background: 'rgba(255,255,255,0.1)', color: '#fff',
              borderRadius: 8, textDecoration: 'none', fontSize: 13, fontWeight: 600,
              border: '1px solid rgba(255,255,255,0.2)',
            }}>
              {t('home.philosophy.method')}
            </Link>
            <a href="https://github.com/polomarcus/common-trails" target="_blank" rel="noopener noreferrer" style={{
              padding: '12px 20px', background: 'rgba(255,255,255,0.1)', color: '#fff',
              borderRadius: 8, textDecoration: 'none', fontSize: 13, fontWeight: 600,
              border: '1px solid rgba(255,255,255,0.2)',
            }}>
              GitHub
            </a>
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '5px 12px', background: 'rgba(255,255,255,0.08)', borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.15)', fontSize: 11, color: 'rgba(255,255,255,0.45)',
          }}>
            {t('home.footer.license')}
          </div>
        </div>
      </section>
    </>
  );
}
