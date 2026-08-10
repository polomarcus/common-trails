'use client';

import { useEffect, useState } from 'react';

import { API_URL } from '@/lib/api-client';
import { useT } from '@/lib/i18n';

type BannerState = 'checking' | 'hidden' | 'waking' | 'ready';

interface StartupStatus {
  step: number;
  total: number;
  label: string;
  done: boolean;
}

export default function ServerWakeupBanner() {
  const t = useT();
  const [state, setState] = useState<BannerState>('checking');
  const [progress, setProgress] = useState<StartupStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const checkBackend = async (): Promise<boolean> => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);
      try {
        const res = await fetch(`${API_URL}/readyz`, {
          signal: controller.signal,
          mode: 'cors',
        });
        clearTimeout(timeout);
        return res.ok;
      } catch {
        clearTimeout(timeout);
        return false;
      }
    };

    const fetchProgress = async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);
      try {
        const res = await fetch(`${API_URL}/startup-status`, {
          signal: controller.signal,
          mode: 'cors',
        });
        clearTimeout(timeout);
        if (res.ok) {
          const data: StartupStatus = await res.json();
          if (!cancelled) setProgress(data);
        }
      } catch {
        clearTimeout(timeout);
      }
    };

    const init = async () => {
      const warm = await checkBackend();
      if (cancelled) return;

      if (warm) {
        setState('hidden');
        return;
      }

      // Delay before showing banner to avoid flash on borderline cases
      await new Promise((r) => setTimeout(r, 1500));
      if (cancelled) return;
      setState('waking');

      pollTimer = setInterval(async () => {
        await fetchProgress();
        const ok = await checkBackend();
        if (cancelled) return;
        if (ok) {
          if (pollTimer) clearInterval(pollTimer);
          pollTimer = null;
          setState('ready');
          setTimeout(() => {
            if (!cancelled) setState('hidden');
          }, 2000);
        }
      }, 2000);
    };

    init();

    return () => {
      cancelled = true;
      if (pollTimer) clearInterval(pollTimer);
    };
  }, []);

  if (state === 'checking' || state === 'hidden') return null;

  const isReady = state === 'ready';
  const pct = progress ? Math.round((progress.step / progress.total) * 100) : 0;

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 9999,
        padding: '10px 16px 8px',
        fontSize: '14px',
        fontWeight: 600,
        color: isReady ? '#065f46' : '#92400e',
        background: isReady ? '#d1fae5' : '#fef3c7',
        borderBottom: `2px solid ${isReady ? '#6ee7b7' : '#fbbf24'}`,
        transition: 'background 0.3s, color 0.3s, border-color 0.3s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
        {isReady ? (
          <span style={{ fontSize: '16px' }}>&#10003;</span>
        ) : (
          <span className="wakeup-pulse" />
        )}
        <span>
          {isReady
            ? t('wakeup.ready')
            : progress?.label
              ? t('wakeup.startingLabel', { label: progress.label })
              : t('wakeup.starting')}
        </span>
        {!isReady && progress && (
          <span style={{ fontSize: '12px', fontWeight: 400, opacity: 0.8 }}>
            ({progress.step}/{progress.total})
          </span>
        )}
      </div>

      {!isReady && (
        <div
          style={{
            marginTop: '6px',
            height: '4px',
            borderRadius: '2px',
            background: 'rgba(0,0,0,0.08)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              width: `${isReady ? 100 : pct}%`,
              background: '#f59e0b',
              borderRadius: '2px',
              transition: 'width 0.5s ease',
            }}
          />
        </div>
      )}
    </div>
  );
}
