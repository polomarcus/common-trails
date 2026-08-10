import type { Metadata } from 'next';

const TITLE = 'Support & aide — Chemins communs';
const DESCRIPTION = 'Besoin d\'aide avec Chemins Communs ? Contact, signalement de bugs et questions.';

export const metadata: Metadata = {
  title: 'Support & aide',
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

export default function SupportLayout({ children }: { children: React.ReactNode }) {
  return children;
}
