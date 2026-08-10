'use client';

import { useState, useRef, useEffect, useCallback, memo } from 'react';
import { useI18n } from '@/lib/i18n';

interface PhotonFeature {
  geometry: { coordinates: [number, number] };
  properties: {
    name?: string;
    city?: string;
    village?: string;
    county?: string;
    state?: string;
    country?: string;
    osm_key?: string;
    osm_value?: string;
  };
}

interface PlaceSearchProps {
  onSelect: (lon: number, lat: number, name: string) => void;
  /** Bias results toward this center [lon, lat] */
  biasCenter?: [number, number];
  /** Visual variant */
  variant?: 'light' | 'dark';
}

export default memo(function PlaceSearch({ onSelect, biasCenter, variant = 'light' }: PlaceSearchProps) {
  const { locale, t } = useI18n();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<PhotonFeature[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const fetchResults = useCallback(async (q: string) => {
    if (q.length < 2) { setResults([]); return; }
    try {
      const params = new URLSearchParams({ q, lang: locale, limit: '5' });
      if (biasCenter) {
        params.set('lat', String(biasCenter[1]));
        params.set('lon', String(biasCenter[0]));
      }
      const resp = await fetch(`https://photon.komoot.io/api/?${params}`);
      if (!resp.ok) return;
      const data = await resp.json();
      setResults(data.features || []);
      setOpen(true);
      setActiveIdx(-1);
    } catch { /* silent */ }
  }, [biasCenter, locale]);

  const handleChange = (value: string) => {
    setQuery(value);
    if (timerRef.current) clearTimeout(timerRef.current);
    if (value.length < 2) { setResults([]); setOpen(false); return; }
    timerRef.current = setTimeout(() => fetchResults(value), 300);
  };

  const handleSelect = (feat: PhotonFeature) => {
    const [lon, lat] = feat.geometry.coordinates;
    const name = formatName(feat);
    setQuery(name);
    setOpen(false);
    setResults([]);
    onSelect(lon, lat, name);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && activeIdx >= 0 && results[activeIdx]) {
      e.preventDefault();
      handleSelect(results[activeIdx]);
    }
  };

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Cleanup timer
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const isDark = variant === 'dark';

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', maxWidth: 340 }}>
      <div style={{ position: 'relative' }}>
        <span style={{
          position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
          fontSize: 14, color: isDark ? 'rgba(255,255,255,0.5)' : '#999', pointerEvents: 'none',
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/>
            <line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
        </span>
        <input
          type="text"
          value={query}
          onChange={e => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => { if (results.length > 0) setOpen(true); }}
          placeholder={t('placeSearch.placeholder')}
          data-testid="place-search-input"
          style={{
            width: '100%',
            padding: '8px 12px 8px 30px',
            borderRadius: 20,
            border: isDark ? '1px solid rgba(255,255,255,0.2)' : '1px solid #ddd',
            background: isDark ? 'rgba(255,255,255,0.1)' : '#fff',
            color: isDark ? '#fff' : '#333',
            fontSize: 13,
            outline: 'none',
            backdropFilter: isDark ? 'blur(8px)' : undefined,
            boxShadow: isDark ? 'none' : '0 2px 8px rgba(0,0,0,0.08)',
          }}
        />
        {query && (
          <button
            onClick={() => { setQuery(''); setResults([]); setOpen(false); }}
            style={{
              position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', cursor: 'pointer', padding: 2,
              color: isDark ? 'rgba(255,255,255,0.5)' : '#aaa', fontSize: 14, lineHeight: 1,
            }}
            aria-label={t('common.clear')}
          >
            ✕
          </button>
        )}
      </div>
      {open && results.length > 0 && (
        <div
          data-testid="place-search-results"
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0,
            marginTop: 4,
            background: '#fff', borderRadius: 12,
            boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
            border: '1px solid #e8e8e8',
            overflow: 'hidden', zIndex: 50,
          }}
        >
          {results.map((feat, i) => (
            <button
              key={i}
              onClick={() => handleSelect(feat)}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                display: 'block', width: '100%', textAlign: 'left',
                padding: '10px 14px',
                border: 'none', cursor: 'pointer',
                background: i === activeIdx ? '#f0faf4' : '#fff',
                fontSize: 13, color: '#333',
                borderBottom: i < results.length - 1 ? '1px solid #f0f0f0' : 'none',
              }}
            >
              <div style={{ fontWeight: 600 }}>{feat.properties.name || t('common.noName')}</div>
              <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>
                {formatSubtitle(feat)}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

function formatName(feat: PhotonFeature): string {
  return feat.properties.name || feat.properties.city || feat.properties.village || 'Lieu';
}

function formatSubtitle(feat: PhotonFeature): string {
  const parts: string[] = [];
  const p = feat.properties;
  if (p.city || p.village) parts.push(p.city || p.village || '');
  if (p.county) parts.push(p.county);
  if (p.state) parts.push(p.state);
  if (p.country) parts.push(p.country);
  // Remove duplicate first entry (if name === city)
  if (parts[0] === p.name && parts.length > 1) parts.shift();
  return parts.join(', ') || '';
}
