import type { Metadata } from 'next';

const TITLE = 'Méthode — Chemins communs';
const DESCRIPTION = 'Notre méthodologie : traces GPS brutes, masquage des extrémités, données communautaires ouvertes sous ODbL.';

export const metadata: Metadata = {
  title: 'Méthode',
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

export default function MethodeLayout({ children }: { children: React.ReactNode }) {
  return children;
}
