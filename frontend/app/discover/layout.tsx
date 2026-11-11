import type { Metadata } from 'next';

const TITLE = 'Explorer — Chemins communs';
const DESCRIPTION = 'Trouve des itinéraires gravel et VTT près de chez toi grâce à la communauté.';

export const metadata: Metadata = {
  title: 'Explorer',
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

export default function DiscoverLayout({ children }: { children: React.ReactNode }) {
  return children;
}
