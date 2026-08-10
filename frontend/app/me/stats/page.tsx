'use client';

// The OLD matched-era personal stats page (/me/stats) is DISABLED post-pivot.
// It read `unique_cells` from the dropped cell-aggregation model, so it now
// renders empty/broken. The route file is kept as a client-side redirect stub
// so the static export still builds `/me/stats` and any existing bookmark /
// inbound link lands on the new post-pivot personal dashboard (`/stats`)
// rather than a dead shell. (output: 'export' → no server-side redirect; a
// useEffect replace is the only option on a statically exported route.)

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useT } from '@/lib/i18n';

export default function MeStatsPage() {
  const t = useT();
  const router = useRouter();

  useEffect(() => {
    router.replace('/stats');
  }, [router]);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        color: '#aaa',
        fontSize: 16,
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}
    >
      {t('routes.redirecting')}
    </div>
  );
}
