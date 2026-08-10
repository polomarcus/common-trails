'use client';

import { useEffect, useState } from 'react';

import { apiFetchSafe, apiFetch } from '@/lib/api-client';
import { useT } from '@/lib/i18n';
import { notificationDisplay } from '@/lib/notification-display';

interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string;
  meta: Record<string, unknown> | null;
  created_at: string;
  read_at: string | null;
}

interface NotificationList {
  items: NotificationItem[];
  unread_count: number;
}

/**
 * One-shot poll of `/me/notifications?unread_only=true` on app mount,
 * rendered as a stack of dismissible toasts. No continuous polling —
 * friends-beta, every notification fires from a long-running job and
 * the user only needs to see it once per session.
 *
 * Auth is httpOnly-cookie based (apiFetch sends credentials: 'include'),
 * so we render nothing when the user is anonymous.
 */
export default function NotificationToasts() {
  const t = useT();
  const [items, setItems] = useState<NotificationItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const data = await apiFetchSafe<NotificationList>('/me/notifications?unread_only=true&limit=20');
      if (cancelled || !data) return;
      setItems(data.items);
    })();
    return () => { cancelled = true; };
  }, []);

  const markRead = async (id: string) => {
    setItems(prev => prev.filter(n => n.id !== id));
    try {
      await apiFetch(`/me/notifications/${id}/read`, { method: 'POST' });
    } catch {
      // Best-effort — if the call fails the row stays unread and will
      // reappear on the next mount; not worth surfacing the error.
    }
  };

  if (items.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="Notifications"
      style={{
        position: 'fixed',
        top: 16,
        right: 16,
        zIndex: 10000,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        maxWidth: 360,
      }}
    >
      {items.map(n => {
        const { title, body } = notificationDisplay(n, t);
        return (
        <div
          key={n.id}
          style={{
            background: '#1e2128',
            border: '1px solid rgba(255,255,255,0.08)',
            borderLeft: '3px solid #27ae60',
            borderRadius: 8,
            padding: '12px 14px',
            color: '#fff',
            boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
            fontSize: 13,
            lineHeight: 1.4,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
          {body && (
            <div style={{ color: 'rgba(255,255,255,0.7)', marginBottom: 8 }}>
              {body}
            </div>
          )}
          <button
            type="button"
            onClick={() => markRead(n.id)}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#5dade2',
              cursor: 'pointer',
              fontSize: 12,
              padding: 0,
              textDecoration: 'underline',
            }}
          >
            {t('notif.markRead')}
          </button>
        </div>
        );
      })}
    </div>
  );
}
