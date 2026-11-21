'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { API_URL } from '@/lib/api-client';
import { clearAuthState, getToken } from '@/lib/auth';

export interface UserMenuState {
  showUserMenu: boolean;
  setShowUserMenu: React.Dispatch<React.SetStateAction<boolean>>;
  userEmail: string | null;
  /** is_admin from GET /auth/me — gates the toolbar's Admin entry. */
  isAdmin: boolean;
  handleLogout: () => Promise<void>;
}

export function useUserMenu(): UserMenuState {
  const router = useRouter();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  // Fetch user info on mount
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    (async () => {
      try {
        // /auth/me is the real endpoint (a bare /me never existed → the email
        // and admin flag silently stayed empty).
        const resp = await fetch(`${API_URL}/auth/me`, { credentials: 'include' });
        if (resp.status === 401) {
          // A session cookie was sent but refused (signed out on another
          // device, email changed, expired): forget it here too — drop the
          // dead cookie, clear the local flag, reload so every component
          // re-reads it — instead of a signed-in UI whose every call fails.
          // No cookie at all ("Not authenticated") is left alone.
          const body = await resp.json().catch(() => null);
          if (body?.detail && body.detail !== 'Not authenticated') {
            try {
              await fetch(`${API_URL}/auth/logout`, { method: 'DELETE', credentials: 'include' });
            } catch { /* best-effort */ }
            clearAuthState();
            window.location.reload();
          }
          return;
        }
        if (!resp.ok) return;
        const data = await resp.json();
        setUserEmail(data.email ?? null);
        setIsAdmin(data.is_admin === true);
      } catch { /* ignore */ }
    })();
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await fetch(`${API_URL}/auth/logout`, { method: 'DELETE', credentials: 'include' });
    } catch { /* best-effort */ }
    localStorage.removeItem('user_id');
    // Legacy key from the removed Strava connection — still cleared so an old
    // browser does not keep a stale display name around.
    localStorage.removeItem('strava_name');
    router.push('/?disconnected=1');
  }, [router]);

  return {
    showUserMenu, setShowUserMenu,
    userEmail, isAdmin,
    handleLogout,
  };
}
