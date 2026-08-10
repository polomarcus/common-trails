import type { Metadata } from 'next';

const TITLE = 'Statistiques — Chemins communs';
const DESCRIPTION = 'Statistiques de la communauté Chemins Communs : traces, utilisateurs, sports.';

export const metadata: Metadata = {
  title: 'Statistiques',
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

export default function StatsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
