'use client';

// The "Découvrir les itinéraires" page is DISABLED (frozen social/discovery —
// not part of the compliance pivot). The route file is kept as a client-side
// redirect stub so the static export still builds `/discover` and any existing
// bookmark / inbound link lands on the map rather than a broken/frozen page.
// (output: 'export' → no server-side redirect; a useEffect replace is the only
// option on a statically exported route.)

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useT } from '@/lib/i18n';

export default function DiscoverPage() {
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
