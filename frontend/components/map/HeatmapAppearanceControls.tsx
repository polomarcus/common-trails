'use client';

/**
 * Réglages d'apparence dans le menu « Calques » de /map — une ligne repliée
 * « Apparence » qui ouvre :
 *   - couleur + opacité de « Ma heatmap » (si le calque est affiché) ;
 *   - opacité de la heatmap commune (si le calque est affiché).
 * Stockés par navigateur (lib/heatmap-appearance.ts) ; la carte se repeint via
 * les hooks abonnés au même store.
 */
import React, { useState } from 'react';
import { useI18n } from '@/lib/i18n';
import {
  COMMUNITY_OPACITY_MAX, DEFAULT_APPEARANCE, MY_COLOR_CHOICES, OPACITY_MAX, OPACITY_MIN,
  setHeatmapAppearance, useHeatmapAppearance,
} from '@/lib/heatmap-appearance';

const row: React.CSSProperties = {
  padding: '0 14px 6px 40px', display: 'flex', alignItems: 'center', gap: 8,
  fontSize: 11, color: '#666',
};

const heading: React.CSSProperties = {
  padding: '0 14px 3px 40px', fontSize: 11, fontWeight: 600, color: '#444',
};

function OpacitySlider({ value, max, onChange, testId, label }: {
  value: number; max: number; onChange: (v: number) => void; testId: string; label: string;
}) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
      <span style={{ flexShrink: 0 }}>{label}</span>
      <input
        type="range"
        data-testid={testId}
        min={OPACITY_MIN}
        max={max}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ flex: 1, minWidth: 0, accentColor: '#2d6a4f' }}
      />
      <span style={{ flexShrink: 0, width: 32, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {Math.round(value * 100)} %
      </span>
    </label>
  );
}

function MyHeatmapAppearance() {
  const { t } = useI18n();
  const a = useHeatmapAppearance();
  return (
    <div data-testid="my-heatmap-appearance">
      <div style={heading}>{t('map.toolbar.myHeatmap')}</div>
      <div style={row} role="radiogroup" aria-label={t('map.appearance.color')}>
        <span style={{ flexShrink: 0 }}>{t('map.appearance.color')}</span>
        {MY_COLOR_CHOICES.map((c) => (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={a.myColor === c}
            aria-label={c}
            data-testid={`my-heatmap-color-${c.slice(1)}`}
            onClick={() => setHeatmapAppearance({ myColor: c })}
            style={{
              width: 18, height: 18, borderRadius: '50%', background: c, cursor: 'pointer', padding: 0,
              border: '2px solid #fff',
              boxShadow: a.myColor === c ? '0 0 0 2px #2d6a4f' : '0 0 0 1px #ccc',
            }}
          />
        ))}
      </div>
      <div style={row}>
        <OpacitySlider
          label={t('map.appearance.opacity')}
          testId="my-heatmap-opacity"
          value={a.myOpacity}
          max={OPACITY_MAX}
          onChange={(v) => setHeatmapAppearance({ myOpacity: v })}
        />
      </div>
    </div>
  );
}

function CommunityHeatmapAppearance() {
  const { t } = useI18n();
  const a = useHeatmapAppearance();
  return (
    <>
    <div style={heading}>{t('map.toolbar.heatmapLayer')}</div>
    <div style={row} data-testid="community-heatmap-appearance">
      <OpacitySlider
        label={t('map.appearance.opacity')}
        testId="community-heatmap-opacity"
        value={a.communityOpacity}
        max={COMMUNITY_OPACITY_MAX}
        onChange={(v) => setHeatmapAppearance({ communityOpacity: v })}
      />
      {a.communityOpacity !== DEFAULT_APPEARANCE.communityOpacity && (
        <button
          type="button"
          data-testid="community-heatmap-opacity-reset"
          onClick={() => setHeatmapAppearance({ communityOpacity: DEFAULT_APPEARANCE.communityOpacity })}
          style={{ border: 'none', background: 'none', color: '#2d6a4f', cursor: 'pointer', fontSize: 11, padding: 0, textDecoration: 'underline' }}
        >
          {t('map.appearance.reset')}
        </button>
      )}
    </div>
    </>
  );
}

export default function HeatmapAppearancePanel({ showMine, showCommunity }: {
  showMine: boolean; showCommunity: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (!showMine && !showCommunity) return null;
  return (
    <div data-testid="heatmap-appearance">
      <button
        type="button"
        data-testid="heatmap-appearance-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          border: 'none', background: 'none', cursor: 'pointer', padding: '2px 14px 6px 40px',
          fontSize: 11, fontWeight: 600, color: '#2d6a4f', width: '100%', textAlign: 'left',
        }}
      >
        🎨 {t('map.appearance.title')} {open ? '\u25B4' : '\u25BE'}
      </button>
      {open && showMine && <MyHeatmapAppearance />}
      {open && showCommunity && <CommunityHeatmapAppearance />}
    </div>
  );
}
