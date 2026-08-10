import type { Metadata } from 'next';
import ServerWakeupBanner from '../components/ServerWakeupBanner';
import SentryProvider from '../components/SentryProvider';
import { I18nProvider } from '../lib/i18n';
import BetaBanner from '../components/BetaBanner';
import NotificationToasts from '../components/NotificationToasts';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Chemins communs',
    template: '%s — Chemins communs',
  },
  description: 'La carte de popularité cycliste ouverte et communautaire — bâtie par les traces que vous partagez. Open source, ODbL.',
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || 'https://chemins-communs.fr'),
  icons: {
    icon: `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/icon.svg`,
  },
  openGraph: {
    title: 'Chemins communs',
    description: 'La carte de popularité cycliste ouverte et communautaire — bâtie par les traces que vous partagez. Open source, ODbL.',
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
    description: 'La carte de popularité cycliste ouverte et communautaire — bâtie par les traces que vous partagez. Open source, ODbL.',
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
