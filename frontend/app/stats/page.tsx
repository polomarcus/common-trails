'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import TopNav from '@/components/TopNav';
import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';
import { fmtKm, fmtElevFull } from '@/lib/format';
import { SPORT_META, SPORTS } from '@/lib/constants';
import { useI18n } from '@/lib/i18n';

// ── Types ─────────────────────────────────────────────────────────────────────
//
// Post-pivot personal dashboard (Paul, 2026-08-08). Everything here is
// activity-based and survives the raw-trace pivot. The matched-era bits — the
// community heatmap summary, the per-cell "unique_cells" metric, and the
// "Mes traces importées" list — were REMOVED: they read dead/empty in raw mode
// (community heatmap total_edges:0, unique_cells not written) and made the page
// look broken. The page is now: summary cards + Personal Bests / Records +
// Sport breakdown, fed only by /me/stats_by_sport and /me/personal-bests.

interface StatsData {
  activity_count: number;
  total_distance_m: number;
  total_elevation_gain_m: number;
  sport: string | null;
}

interface PersonalBestRecord {
  label: string;
  sport: string;
  value: number;
  unit: string;
  formatted: string;
  category: string;
  activity_id: string | null;
}

interface PersonalBests {
  total_activities: number;
  total_distance_km: number;
  best_year: { year: number; distance_km: number } | null;
  best_month: { year: number; month: number; month_name: string; distance_km: number } | null;
  records: PersonalBestRecord[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtElev(m?: number, locale: string = 'fr-FR'): string {
  return fmtElevFull(m, locale);
}

// ── Fun distance comparisons ───────────────────────────────────────────────────

interface FunRef { dist: number; label: string; emoji: string }
const FUN_REFS: FunRef[] = [
  { dist: 583,    label: 'Montpellier–Lille',     emoji: '🚂' },
  { dist: 1166,   label: 'A/R Montpellier–Lille', emoji: '🔄' },
  { dist: 3500,   label: 'Tours de France',        emoji: '🏁' },
  { dist: 40075,  label: 'tours de la Terre',      emoji: '🌍' },
  { dist: 384400, label: 'fois la Lune',             emoji: '🌕' },
];

function funComparison(distM: number): string {
  const km = distM / 1000;
  for (const ref of FUN_REFS) {
    const ratio = km / ref.dist;
    if (ratio >= 1.5 && ratio <= 500) {
      return `${ratio.toFixed(0)}× ${ref.label} ${ref.emoji}`;
    }
  }
  return '';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({
  value,
  label,
  icon,
  sub,
}: {
  value: string;
  label: string;
  icon: string;
  sub?: string;
}) {
  return (
    <div
      style={{
        background: '#fff',
        borderRadius: 14,
        padding: '20px 24px',
        boxShadow: '0 2px 10px rgba(0,0,0,0.07)',
        flex: 1,
        minWidth: 150,
      }}
    >
      <div style={{ fontSize: 28, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontSize: 28, fontWeight: 800, color: '#1a1a1a', lineHeight: 1.1 }}>
        {value}
      </div>
      <div style={{ fontSize: 13, color: '#777', marginTop: 4, fontWeight: 500 }}>
        {label}
      </div>
      {sub && <div style={{ fontSize: 12, color: '#aaa', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function SportRow({
  sport,
  stats,
  maxDist,
}: {
  sport: string;
  stats: StatsData;
  maxDist: number;
}) {
  const meta = SPORT_META[sport] ?? { label: sport, icon: '🏅', color: '#888' };
  const pct = maxDist > 0 ? (stats.total_distance_m / maxDist) * 100 : 0;

  return (
    <div
      style={{
        background: '#fff',
        borderRadius: 10,
        padding: '14px 18px',
        boxShadow: '0 1px 5px rgba(0,0,0,0.06)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 20 }}>{meta.icon}</span>
        <span style={{ fontWeight: 700, fontSize: 15 }}>{meta.label}</span>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 13,
            fontWeight: 600,
            color: meta.color,
          }}
        >
          {stats.activity_count} activité{stats.activity_count !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Progress bar */}
      <div
        style={{
          height: 6,
          background: '#f0f0f0',
          borderRadius: 3,
          overflow: 'hidden',
          marginBottom: 8,
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            background: meta.color,
            borderRadius: 3,
            transition: 'width 0.6s ease',
          }}
        />
      </div>

      <div style={{ display: 'flex', gap: 20, fontSize: 13, color: '#555' }}>
        <span>📏 {fmtKm(stats.total_distance_m)}</span>
        <span>⛰️ {fmtElev(stats.total_elevation_gain_m)} D+</span>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function StatsPage() {
  const { t, locale } = useI18n();
  const [token] = useState<string | null>(getToken);
  const [totals, setTotals] = useState<StatsData | null>(null);
  const [bySport, setBySport] = useState<Record<string, StatsData>>({});
  const [personalBests, setPersonalBests] = useState<PersonalBests | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-section loading states — each fetch settles independently so a
  // slow endpoint never blocks the rest of the page (Promise.allSettled).
  const [statsLoading, setStatsLoading] = useState(true);
  const [personalBestsLoading, setPersonalBestsLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    if (!token) {
      setLoading(false);
      setStatsLoading(false);
      setPersonalBestsLoading(false);
      return;
    }
    setLoading(true);
    setStatsLoading(true);
    setPersonalBestsLoading(true);
    setError(null);

    // One round-trip for totals + per-sport via GROUP BY on the backend
    // (replaces the 6 individual /me/stats?sport=X calls).
    const statsPromise = fetch(`${API_URL}/me/stats_by_sport`, {
      credentials: 'include',
    }).then(async (resp) => {
      if (!resp.ok) throw new Error('stats_by_sport failed');
      const data = await resp.json();
      setTotals({
        activity_count: data.total.activity_count,
        total_distance_m: data.total.total_distance_m,
        total_elevation_gain_m: data.total.total_elevation_gain_m,
        sport: null,
      });
      const sportData: Record<string, StatsData> = {};
      for (const s of SPORTS) {
        const block = data[s];
        if (block) {
          sportData[s] = {
            activity_count: block.activity_count,
            total_distance_m: block.total_distance_m,
            total_elevation_gain_m: block.total_elevation_gain_m,
            sport: s,
          };
        }
      }
      setBySport(sportData);
    }).finally(() => setStatsLoading(false));

    const pbPromise = fetch(`${API_URL}/me/personal-bests`, {
      credentials: 'include',
    }).then(async (resp) => {
      if (!resp.ok) throw new Error('personal-bests failed');
      setPersonalBests(await resp.json());
    }).finally(() => setPersonalBestsLoading(false));

    // allSettled so one slow endpoint never blocks the page render
    const results = await Promise.allSettled([statsPromise, pbPromise]);
    if (results.every((r) => r.status === 'rejected')) {
      setError('Impossible de charger les statistiques.');
    }
    setLoading(false);
  }, [token]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  if (!token) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <p style={{ fontSize: 18, color: '#555' }}>{t('stats.loginRequired')}</p>
        <Link href="/" style={{ color: '#2d6a4f', fontWeight: 600 }}>← Accueil</Link>
      </div>
    );
  }

  // Max distance across sports (for bar scaling)
  const maxDist = Math.max(...Object.values(bySport).map((s) => s.total_distance_m), 1);

  // Active sports (at least 1 activity)
  const activeSports = SPORTS.filter((s) => (bySport[s]?.activity_count ?? 0) > 0);

  return (
    <div style={{ minHeight: '100vh', background: '#f5f5f0' }}>
      <TopNav activeHref="/stats" />

      <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
        {/* Only show the top spinner while the stats cards are still loading.
            Personal-bests has its own granular loader below so a slow endpoint
            never blocks the first paint. */}
        {statsLoading && !totals && (
          <div style={{ textAlign: 'center', padding: 60, color: '#aaa', fontSize: 18 }}>
            Chargement…
          </div>
        )}

        {error && (
          <div style={{ padding: '12px 16px', background: '#ffeaea', borderRadius: 8, color: '#c0392b', marginBottom: 16 }}>
            {error}
          </div>
        )}

        {!loading && totals && (
          <>
            {/* ── Global summary cards ── */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 24 }}>
              <StatCard
                icon="📏"
                value={fmtKm(totals.total_distance_m)}
                label={t('stats.totalDistance')}
                sub={funComparison(totals.total_distance_m) || `${totals.activity_count} activité${totals.activity_count !== 1 ? 's' : ''}`}
              />
              <StatCard
                icon="⛰️"
                value={fmtElev(totals.total_elevation_gain_m)}
                label={t('stats.elevGain')}
              />
              <StatCard
                icon="🏅"
                value={String(activeSports.length)}
                label={t('stats.sportsPracticed')}
              />
            </div>

            {/* ── Personal Bests + Records ── */}
            {personalBestsLoading && !personalBests && (
              <div style={{
                background: '#fff', borderRadius: 14, padding: '14px 22px',
                marginBottom: 24, boxShadow: '0 2px 10px rgba(0,0,0,0.07)',
                color: '#aaa', fontSize: 13,
              }}>
                Chargement des records personnels…
              </div>
            )}
            {personalBests && personalBests.total_activities > 0 && (
              <div
                style={{
                  background: '#fff',
                  borderRadius: 14,
                  padding: '18px 22px',
                  marginBottom: 24,
                  boxShadow: '0 2px 10px rgba(0,0,0,0.07)',
                }}
              >
                <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 14, color: '#333' }}>
                  {t('stats.personalRecords')}
                </h2>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 14, color: '#444' }}>
                  <div>
                    <strong>{personalBests.total_activities}</strong> {t('stats.activitiesForTotal')}{' '}
                    <strong>{personalBests.total_distance_km.toLocaleString(locale === 'en' ? 'en-GB' : 'fr-FR', { maximumFractionDigits: 1 })} km</strong>
                  </div>
                  {personalBests.best_year && (
                    <div>
                      <strong>{personalBests.best_year.year}</strong> {t('stats.bestYear')}{' '}
                      <strong>{personalBests.best_year.distance_km.toLocaleString(locale === 'en' ? 'en-GB' : 'fr-FR', { maximumFractionDigits: 0 })} km</strong>
                    </div>
                  )}
                  {personalBests.best_month && (
                    <div>
                      <strong>{personalBests.best_month.month_name} {personalBests.best_month.year}</strong> {t('stats.bestMonth')}{' '}
                      <strong>{personalBests.best_month.distance_km.toLocaleString(locale === 'en' ? 'en-GB' : 'fr-FR', { maximumFractionDigits: 0 })} km</strong>
                    </div>
                  )}
                  {personalBests.records.map((rec, i) => {
                    const meta = SPORT_META[rec.sport] ?? { icon: '', label: rec.sport, color: '#888' };
                    const sportLabel = meta.label;
                    const inner = (() => {
                      if (rec.category === 'longest') {
                        return (
                          <>
                            <strong>&laquo;{rec.label}&raquo;</strong> est votre plus long{' '}
                            {sportLabel} avec <strong>{rec.formatted}</strong>
                          </>
                        );
                      }
                      if (rec.category === 'most_elevation') {
                        return (
                          <>
                            <strong>&laquo;{rec.label}&raquo;</strong> est votre {sportLabel} avec le plus de D+ :{' '}
                            <strong>{rec.formatted}</strong>
                          </>
                        );
                      }
                      if (rec.category === 'fastest') {
                        return (
                          <>
                            <strong>&laquo;{rec.label}&raquo;</strong> est votre meilleur {sportLabel} avec une moyenne de{' '}
                            <strong>{rec.formatted}</strong>
                          </>
                        );
                      }
                      return <>{rec.label} — {rec.formatted}</>;
                    })();
                    return (
                      <div key={`${rec.category}-${rec.sport}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 16 }}>{meta.icon}</span>
                        <span style={{ flex: 1 }}>
                          {rec.activity_id ? (
                            <Link
                              href={`/map?activity=${rec.activity_id}`}
                              style={{ color: 'inherit', textDecoration: 'none', borderBottom: '1px dashed #ccc' }}
                              title="Voir sur la carte"
                            >
                              {inner}
                            </Link>
                          ) : (
                            inner
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* ── Sport breakdown ── */}
            {activeSports.length > 0 && (
              <div style={{ marginBottom: 24 }}>
                <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12, color: '#333' }}>
                  Par sport
                </h2>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {activeSports.map((s) => (
                    <SportRow
                      key={s}
                      sport={s}
                      stats={bySport[s]}
                      maxDist={maxDist}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
