'use client';

import { memo } from 'react';
import { useI18n } from '@/lib/i18n';

export default memo(function LanguageToggle() {
  const { locale, setLocale } = useI18n();

  return (
    <button
      data-testid="language-toggle"
      onClick={() => setLocale(locale === 'fr' ? 'en' : 'fr')}
      aria-label={locale === 'fr' ? 'Switch to English' : 'Passer en français'}
      title={locale === 'fr' ? 'Switch to English' : 'Passer en français'}
      style={{
        background: 'none',
        border: '1px solid #e0e0e0',
        borderRadius: 4,
        cursor: 'pointer',
        padding: '2px 6px',
        fontSize: 16,
        lineHeight: 1,
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {locale === 'fr' ? '🇬🇧' : '🇫🇷'}
    </button>
  );
});
