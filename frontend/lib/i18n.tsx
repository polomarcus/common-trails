'use client';

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
// Synchronous imports — dictionaries are ~30 KB each gzipped and present
// in every bundle anyway. Async `import()` previously caused a visible
// flash-of-keys on cold paint (translations[locale]?.[key] ?? key returns
// the raw 'home.connect.title' string until the dynamic import resolves).
import frTranslations from './translations/fr';
import enTranslations from './translations/en';

export type Locale = 'fr' | 'en';

// Flat translation dictionary — keys are dot-separated paths
export type Translations = Record<string, string>;

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

const STORAGE_KEY = 'ct_locale';

const TRANSLATIONS: Record<Locale, Translations> = {
  fr: frTranslations,
  en: enTranslations,
};

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>('fr');

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as Locale | null;
      if (saved === 'en' || saved === 'fr') setLocaleState(saved);
    } catch { /* SSR / no localStorage */ }
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try { localStorage.setItem(STORAGE_KEY, l); } catch { /* noop */ }
  }, []);

  const t = useCallback((key: string, vars?: Record<string, string | number>): string => {
    let str = TRANSLATIONS[locale]?.[key] ?? TRANSLATIONS['fr']?.[key] ?? key;
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        str = str.replaceAll(`{${k}}`, String(v));
      }
    }
    return str;
  }, [locale]);

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}

/** Shorthand: just the t function */
export function useT() {
  return useI18n().t;
}
