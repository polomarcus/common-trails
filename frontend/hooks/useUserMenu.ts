'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';

export interface UserMenuState {
  showUserMenu: boolean;
  setShowUserMenu: React.Dispatch<React.SetStateAction<boolean>>;
  userEmail: string | null;
  stravaName: string | null;
  lastSyncedAt: string | null;
  stravaSyncing: boolean;
  stravaSyncMsg: string | null;
  handleStravaConnect: () => void;
  handleStravaSync: () => Promise<void>;
  handleLogout: () => Promise<void>;
}

export function useUserMenu(reloadActivitiesRef: React.RefObject<(() => void) | null>): UserMenuState {
  const router = useRouter();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [stravaName, setStravaName] = useState<string | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [stravaSyncing, setStravaSyncing] = useState(false);
  const [stravaSyncMsg, setStravaSyncMsg] = useState<string | null>(null);

  // Fetch user info on mount
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    (async () => {
      try {
        const resp = await fetch(`${API_URL}/me`, { credentials: 'include' });
        if (!resp.ok) return;
        const data = await resp.json();
        setUserEmail(data.email ?? null);
        setStravaName(data.strava_name ?? localStorage.getItem('strava_name'));
        setLastSyncedAt(data.last_synced_at ?? null);
      } catch { /* ignore */ }
    })();
  }, []);

  const handleStravaConnect = useCallback(() => {
    if (!getToken()) return;
    window.location.href = `${API_URL}/integrations/strava/connect`;
  }, []);

  const handleStravaSync = useCallback(async () => {
    const tok = getToken();
    if (!tok) return;
    setStravaSyncing(true);
    setStravaSyncMsg('Import en cours…');
    try {
      const resp = await fetch(`${API_URL}/integrations/strava/import_all`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!resp.ok) throw new Error();
      const data = await resp.json();
      setStravaSyncMsg(`✓ ${data.imported ?? 0} activité(s) importée(s)`);
      reloadActivitiesRef.current?.();
    } catch {
      setStravaSyncMsg('✗ Erreur de synchronisation');
    } finally {
      setStravaSyncing(false);
    }
  }, [reloadActivitiesRef]);

  const handleLogout = useCallback(async () => {
    try {
      await fetch(`${API_URL}/auth/logout`, { method: 'DELETE', credentials: 'include' });
    } catch { /* best-effort */ }
    localStorage.removeItem('user_id');
    localStorage.removeItem('strava_name');
    router.push('/?disconnected=1');
  }, [router]);

  return {
    showUserMenu, setShowUserMenu,
    userEmail, stravaName, lastSyncedAt,
    stravaSyncing, stravaSyncMsg,
    handleStravaConnect,
    handleStravaSync,
    handleLogout,
  };
}
