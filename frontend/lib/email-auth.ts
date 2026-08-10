/**
 * Passwordless magic-link auth — network logic, isolated from React so it can
 * be unit-tested with a mocked fetch (see lib/__tests__/email-auth.test.ts).
 *
 * The backend ALWAYS returns a generic 200 for a request (no account
 * enumeration), so `requestMagicLink` resolves to { ok: true } on 2xx and
 * { ok: false } only on a network / non-2xx failure.
 */
import { API_URL } from '@/lib/api-client';

export interface RequestResult {
  ok: boolean;
}

export interface VerifyResult {
  ok: boolean;
  userId?: string;
  email?: string;
  redirect?: string;
}

/** POST /auth/email/request — trigger a magic-link email. */
export async function requestMagicLink(
  email: string,
  locale?: string,
): Promise<RequestResult> {
  try {
    const resp = await fetch(`${API_URL}/auth/email/request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, locale }),
    });
    return { ok: resp.ok };
  } catch {
    return { ok: false };
  }
}

export interface SetEmailResult {
  ok: boolean;
  /** HTTP status — callers distinguish 409 (email owned by another account). */
  status: number;
  /** True when a confirmation link was mailed (change pending its click). */
  pending?: boolean;
  email?: string;
}

/**
 * POST /auth/me/email — set/change the current account's email (account
 * unification). The server mails a confirmation link to the NEW address and
 * only finalizes on confirm, so a 200 means "confirmation sent", not "changed".
 * 409 means the address is already owned by another account (no takeover).
 */
export async function setAccountEmail(
  email: string,
  locale?: string,
): Promise<SetEmailResult> {
  try {
    const resp = await fetch(`${API_URL}/auth/me/email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, locale }),
    });
    if (!resp.ok) return { ok: false, status: resp.status };
    const data = await resp.json();
    return { ok: true, status: resp.status, pending: data.pending, email: data.email };
  } catch {
    return { ok: false, status: 0 };
  }
}

export interface ConfirmEmailResult {
  ok: boolean;
  status: number;
  email?: string;
}

/** POST /auth/me/email/confirm — finalize an email change from its link. */
export async function confirmEmailChange(token: string): Promise<ConfirmEmailResult> {
  try {
    const resp = await fetch(`${API_URL}/auth/me/email/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ token }),
    });
    if (!resp.ok) return { ok: false, status: resp.status };
    const data = await resp.json();
    return { ok: true, status: resp.status, email: data.email };
  } catch {
    return { ok: false, status: 0 };
  }
}

/** POST /auth/email/verify — consume the token, set the session cookie. */
export async function verifyMagicLink(token: string): Promise<VerifyResult> {
  try {
    const resp = await fetch(`${API_URL}/auth/email/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ token }),
    });
    if (!resp.ok) return { ok: false };
    const data = await resp.json();
    return {
      ok: true,
      userId: data.user_id,
      email: data.email,
      redirect: data.redirect ?? '/strava',
    };
  } catch {
    return { ok: false };
  }
}
