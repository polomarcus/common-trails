import type { Metadata } from 'next';

const TITLE = 'Politique de confidentialité — Chemins communs';
const DESCRIPTION = 'Comment Chemins Communs protège vos données personnelles et vos traces GPS.';

export const metadata: Metadata = {
  title: 'Politique de confidentialité',
  description: DESCRIPTION,
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    title: TITLE,
    description: DESCRIPTION,
  },
};

export default function PrivacyLayout({ children }: { children: React.ReactNode }) {
  return children;
}
