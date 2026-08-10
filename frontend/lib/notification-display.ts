/**
 * Pure display-text resolver for a notification toast.
 *
 * Most notification kinds carry a backend-generated (French) title/body
 * that the toast renders verbatim. A few kinds are re-localised on the
 * client from i18n keys + `meta` so they honour the user's locale switch —
 * `activity_synced` is the first (see NotificationToasts). Kept pure +
 * dependency-free so it is unit-testable without React.
 */
export interface NotificationLike {
  kind: string;
  title: string;
  body: string;
  meta: Record<string, unknown> | null;
}

type Translate = (key: string, vars?: Record<string, string | number>) => string;

export function notificationDisplay(
  n: NotificationLike,
  t: Translate,
): { title: string; body: string } {
  if (n.kind === 'activity_synced') {
    const rawName = n.meta?.name;
    const name = typeof rawName === 'string' ? rawName.trim() : '';
    return {
      title: t('notif.activitySynced.title'),
      body: name
        ? t('notif.activitySynced.body', { name })
        : t('notif.activitySynced.bodyNoName'),
    };
  }
  return { title: n.title, body: n.body };
}
