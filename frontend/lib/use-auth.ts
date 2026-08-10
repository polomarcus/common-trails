'use client';

import { useState, useEffect, useCallback } from 'react';
import { isAuthenticated as checkAuth, getCurrentUserId, clearAuthState } from '@/lib/auth';
import { API_URL } from '@/lib/api-client';

interface AuthState {
  token: string | null;
  userId: string | null;
  isAuthenticated: boolean;
  logout: () => void;
  /** Refresh auth state from localStorage (e.g. after OAuth redirect). */
  refresh: () => void;
}

/**
 * Centralized auth hook.
 * Auth token is in httpOnly cookie; userId is in localStorage for UI state.
 */
export function useAuth(): AuthState {
  const [userId, setUserId] = useState<string | null>(() => getCurrentUserId());

  const refresh = useCallback(() => {
    setUserId(getCurrentUserId());
  }, []);

  // Detect auth state changes from other tabs
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === 'user_id') refresh();
    };
    window.addEventListener('storage', handler);
    return () => window.removeEventListener('storage', handler);
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_URL}/auth/logout`, { method: 'DELETE', credentials: 'include' });
    } catch { /* best-effort cookie clear */ }
    clearAuthState();
    setUserId(null);
  }, []);

  return {
    token: userId ? 'cookie-auth' : null,
    userId,
    isAuthenticated: !!userId,
    logout,
    refresh,
  };
}
