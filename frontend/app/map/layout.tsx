import type { Metadata } from 'next';

const TITLE = 'Carte — Chemins communs';
const DESCRIPTION = 'Explorez les traces cyclistes de la communauté sur une carte interactive.';

export const metadata: Metadata = {
  title: 'Carte',
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

export default function MapLayout({ children }: { children: React.ReactNode }) {
  return children;
}
