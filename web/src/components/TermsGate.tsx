'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { AgreeToTerms } from './AgreeToTerms';

// Where someone who has not agreed yet may still go: to read what they are asked to agree to, to delete their
// account instead, and to sign in.
const OPEN_PATHS = ['/terms', '/privacy', '/consumer-health-data', '/account', '/auth'];

/**
 * Asks a signed-in person to agree to the current Terms of Use and Privacy Policy before anything else, when their
 * account has no record of it (made before the sign-in box) or an older version (the Terms changed in a way people
 * must agree to again: api/src/lib/terms.ts). It cannot be dismissed: they agree, sign out, or delete their account.
 */
export function TermsGate() {
  const { user, login, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [agreed, setAgreed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = !!user && !user.termsCurrent && !OPEN_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    else if (!open && el.open) el.close();
  }, [open]);

  const agree = async () => {
    setSaving(true);
    setError(null);
    try {
      login(await api.acceptTerms());
      setAgreed(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save that. Try again.');
    } finally {
      setSaving(false);
    }
  };

  if (!user || user.termsCurrent) return null;
  const updated = !!user.termsAcceptedAt;

  return (
    <dialog
      ref={dialogRef}
      onCancel={(e) => e.preventDefault()} // Escape does not get past it
      aria-labelledby="terms-gate-title"
      data-testid="terms-gate"
      style={{ border: 'none', padding: 0, background: 'transparent', maxWidth: '92vw', width: '460px', margin: '12vh auto 0' }}
    >
      <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, color: 'var(--text-primary)', boxShadow: 'var(--dialog-shadow)' }}>
        <h3 id="terms-gate-title" className="text-base font-bold mb-2">
          {updated ? 'We have updated our Terms' : 'Before you go on'}
        </h3>
        <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          {updated
            ? 'Our Terms of Use or Privacy Policy changed since you last agreed. Please read them and agree to keep using Popsmosis.'
            : 'To keep using Popsmosis, please read and agree to our Terms of Use and Privacy Policy.'}
        </p>
        <div className="mb-4">
          <AgreeToTerms checked={agreed} onChange={setAgreed} />
        </div>
        {error && <p className="text-xs mb-3" style={{ color: 'var(--accent-red)' }}>{error}</p>}
        <div className="flex flex-wrap items-center justify-end gap-3">
          <Link href="/account" className="text-xs underline hover-text mr-auto" style={{ color: 'var(--text-muted)' }}>
            Delete my account instead
          </Link>
          <button type="button" onClick={async () => { await logout().catch(() => {}); router.push('/droplets'); }}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
            style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            Sign out
          </button>
          <button type="button" onClick={agree} disabled={!agreed || saving}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
            style={agreed && !saving
              ? { background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }
              : { background: 'var(--bg-tertiary)', color: 'var(--text-muted)', cursor: 'not-allowed' }}>
            {saving ? 'Saving…' : 'Agree and continue'}
          </button>
        </div>
      </div>
    </dialog>
  );
}
