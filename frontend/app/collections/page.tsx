'use client';

// The Collections (trips) page is DISABLED (frozen social — not part of the
// compliance pivot). It was login-gated and reachable only by URL, rendering a
// dead/broken shell post-pivot. The route file is kept as a client-side
// redirect stub so the static export still builds `/collections` and any
// existing bookmark / inbound link lands on the map rather than a broken page.
// (output: 'export' → no server-side redirect; a useEffect replace is the only
// option on a statically exported route.)

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useT } from '@/lib/i18n';

export default function CollectionsPage() {
  const t = useT();
  const router = useRouter();

  useEffect(() => {
    router.replace('/map');
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
