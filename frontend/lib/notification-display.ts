/**
 * Pure display-text resolver for a notification toast.
 *
 * Every notification carries a backend-generated (French) title/body that
 * the toast renders verbatim. The client-side re-localisation of
 * `activity_synced` went away with the Strava live sync: nothing emits that
 * kind any more, and a legacy row still in the database renders its stored
 * title/body like any other kind. Kept pure + dependency-free so it is
 * unit-testable without React; `t` stays in the signature for the next kind
 * that needs re-localising.
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
  _t: Translate,
): { title: string; body: string } {
  return { title: n.title, body: n.body };
}
