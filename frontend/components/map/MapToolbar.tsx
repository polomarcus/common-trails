'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useI18n } from '@/lib/i18n';
import { BASEMAPS, WAYMARKED_LAYERS } from '@/lib/basemap-styles';
import { getToken } from '@/lib/auth';
import PlaceSearch from '@/components/PlaceSearch';
import LanguageToggle from '@/components/LanguageToggle';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';

// ── Types ────────────────────────────────────────────────────────────────────

export interface MapToolbarProps {
  // Layer controls
  layers: LayerToggles;
  setLayers: React.Dispatch<React.SetStateAction<LayerToggles>>;
  waymarked: WaymarkedToggles;
  setWaymarked: React.Dispatch<React.SetStateAction<WaymarkedToggles>>;
  highContrast: boolean;
  setHighContrast: (b: boolean) => void;
  showLayersMenu: boolean;
  setShowLayersMenu: React.Dispatch<React.SetStateAction<boolean>>;
  showMoreLayers: boolean;
  setShowMoreLayers: (v: boolean) => void;
  showBasemapMenu: boolean;
  setShowBasemapMenu: React.Dispatch<React.SetStateAction<boolean>>;
  basemapKey: string;
  setBasemapKey: (k: string) => void;
  layersMenuRef: React.RefObject<HTMLDivElement>;
  basemapMenuRef: React.RefObject<HTMLDivElement>;

  // Heatmap controls
  heatmapSport: string;
  setHeatmapSport: (s: string) => void;
  heatmapDays: number | null;
  setHeatmapDays: (d: number | null) => void;
  dfciCount: number | null;

  // Mode toggles
  exploreMode: boolean;
  setExploreMode: React.Dispatch<React.SetStateAction<boolean>>;

  // User menu
  showUserMenu: boolean;
  setShowUserMenu: React.Dispatch<React.SetStateAction<boolean>>;
  userEmail: string | null;
  stravaName: string | null;
  stravaSyncing: boolean;
  stravaSyncMsg: string | null;
  lastSyncedAt: string | null;
  handleLogout: () => void;
  setShowImportModal: (v: boolean) => void;
  setShowExportModal: (v: boolean) => void;
  activitiesCount: number;

  // Map
  mapInstance: unknown;

  // Locale
  dateLocale: string;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function MapToolbar(props: MapToolbarProps) {
  const { t } = useI18n();
  const router = useRouter();

  const {
    layers, setLayers, waymarked, setWaymarked,
    highContrast, setHighContrast,
    showLayersMenu, setShowLayersMenu, showMoreLayers, setShowMoreLayers,
    basemapKey, setBasemapKey,
    layersMenuRef,
    heatmapSport, setHeatmapSport, heatmapDays, setHeatmapDays, dfciCount,
    exploreMode, setExploreMode,
    showUserMenu, setShowUserMenu, userEmail, stravaName,
    handleLogout, setShowImportModal,
    setShowExportModal,
    activitiesCount, mapInstance, dateLocale,
  } = props;

  // Read auth AFTER mount, not during render. Calling getToken() (reads
  // localStorage) in the JSX below made the account button render differently
  // on the client (logged in) than in the static-export build (logged out) →
  // a hydration text mismatch that crashed /map with React #418 for any
  // logged-in visitor. Null on first paint matches the build; the effect
  // updates it post-hydration.
  const [authToken, setAuthToken] = useState<string | null>(null);
  useEffect(() => { setAuthToken(getToken()); }, []);

  return (
    <nav
      role="navigation"
      aria-label={t('nav.ariaLabel')}
      style={{
        height: 44,
        padding: '0 16px',
        background: '#fff',
        borderBottom: '1px solid #e5e5e5',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        zIndex: 30,
      }}
    >
      <Link href="/" style={{ fontWeight: 700, color: '#2d6a4f', fontSize: 14, textDecoration: 'none', flexShrink: 0, letterSpacing: '-0.01em' }}>
        CHEMINS COMMUNS
      </Link>
      <span style={{ color: '#e0e0e0', fontSize: 14 }}>|</span>

      {/* ── Page nav links (same top-level set as TopNav: Carte) ── */}
      {([
        { href: '/map', label: t('nav.map') },
      ]).map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          style={{
            color: href === '/map' ? '#333' : '#777',
            fontWeight: href === '/map' ? 600 : 400,
            textDecoration: 'none',
            fontSize: 12,
          }}
        >
          {label}
        </Link>
      ))}

