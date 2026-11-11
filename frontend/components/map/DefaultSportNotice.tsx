'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getToken } from '@/lib/auth';
import { SPORT_COLORS } from '@/lib/constants';
import {
  BULK_SPORT_MAX_IDS, DEFAULT_SPORT_CHOICES, fetchDefaultSport, tryFetchDefaultSport, reclassifyDefaultSport,
  type DefaultSportChange, type DefaultSportChoice, type DefaultSportStatus,
} from '@/lib/default-sport';
import { formatDist, fmtDate } from '@/lib/format';
import { useT } from '@/lib/i18n';

interface DefaultSportNoticeProps {
  dateLocale: string;
  /** Bump to re-read the count (e.g. a sport was changed from the activity card). */
  refreshKey?: number;
  /** A bulk change was applied: lets the page sync its list + map source. */
  onChanged?: (change: DefaultSportChange) => void;
}

/**
 * Signed-in rider whose import left bike rides on "Route" BY DEFAULT (the
 * Strava export only says "Ride"): a discreet, dismissible notice + a small
 * panel to re-classify them in bulk, or confirm they are road rides.
 *
 * Auth is read AFTER mount (static export built logged out — see
 * MapActivitiesNotice). Dismissal lasts for the visit: the notice comes back
 * while there are rides left to classify.
 */
