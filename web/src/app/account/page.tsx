'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { UserAvatar } from '@/components/UserAvatar';
import { useToast } from '@/components/Toast';
import { Contact } from '@/components/LegalPage';

const card = { background: 'var(--bg-secondary)', border: '1px solid var(--border)' } as const;

/**
 * The signed-in person's account: what Popsmosis holds about them, and deleting it (DELETE /api/auth/me). The
 * Privacy Policy, section 7, sends people here.
 */
export default function AccountPage() {
  const { user, loading, unreachable, retry, logout } = useAuth();
  const { showToast } = useToast();
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const deleteAccount = async () => {
    setConfirming(false);
    setDeleting(true);
    try {
      await api.deleteAccount();
      await logout().catch(() => {}); // the cookie is already cleared; this resets the page's signed-in state
      showToast('Your account is deleted.', 'success');
      router.push('/droplets');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to delete your account', 'error');
      setDeleting(false);
    }
  };

  if (loading) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (!user) {
    return (
      <PageContainer maxWidth="xl" className="py-10 px-4 text-center">
        <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>Sign in to see your account.</p>
        <Link href="/auth/callback?returnTo=%2Faccount" className="inline-block px-6 py-3 rounded font-bold text-sm uppercase tracking-wider"
          style={{ background: 'var(--accent-green)', color: 'var(--btn-filled-text)' }}>
          Sign in with Google
        </Link>
      </PageContainer>
    );
  }

  return (
    <PageContainer maxWidth="xl" className="py-6 px-4">
      <ConfirmDialog
        open={confirming}
        title="Delete your account"
        message={
          <>
            <p className="mb-2">This deletes your account, your files, your follow-up questions and any request that has
              no lesson yet. It can&rsquo;t be undone.</p>
            <p>Lessons the teacher already made stay on the public Droplets tab, with nothing linking them to you.</p>
          </>
        }
        confirmLabel="Delete my account"
        variant="danger"
        onConfirm={deleteAccount}
        onCancel={() => setConfirming(false)}
      />

      <h2 className="text-2xl font-bold tracking-wider mb-6">ACCOUNT</h2>

      <section className="rounded-lg p-4 mb-4 flex items-center gap-3" style={card}>
        <UserAvatar name={user.name} avatarUrl={user.avatarUrl} size={40} />
        <div className="min-w-0">
          <p className="text-sm font-bold truncate" style={{ color: 'var(--text-primary)' }}>{user.name || 'No name'}</p>
          <p className="text-xs truncate" style={{ color: 'var(--text-secondary)' }} data-testid="account-email">{user.email}</p>
          <p className="text-[10px] uppercase tracking-wider mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {user.role === 'ADMIN' ? 'Admin · ' : ''}Joined {new Date(user.createdAt).toLocaleDateString()}
          </p>
        </div>
      </section>

      <section className="rounded-lg p-4 mb-4 text-sm" style={{ ...card, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
        <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--text-primary)' }}>Your data</h3>
        <p className="mb-2">
          Popsmosis holds your name, email address and photo from Google, and the requests, files and follow-ups you can
          see on <Link href="/learn" className="underline hover-text">Learn</Link>. Only you, the AI teacher and our
          admins can see them. The lessons made for you are public on the{' '}
          <Link href="/droplets" className="underline hover-text">Droplets tab</Link>, without your name or your words.
        </p>
        <p>
          For a copy of your data, email <Contact /> from this address. Read the{' '}
          <Link href="/privacy" className="underline hover-text">Privacy Policy</Link> and the{' '}
          <Link href="/terms" className="underline hover-text">Terms of Use</Link>.
        </p>
      </section>

      <section className="rounded-lg p-4 text-sm" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--accent-red)' }}>
        <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--accent-red)' }}>Delete my account</h3>
        <p className="mb-3" style={{ color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          Deletes your account, your files, your follow-ups and any request without a lesson. Lessons already made stay
          public, with nothing linking them to you. To delete only one request, use its <em>delete</em> button on Learn.
        </p>
        <button onClick={() => setConfirming(true)} disabled={deleting}
          className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--accent-red)', border: '1px solid var(--accent-red)' }}>
          {deleting ? 'Deleting…' : 'Delete my account'}
        </button>
      </section>
    </PageContainer>
  );
}
