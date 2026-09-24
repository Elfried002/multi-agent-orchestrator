/**
 * Contexte de session administrateur.
 *
 * L'état de session provient EXCLUSIVEMENT de `GET /api/v1/auth/me` : aucun
 * jeton n'est stocké dans localStorage ou sessionStorage (SECURITY.md §13), le
 * backend posant un cookie HttpOnly (SECURITY.md §4).
 *
 * États possibles :
 * - `loading`     : sonde de session en cours ;
 * - `authenticated` : session valide ;
 * - `anonymous`   : aucune session (401) → redirection vers /login ;
 * - `unavailable` : backend injoignable → écran d'erreur avec nouvelle tentative
 *   (distinct d'un « non connecté », pour ne pas masquer une panne réseau).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { AdminSession } from '../types';
import {
  changePassword as requestPasswordChange,
  getCurrentSession,
  login as requestLogin,
  logout as requestLogout,
} from '../services/auth';
import { isApiError, setUnauthorizedHandler } from '../services/api';
import { describeError } from '../utils/errors';

export type SessionStatus = 'loading' | 'authenticated' | 'anonymous' | 'unavailable';

export interface SessionContextValue {
  status: SessionStatus;
  admin: AdminSession | null;
  error: string | null;
  refresh: () => Promise<void>;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>('loading');
  const [admin, setAdmin] = useState<AdminSession | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const session = await getCurrentSession();
      setAdmin(session);
      setStatus('authenticated');
      setError(null);
    } catch (caught: unknown) {
      setAdmin(null);
      if (isApiError(caught) && caught.status === 401) {
        setStatus('anonymous');
        setError(null);
        return;
      }
      if (isApiError(caught) && caught.isNetworkError) {
        setStatus('unavailable');
        setError(describeError(caught));
        return;
      }
      setStatus('anonymous');
      setError(describeError(caught));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Expiration de session détectée par n'importe quel appel API : 401 global.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAdmin(null);
      setStatus('anonymous');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (username: string, password: string): Promise<void> => {
    await requestLogin(username, password);
    const session = await getCurrentSession();
    setAdmin(session);
    setStatus('authenticated');
    setError(null);
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    try {
      await requestLogout();
    } finally {
      setAdmin(null);
      setStatus('anonymous');
    }
  }, []);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string): Promise<void> => {
      await requestPasswordChange(currentPassword, newPassword);
    },
    [],
  );

  const value = useMemo<SessionContextValue>(
    () => ({ status, admin, error, refresh, signIn, signOut, changePassword }),
    [status, admin, error, refresh, signIn, signOut, changePassword],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error('useSession doit être utilisé dans un <SessionProvider>.');
  return context;
}
