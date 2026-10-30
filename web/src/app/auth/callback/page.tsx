'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { AgreeToTerms } from '@/components/AgreeToTerms';
import { useAuth } from '@/lib/auth';
import { Wordmark } from '@/components/Wordmark';

function friendlyAuthError(msg: string): string {
  const lower = msg.toLowerCase();
  if (lower.includes('expired') || lower.includes('invalid_grant')) return 'Your sign-in session expired. Please try again.';
  if (lower.includes('not configured')) return msg;
  if (lower.includes('access_denied')) return 'Sign-in was cancelled. Click below to try again.';
  if (lower.includes('oauth state') || lower.includes('csrf')) return 'Sign-in session was interrupted. Please try again.';
  if (lower.includes('cannot reach the server')) return 'The server did not answer — it may be starting up. Please try again in a moment.';
  return msg;
}

/**
 * Where to go once signed in: a page of this site, or null. The value is resolved as the browser would resolve the
 * link and kept only when it stays on this origin, so `//evil.example`, `/\evil.example` (a backslash reads as a
 * slash) and `/<tab>/evil.example` (tabs and newlines are dropped) never send a learner off-site. What is kept is
 * the resolved path, query and fragment, never the text as given.
 */
function safeReturnTo(value: string | null): string | null {
  if (!value || !value.startsWith('/')) return null;
  let url: URL;
  try {
    url = new URL(value, window.location.origin);
  } catch {
    return null;
  }
  // A path the dot segments folded to "//evil.example" would be read as another host by whatever reads it next.
  if (url.origin !== window.location.origin || url.pathname.startsWith('//')) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

export default function AuthCallbackPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [processing, setProcessing] = useState(true);
  const [agreed, setAgreed] = useState(false); // the box: nobody goes on to Google without it
  const exchangeStarted = useRef(false);

  useEffect(() => {
    if (exchangeStarted.current) return; // React strict mode double-fires; codes are single-use

    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const returnedState = params.get('state');
    const urlError = params.get('error_description') || params.get('error');

    if (urlError) {
      setError(friendlyAuthError(decodeURIComponent(urlError)));
      setProcessing(false);
      return;
    }

    if (!code) {
      // Landing here to start sign-in. Remember where to go afterwards.
      const returnTo = safeReturnTo(params.get('returnTo'));
      if (returnTo) sessionStorage.setItem('auth_return_to', returnTo);
      if (params.get('reason') === 'session_expired') setError('Your session has expired. Please sign in again.');
      setProcessing(false);
      return;
    }

    const savedState = sessionStorage.getItem('oauth_state');
    if (!returnedState || returnedState !== savedState) {
      setError('Invalid OAuth state. Please try signing in again.');
      setProcessing(false);
      return;
    }
    sessionStorage.removeItem('oauth_state');
    exchangeStarted.current = true;

    (async () => {
      try {
        // The code is single-use, so it must not be spent on an API that is
        // still waking up: this GET retries the gateway errors that come back
        // while Render has no instance to route to, and the exchange follows a
        // server that has just answered.
        await api.health().catch(() => {});
        // The box was ticked on this page before Google; the API refuses the sign-in without it, and records it.
        const acceptTerms = sessionStorage.getItem('terms_agreed') === '1';
        sessionStorage.removeItem('terms_agreed');
        const result = await api.authGoogle(code, acceptTerms);
        login(result.user);
        const dest = safeReturnTo(sessionStorage.getItem('auth_return_to')) ?? '/learn';
        sessionStorage.removeItem('auth_return_to');
        router.push(dest);
      } catch (err) {
        setError(friendlyAuthError(err instanceof Error ? err.message : 'Authentication failed'));
        setProcessing(false);
      }
    })();
  }, [login, router]);

  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

  const handleSignIn = () => {
    const redirectUri = process.env.NEXT_PUBLIC_GOOGLE_REDIRECT_URI
      || `${process.env.NEXT_PUBLIC_SITE_URL || window.location.origin}/auth/callback`;
    if (!clientId) {
      setError('Google OAuth is not configured. Set NEXT_PUBLIC_GOOGLE_CLIENT_ID.');
      return;
    }
    if (!agreed) return;
    // Carried across Google's redirect, like the state, and sent with the code (above).
    sessionStorage.setItem('terms_agreed', '1');
    const state = crypto.randomUUID();
    sessionStorage.setItem('oauth_state', state);
    const params = new URLSearchParams({
      client_id: clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: 'openid email profile',
      prompt: 'select_account',
      state,
    });
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
  };

  const button = (label: string) => (
    <>
      <div className="mb-4">
        <AgreeToTerms checked={agreed} onChange={setAgreed} />
      </div>
      <button onClick={handleSignIn} disabled={!agreed}
        className="inline-block px-6 py-3 rounded font-bold text-sm uppercase tracking-wider transition-colors"
        style={agreed
          ? { background: 'var(--accent-brand)', color: 'var(--accent-brand-text)', cursor: 'pointer' }
          : { background: 'var(--bg-tertiary)', color: 'var(--text-muted)', cursor: 'not-allowed' }}>
        {label}
      </button>
    </>
  );

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
      <div className="text-center p-8 rounded-lg max-w-md animate-fade-in"
        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h1 className="mb-3"><Wordmark size="lg" /></h1>
        <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
          Droplets for learning.
        </p>

        {error ? (
          <>
            <div className="mb-4 p-3 rounded text-sm" style={{ background: 'rgba(255, 59, 48, 0.12)', color: 'var(--accent-red)' }}>
              {error}
            </div>
            {button('Try Again')}
          </>
        ) : processing ? (
          <p style={{ color: 'var(--text-muted)' }}>Authenticating...</p>
        ) : !clientId ? (
          <>
            <div className="mb-4 p-3 rounded text-sm" style={{ background: 'rgba(255, 59, 48, 0.12)', color: 'var(--accent-red)' }}>
              Google OAuth is not configured. Set NEXT_PUBLIC_GOOGLE_CLIENT_ID.
            </div>
            {button('Sign in with Google')}
          </>
        ) : (
          button('Sign in with Google')
        )}

        <p className="text-xs mt-6" style={{ color: 'var(--text-muted)' }}>
          Sign in to ask for lessons and follow up on them. The lessons are public; your requests and follow-ups stay
          private. Comments you post on a droplet are public.
        </p>
      </div>
    </div>
  );
}