export default function DefaultSportNotice({ dateLocale, refreshKey = 0, onChanged }: DefaultSportNoticeProps) {
  const t = useT();
  const [status, setStatus] = useState<DefaultSportStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [all, setAll] = useState(false);
  const [sport, setSport] = useState<DefaultSportChoice>('gravel');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const reload = useCallback(async () => {
    if (getToken() === null) return;
    setStatus(await fetchDefaultSport());
  }, []);
  useEffect(() => { void reload(); }, [reload, refreshKey]);

  // Basic dialog focus management: focus the panel when it opens (so Escape
  // and Tab start inside it), give focus back to the opener when it closes.
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const openButtonRef = useRef<HTMLButtonElement | null>(null);
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (open) dialogRef.current?.focus();
    else if (wasOpenRef.current) openButtonRef.current?.focus();
    wasOpenRef.current = open;
  }, [open]);

  const count = status?.count ?? 0;
  if (count === 0) return null;
  const listed = status!.activities;
  const others = count - listed.length;
  const picked = all ? count : selected.size;

  const run = async (target: string[] | 'all', to: DefaultSportChoice) => {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      const res = await reclassifyDefaultSport(target, to);
      onChanged?.({ sport: res.sport, updatedIds: res.updated_ids, cleared: target });
      setSelected(new Set());
      setAll(false);
      const next = await fetchDefaultSport();
      setStatus(next);
      if (next.count === 0) setOpen(false);
    } catch {
      setError(true);
      // The request may have been partly applied (or the list is out of
      // date): show what the server has now rather than a stale count — but
      // ONLY if that read succeeded. A failed refetch must not read as "zero
      // left": the panel, the notice and this error would all vanish and the
      // rider would believe everything was classified.
      const now = await tryFetchDefaultSport();
      if (now) setStatus(now);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) => {
    setAll(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const btn: React.CSSProperties = {
    padding: '6px 12px', borderRadius: 8, border: 'none', fontWeight: 700, fontSize: 12,
    cursor: busy ? 'wait' : 'pointer', whiteSpace: 'nowrap',
  };

  return (
    <>
      {!dismissed && !open && (
        <div
          data-testid="default-sport-notice"
          role="status"
          style={{
            // Below the empty-state card slot, clear of MapLibre's top-right controls.
            position: 'absolute', top: 12, left: 12, zIndex: 24,
            background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb',
            boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: '8px 10px 8px 14px',
            maxWidth: 'min(420px, calc(100% - 68px))',
            display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
            fontSize: 12, color: '#1f2937',
          }}
        >
          <span style={{ flex: '1 1 200px' }}>
            {t(count === 1 ? 'map.defaultSport.noticeOne' : 'map.defaultSport.notice', { count })}
          </span>
          <button
            type="button"
            ref={openButtonRef}
            data-testid="default-sport-open"
            onClick={() => setOpen(true)}
            style={{ ...btn, background: '#2d6a4f', color: '#fff' }}
          >
            {t('map.defaultSport.cta')}
          </button>
          <button
            type="button"
            data-testid="default-sport-dismiss"
            onClick={() => setDismissed(true)}
            aria-label={t('map.defaultSport.dismiss')}
            title={t('map.defaultSport.dismiss')}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: '#9ca3af', padding: '0 2px', lineHeight: 1 }}
          >
            ×
          </button>
        </div>
      )}

      {open && (
        <div
          data-testid="default-sport-panel"
          role="dialog"
          aria-modal="true"
          aria-label={t('map.defaultSport.title')}
          onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } }}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.35)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
          }}
        >
          <div ref={dialogRef} tabIndex={-1} style={{
            outline: 'none',
            background: '#fff', borderRadius: 14, width: '100%', maxWidth: 460, maxHeight: '86vh',
            display: 'flex', flexDirection: 'column', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
            fontSize: 13, color: '#1f2937',
          }}>
            <div style={{ padding: '14px 16px 8px', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>{t('map.defaultSport.title')}</h2>
                <p style={{ margin: '6px 0 0', fontSize: 12, color: '#6b7280', lineHeight: 1.4 }}>
                  {t('map.defaultSport.intro')}
                </p>
              </div>
              <button
                type="button"
                data-testid="default-sport-close"
                onClick={() => setOpen(false)}
                aria-label={t('map.defaultSport.dismiss')}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: '#9ca3af', lineHeight: 1 }}
              >
                ✕
              </button>
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 16px', fontWeight: 700, borderBottom: '1px solid #f0f0f0' }}>
              <input
                type="checkbox"
                data-testid="default-sport-select-all"
                checked={all}
                onChange={(e) => { setAll(e.target.checked); setSelected(new Set()); }}
              />
              {t('map.defaultSport.selectAll', { count })}
            </label>

            <ul data-testid="default-sport-list" style={{ listStyle: 'none', margin: 0, padding: '4px 16px', overflowY: 'auto', flex: 1 }}>
              {listed.map((a) => (
                <li key={a.id}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      data-testid={`default-sport-item-${a.id}`}
                      checked={all || selected.has(a.id)}
                      onChange={() => toggle(a.id)}
                    />
                    <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600 }}>
                      {a.name || t('map.activityDefaultName')}
                    </span>
                    <span style={{ fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap' }}>
                      {a.activity_date ? `${fmtDate(a.activity_date, dateLocale)} · ` : ''}{formatDist(a.distance_m ?? undefined)}
                    </span>
                  </label>
                </li>
              ))}
              {others > 0 && (
                <li data-testid="default-sport-others" style={{ padding: '6px 0', fontSize: 12, color: '#6b7280' }}>
                  {t('map.defaultSport.others', { count: others })}
                </li>
              )}
            </ul>

            {error && (
              <div role="alert" data-testid="default-sport-error" style={{ padding: '0 16px 6px', fontSize: 12, color: '#c0392b' }}>
                {t('map.defaultSport.error')}
              </div>
            )}

            <div style={{ padding: '10px 16px 14px', borderTop: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                  {t('map.defaultSport.sportLabel')}
                  <select
                    data-testid="default-sport-sport"
                    value={sport}
                    disabled={busy}
                    onChange={(e) => setSport(e.target.value as DefaultSportChoice)}
                    style={{
                      padding: '4px 8px', borderRadius: 10, fontSize: 12, fontWeight: 700, border: 'none',
                      background: SPORT_COLORS[sport] ?? '#2d6a4f', color: '#fff',
                    }}
                  >
                    {DEFAULT_SPORT_CHOICES.map((s) => <option key={s} value={s}>{t(`sport.${s}`)}</option>)}
                  </select>
                </label>
                <button
                  type="button"
                  data-testid="default-sport-apply"
                  disabled={busy || picked === 0 || (!all && selected.size > BULK_SPORT_MAX_IDS)}
                  onClick={() => { void run(all ? 'all' : [...selected], sport); }}
                  style={{ ...btn, background: '#2d6a4f', color: '#fff', opacity: busy || picked === 0 ? 0.5 : 1, marginLeft: 'auto' }}
                >
                  {t('map.defaultSport.apply', { count: picked })}
                </button>
              </div>
              <button
                type="button"
                data-testid="default-sport-confirm-road"
                disabled={busy}
                onClick={() => { void run('all', 'road'); }}
                style={{ ...btn, background: '#f0f0f0', color: '#374151', alignSelf: 'flex-start' }}
              >
                {t('map.defaultSport.confirmRoad')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
