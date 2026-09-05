import type { Metadata } from 'next';
import ServerWakeupBanner from '../components/ServerWakeupBanner';
import SentryProvider from '../components/SentryProvider';
import { I18nProvider } from '../lib/i18n';
import BetaBanner from '../components/BetaBanner';
import NotificationToasts from '../components/NotificationToasts';
import './globals.css';

// Mirrors the hero tagline (home.hero.tagline) — the crawler/link-preview copy
// must sell the same positioning as the page itself.
const SITE_DESCRIPTION = 'La heatmap communautaire du vélo : plus un chemin est roulé, plus il brille. De quoi tracer de vrais parcours. Open source, données ODbL.';

export const metadata: Metadata = {
  title: {
    default: 'Chemins communs',
    template: '%s — Chemins communs',
  },
  description: SITE_DESCRIPTION,
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || 'https://chemins-communs.fr'),
  icons: {
    icon: `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/icon.svg`,
  },
  openGraph: {
    title: 'Chemins communs',
    description: SITE_DESCRIPTION,
    type: 'website',
    locale: 'fr_FR',
    images: [{
      url: `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/og-card.png`,
      width: 1200,
      height: 630,
      type: 'image/png',
      alt: 'Chemins Communs — carte communautaire des traces cyclistes',
    }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Chemins communs',
    description: SITE_DESCRIPTION,
    images: [`${process.env.NEXT_PUBLIC_BASE_PATH || ''}/og-card.png`],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <head>
        {/* Preconnect to IGN tile server — TLS handshake starts during JS download
            instead of after MapLibre constructs. Saves ~100-200ms on first tile. */}
        <link rel="preconnect" href="https://data.geopf.fr" />
        <link rel="dns-prefetch" href="https://data.geopf.fr" />
      </head>
      <body>
        <I18nProvider>
          <ServerWakeupBanner />
          <BetaBanner />
          <NotificationToasts />
          <SentryProvider>{children}</SentryProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
