'use client';

import { useEffect } from 'react';

export default function SentryProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    if (dsn) {
      import('@sentry/react').then((Sentry) => {
        if (!Sentry.isInitialized()) {
          Sentry.init({
            dsn,
            environment: process.env.NODE_ENV,
            tracesSampleRate: 0.05,
            enabled: process.env.NODE_ENV === 'production',
          });
        }
      });
    }
  }, []);

  return <>{children}</>;
}
