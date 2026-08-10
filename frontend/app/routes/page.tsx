'use client';

import { Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { useT } from '@/lib/i18n';

function RouteRedirect() {
  const t = useT();
  const searchParams = useSearchParams();
  const router = useRouter();
  const id = searchParams.get('id');

  useEffect(() => {
    if (id) {
      router.replace(`/map?route=${id}`);
    } else {
      router.replace('/map');
    }
  }, [id, router]);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', color: '#aaa', fontSize: 16, fontFamily: 'system-ui, sans-serif' }}>
      {t('routes.redirecting')}
    </div>
  );
}

export default function RoutesPage() {
  const t = useT();
  return (
    <Suspense fallback={<div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', color: '#aaa', fontSize: 16 }}>{t('routes.redirecting')}</div>}>
      <RouteRedirect />
    </Suspense>
  );
}