      <span style={{ color: '#e0e0e0', fontSize: 14 }}>|</span>

      {/* ── Couches dropdown — single entry that folds in Fond de carte + Exporter ── */}
      <div ref={layersMenuRef} style={{ position: 'relative' }}>
        <button
          data-testid="map-layers-btn"
          onClick={() => setShowLayersMenu(v => !v)}
          style={{
            padding: '3px 10px',
            borderRadius: 5,
            border: `1px solid ${showLayersMenu ? '#2d6a4f' : '#ccc'}`,
            background: showLayersMenu ? '#2d6a4f' : '#fafafa',
            color: showLayersMenu ? '#fff' : '#555',
            cursor: 'pointer',
            fontSize: 12,
            fontWeight: 500,
            display: 'flex', alignItems: 'center', gap: 4,
          }}
        >
          {t('map.layers')}
          <svg width="10" height="6" viewBox="0 0 10 6" fill="none" style={{ transform: showLayersMenu ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
            <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
        {showLayersMenu && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, marginTop: 6,
            background: '#fff', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
            border: '1px solid #e8e8e8', minWidth: 240, maxHeight: '80vh', overflowY: 'auto',
            zIndex: 100, padding: '8px 0',
          }}>
            {/* Essential layer toggles */}
            <div style={{ padding: '4px 8px', fontSize: 10, color: '#aaa', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t('map.layers')}</div>
            {/* Heatmap toggle + inline sport filter */}
            <div>
              <label
                data-testid="toggle-heatmap"
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: '#333',
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
              >
                <input
                  type="checkbox"
                  checked={layers.heatmap}
                  onChange={() => setLayers(prev => ({ ...prev, heatmap: !prev.heatmap }))}
                  style={{ accentColor: '#2d6a4f', width: 16, height: 16, flexShrink: 0 }}
                />
                <span>{t('map.toolbar.heatmapLayer')}</span>
              </label>
              {layers.heatmap && (
                <div style={{ padding: '2px 14px 6px 40px', display: 'flex', gap: 3, flexWrap: 'wrap' }} data-testid="heatmap-sport-filter">
                  {(
                    [
                      { value: 'road', label: t('sport.road') },
                      { value: 'gravel', label: t('sport.gravel') },
                      { value: 'mtb', label: t('sport.mtb') },
                      { value: 'offroad', label: t('map.offroadCombined') },
                      { value: 'running', label: t('map.sportFilter.running') },
                    ] as { value: string; label: string }[]
                  ).map(({ value, label }) => (
                    <button
                      key={value}
                      onClick={() => setHeatmapSport(value)}
                      data-testid={`heatmap-sport-${value}`}
                      style={{
                        padding: '2px 8px', borderRadius: 12, border: 'none',
                        background: heatmapSport === value ? '#e63946' : '#f0f0f0',
                        color: heatmapSport === value ? '#fff' : '#666',
                        cursor: 'pointer', fontSize: 11,
                        fontWeight: heatmapSport === value ? 700 : 400,
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {layers.heatmap && (
                <div style={{ padding: '2px 14px 6px 40px', display: 'flex', gap: 3, flexWrap: 'wrap' }} data-testid="heatmap-time-filter">
                  {(
                    [
                      { value: null, label: t('map.toolbar.timeAll') },
                      { value: 365, label: t('map.toolbar.time1y') },
                      { value: 60, label: t('map.toolbar.time60d') },
                      { value: 30, label: t('map.toolbar.time30d') },
                    ] as { value: number | null; label: string }[]
                  ).map(({ value, label }) => (
                    <button
                      key={String(value)}
                      onClick={() => setHeatmapDays(value)}
                      data-testid={`heatmap-time-${value ?? 'all'}`}
                      style={{
                        padding: '2px 8px', borderRadius: 12, border: 'none',
                        background: heatmapDays === value ? '#e63946' : '#f0f0f0',
                        color: heatmapDays === value ? '#fff' : '#666',
                        cursor: 'pointer', fontSize: 11,
                        fontWeight: heatmapDays === value ? 700 : 400,
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* DFCI + Mes traces toggles */}
            {(
              [
                { key: 'dfci', label: dfciCount ? t('map.dfciTracksCount', { count: dfciCount.toLocaleString(dateLocale) }) : t('map.dfciTracks'), testId: 'toggle-dfci', icon: '🌲' },
                { key: 'myTraces', label: t('map.account.myTraces'), testId: 'toggle-my-traces', icon: '📍' },
              ] as { key: keyof LayerToggles; label: string; testId: string; icon: string }[]
            ).map(({ key, label, testId, icon }) => (
              <label
                key={key}
                data-testid={testId}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: '#333',
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
              >
                <input
                  type="checkbox"
                  checked={layers[key]}
                  onChange={() => setLayers(prev => ({ ...prev, [key]: !prev[key] }))}
                  style={{ accentColor: '#2d6a4f', width: 16, height: 16, flexShrink: 0 }}
                />
                <span>{icon} {label}</span>
              </label>
            ))}

            {/* "Plus de calques" expandable section */}
            <div style={{ borderTop: '1px solid #f0f0f0', margin: '4px 0' }} />
            <button
              onClick={() => {
                const next = !showMoreLayers;
                setShowMoreLayers(next);
                sessionStorage.setItem('showMoreLayers', String(next));
              }}
              data-testid="toggle-more-layers"
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
                padding: '7px 14px', border: 'none', background: 'transparent',
                cursor: 'pointer', fontSize: 12, color: '#888', fontWeight: 500,
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
            >
              <span>{t('map.toolbar.moreLayers')}</span>
              <span style={{ fontSize: 10 }}>{showMoreLayers ? '\u25B4' : '\u25BE'}</span>
            </button>

            {showMoreLayers && (
              <>
                {(
                  [
                    { key: 'myCells', label: t('map.toolbar.cellCoverage'), testId: 'toggle-my-cells', icon: '🔲' },
                    { key: 'hillshade', label: t('map.toolbar.hillshade'), testId: 'toggle-hillshade', icon: '⛰️' },
                    { key: 'photos', label: t('map.toolbar.stravaPhotos'), testId: 'toggle-photos', icon: '📷' },
                  ] as { key: keyof LayerToggles; label: string; testId: string; icon: string }[]
                ).map(({ key, label, testId, icon }) => (
                  <label
                    key={key}
                    data-testid={testId}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: '#333',
                    }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                  >
                    <input
                      type="checkbox"
                      checked={layers[key]}
                      onChange={() => setLayers(prev => ({ ...prev, [key]: !prev[key] }))}
                      style={{ accentColor: '#2d6a4f', width: 16, height: 16, flexShrink: 0 }}
                    />
                    <span>{icon} {label}</span>
                  </label>
                ))}

                {/* Sentiers balisés */}
                <div style={{ borderTop: '1px solid #f0f0f0', margin: '4px 0' }} />
                <div style={{ padding: '4px 8px', fontSize: 10, color: '#aaa', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t('map.toolbar.waymarkedHeader')}</div>
                {WAYMARKED_LAYERS.map(({ key, label }) => (
                  <label
                    key={key}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: '#333',
                    }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                  >
                    <input
                      type="checkbox"
                      checked={waymarked[key]}
                      onChange={() => setWaymarked(prev => ({ ...prev, [key]: !prev[key] }))}
                      style={{ accentColor: '#388e3c', width: 16, height: 16, flexShrink: 0 }}
                    />
                    {label.startsWith('map.') ? t(label) : label}
                  </label>
                ))}
              </>
            )}

            {/* ── Fond de carte section (folded into the single Calques control) ── */}
            <div style={{ borderTop: '1px solid #f0f0f0', margin: '4px 0' }} />
            <div style={{ padding: '4px 8px', fontSize: 10, color: '#aaa', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t('map.toolbar.basemap')}</div>
            <div style={{ padding: '2px 14px 6px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                {BASEMAPS.map(({ key, label, title, thumb }) => (
                  <button
                    key={key}
                    onClick={() => {
                      setBasemapKey(key);
                      if (typeof window !== 'undefined') localStorage.setItem('cc_basemap', key);
                    }}
                    title={title.startsWith('map.') ? t(title) : title}
                    style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
                      padding: 0, border: 'none', background: 'none', cursor: 'pointer',
                    }}
                  >
                    <div style={{
                      width: '100%', aspectRatio: '1', borderRadius: 8, overflow: 'hidden',
                      border: basemapKey === key ? '2.5px solid #2d6a4f' : '2px solid #e0e0e0',
                      boxShadow: basemapKey === key ? '0 0 0 1.5px #2d6a4f' : 'none',
                      transition: 'border 0.12s, box-shadow 0.12s',
                      background: '#e8e8e8',
                    }}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={thumb} alt={label.startsWith('map.') ? t(label) : label} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                    </div>
                    <span style={{
                      fontSize: 10, fontWeight: basemapKey === key ? 700 : 500,
                      color: basemapKey === key ? '#1a4731' : '#666',
                      lineHeight: 1.2, textAlign: 'center',
                    }}>
                      {label.startsWith('map.') ? t(label) : label}
                    </span>
                  </button>
                ))}
              </div>

              {/* High contrast toggle */}
              <div style={{ borderTop: '1px solid #f0f0f0', marginTop: 8, paddingTop: 8 }}>
                <label
                  data-testid="toggle-route-visibility"
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    cursor: 'pointer', fontSize: 11, color: '#555',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={highContrast}
                    onChange={() => {
                      const next = !highContrast;
                      setHighContrast(next);
                      localStorage.setItem('route_visibility', next ? 'high' : 'normal');
                    }}
                    style={{ accentColor: '#2d6a4f', width: 14, height: 14, flexShrink: 0 }}
                  />
                  {t('map.highContrast')}
                </label>
              </div>
            </div>

            {/* ── Exporter section (folded into the single Calques control, PRD #391) ── */}
            <div style={{ borderTop: '1px solid #f0f0f0', margin: '4px 0' }} />
            <div style={{ padding: '4px 8px', fontSize: 10, color: '#aaa', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{t('map.toolbar.exportSection')}</div>
            <button
              type="button"
              onClick={() => { setShowLayersMenu(false); setShowExportModal(true); }}
              data-testid="open-export-heatmap-modal"
              title={t('map.toolbar.exportTitle')}
              aria-label={t('map.toolbar.exportAria')}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                padding: '9px 14px', border: 'none', background: 'transparent',
                cursor: 'pointer', fontSize: 13, color: '#333', textAlign: 'left',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f5f5f5'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
            >
              <span>{t('map.toolbar.exportBtn')}</span>
            </button>
          </div>
        )}
      </div>

      <span style={{ color: '#e0e0e0', fontSize: 14 }}>|</span>

      {/* Explore mode toggle — read-only heatmap inspection */}
      <button
        onClick={() => {
          setExploreMode(v => {
            if (!v) {
              setLayers(prev => ({ ...prev, heatmap: true, myTraces: false }));
            } else {
              // Leaving explore mode — restore traces
              setLayers(prev => ({ ...prev, myTraces: true }));
            }
            return !v;
          });
        }}
        data-testid="toggle-explore-mode"
        style={{
          padding: '3px 10px',
          borderRadius: 5,
          border: `1px solid ${exploreMode ? '#3b82f6' : '#ccc'}`,
          background: exploreMode ? '#3b82f6' : '#fafafa',
          color: exploreMode ? '#fff' : '#3b82f6',
          cursor: 'pointer',
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        {exploreMode ? t('map.toolbar.exploreModeOn') : t('map.toolbar.exploreModeOff')}
      </button>

      {/* ── Place search ── */}
      <PlaceSearch
        onSelect={(lon, lat) => {
          if (mapInstance) (mapInstance as any).flyTo({ center: [lon, lat], zoom: 14, speed: 1.2 });
        }}
        biasCenter={mapInstance ? [(mapInstance as any).getCenter().lng, (mapInstance as any).getCenter().lat] : undefined}
      />

      <div style={{ flex: 1 }} />

      {/* Language toggle */}
      <LanguageToggle />

      {/* User menu */}
      <div style={{ position: 'relative' }}>
        <button
          onClick={() => {
            if (!getToken()) {
              router.push('/?redirect=/map');
            } else {
              setShowUserMenu((v) => !v);
            }
          }}
          data-testid="map-import-btn"
          style={{
            padding: '3px 10px',
            background: showUserMenu ? '#1a4731' : '#2d6a4f',
            color: '#fff',
            border: 'none',
            borderRadius: 5,
            cursor: 'pointer',
            fontSize: 12,
            fontWeight: 500,
            display: 'flex',
            alignItems: 'center',
            gap: 5,
          }}
        >
          👤 {!authToken ? t('map.account.createOrLogin') : stravaName ? t('map.account.accountOf', { name: stravaName.split(' ')[0] }) : userEmail || t('map.menuButton')}
        </button>

        {showUserMenu && authToken && (
          <>
            {/* Backdrop to close menu */}
            <div
              style={{ position: 'fixed', inset: 0, zIndex: 98 }}
              onClick={() => setShowUserMenu(false)}
            />
            <div
              style={{
                position: 'absolute',
                right: 0,
                top: 'calc(100% + 8px)',
                background: '#fff',
                borderRadius: 12,
                boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
                padding: '8px',
                minWidth: 230,
                zIndex: 99,
                display: 'flex',
                flexDirection: 'column',
                gap: 2,
              }}
            >
              {/* Header */}
              {(stravaName || userEmail) && (
                <div style={{ padding: '6px 10px 10px', borderBottom: '1px solid #f0f0f0', marginBottom: 4 }}>
                  <div style={{ fontSize: 11, color: '#aaa', marginBottom: 2 }}>{t('map.account.loggedAs')}</div>
                  <div style={{ fontWeight: 700, color: '#1a4731' }}>{stravaName || userEmail}</div>
                  {userEmail && <div style={{ fontSize: 11, color: '#999', marginTop: 1 }}>{userEmail}</div>}
                </div>
              )}

              {/* Voir mes stats — RESTORED (Paul, 2026-08-08): /stats was
                  rewritten into a clean post-pivot personal dashboard. The
                  "Mes traces" (/stats#mes-traces) entry stays OUT — that
                  section is now masked. */}
              <Link
                href="/stats"
                onClick={() => setShowUserMenu(false)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', background: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 13, textDecoration: 'none', color: 'inherit', width: '100%' }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f9f4')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
              >
                <span style={{ fontSize: 18 }}>📊</span>
                <div>
                  <div style={{ fontWeight: 600, color: '#1a4731' }}>{t('map.account.viewStats')}</div>
                  <div style={{ fontSize: 11, color: '#888' }}>{t('map.sidebarStats')}</div>
                </div>
              </Link>

              <div style={{ height: 1, background: '#f0f0f0', margin: '4px 0' }} />

              {/* Import / Contribute — THE single entry point for feeding the
                  common map (2026-07 compliance pivot: the personal "connect
                  Strava" affordance is gone; the only compliant path is a
                  consented manual upload). Garmin/Komoot/Strava are not separate
                  integrations, just "export from X → upload the GPX/ZIP here";
                  those how-tos live as secondary hints INSIDE the import modal
                  (large Strava archives route to the /strava signed-URL flow). */}
              <button
                data-testid="map-open-import"
                onClick={() => { setShowUserMenu(false); setShowImportModal(true); }}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', background: 'none', border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 13, textAlign: 'left', width: '100%' }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#f5f5f5')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
              >
                <span style={{ fontSize: 18 }}>📥</span>
                <div>
                  <div style={{ fontWeight: 600 }}>{t('map.account.importContribute')}</div>
                  <div style={{ fontSize: 11, color: '#888' }}>{t('map.account.importContributeDesc')}</div>
                </div>
              </button>

              {/* Divider */}
              <div style={{ height: 1, background: '#f0f0f0', margin: '4px 0' }} />

              {/* Admin */}
              <a
                href="/admin"
                onClick={() => setShowUserMenu(false)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', background: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 13, textAlign: 'left', width: '100%', color: '#374151', textDecoration: 'none' }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#f3f4f6')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
              >
                <span style={{ fontSize: 18 }}>⚙️</span>
                <div>
                  <div style={{ fontWeight: 600 }}>{t('map.account.admin')}</div>
                  <div style={{ fontSize: 11, color: '#888' }}>{t('map.account.adminDesc')}</div>
                </div>
              </a>

              {/* Logout */}
              <button
                onClick={() => { setShowUserMenu(false); handleLogout(); }}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', background: 'none', border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 13, textAlign: 'left', width: '100%', color: '#c0392b' }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#fff5f5')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
              >
                <span style={{ fontSize: 18 }}>🚪</span>
                <div style={{ fontWeight: 600 }}>{t('map.account.logout')}</div>
              </button>
            </div>
          </>
        )}
      </div>
    </nav>
  );
}
