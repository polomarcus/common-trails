import type { Metadata } from 'next';

const TITLE = 'Itinéraire — Chemins communs';
const DESCRIPTION = "Détail d'un itinéraire cycliste partagé par la communauté.";

export const metadata: Metadata = {
  title: 'Itinéraire',
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

export default function RoutesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
