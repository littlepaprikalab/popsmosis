'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from 'react';
import { api, isUnreachable, User } from './api';
import { topicDraft } from '@/lib/topicDraft';
import { forgetSorry } from '@/lib/brains';

interface AuthContext {
  user: User | null;
  loading: boolean;
  unreachable: boolean; // the API never answered; says nothing about the session
  login: (user: User) => void;
  logout: () => Promise<void>;
  retry: () => Promise<void>;
}

const AuthCtx = createContext<AuthContext>({
  user: null,
  loading: true,
  unreachable: false,
  login: () => {},
  logout: async () => {},
  retry: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [unreachable, setUnreachable] = useState(false);
  const userRef = useRef(user);
  userRef.current = user;

  // The cookie is httpOnly; the only way to know if we are signed in is to ask.
  // A 401 is that answer. An API that never answered — spun down, mid-deploy —
  // is not, so it must not drop the page to the signed-out view: the session
  // cookie is still there and the API is the thing that is missing.
  const loadMe = useCallback(async () => {
    setLoading(true);
    try {
      setUser(await api.getMe());
      setUnreachable(false);
    } catch (err) {
      if (isUnreachable(err)) {
        setUnreachable(true);
      } else {
        setUser(null); // not signed in
        setUnreachable(false);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadMe(); }, [loadMe]);

  useEffect(() => {
    // Fired by the api client on 401s that mean the session expired. Only react
    // when the UI currently shows a signed-in user; redirect exactly once.
    let handled = false;
    const onExpired = () => {
      if (handled || !userRef.current) return;
      handled = true;
      forgetSorry(); // as a logout does: whoever signs in next on this tab says sorry for themselves
      setUser(null);
      api.logout().catch(() => {});
      const returnTo = `${window.location.pathname}${window.location.search}`;
      window.location.href = `/auth/callback?reason=session_expired&returnTo=${encodeURIComponent(returnTo)}`;
    };
    window.addEventListener('session-expired', onExpired);
    return () => window.removeEventListener('session-expired', onExpired);
  }, []);

  const login = (u: User) => { setUser(u); setUnreachable(false); }; // cookie already set by the API

  const logout = async () => {
    // Their apology for Muse Wonder (lib/brains.ts) goes first: if the call below fails, it costs only another apology.
    forgetSorry();
    await api.logout();
    topicDraft.clear(); // an unsent topic is theirs: not for whoever uses this tab next
    setUser(null);
  };

  return (
    <AuthCtx.Provider value={{ user, loading, unreachable, login, logout, retry: loadMe }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
