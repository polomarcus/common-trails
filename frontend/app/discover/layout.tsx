import type { Metadata } from 'next';

const TITLE = 'Explorer — Chemins communs';
const DESCRIPTION = 'Trouvez des itinéraires gravel et VTT près de chez vous grâce à la communauté.';

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
