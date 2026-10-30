'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type Announcement, type AnnouncementTone } from '@/lib/api';
import { useToast } from '@/components/Toast';
import { ANNOUNCEMENT_CHANGED } from '@/components/AnnouncementBanner';
import { timeAgo } from '@/lib/time';

// The longest the API takes (api/src/lib/announcement.ts, MAX_MESSAGE_LENGTH): about three lines on a phone.
const MAX_LENGTH = 500;

const TONES: { tone: AnnouncementTone; note: string }[] = [
  { tone: 'info', note: 'News: blue.' },
  { tone: 'warning', note: 'Something that does not work as it should, like songs being off: orange.' },
];

/**
 * /admin/studio: the announcement under the nav on every page (components/AnnouncementBanner.tsx). Putting one up
 * replaces the one before, and shows again to anyone who closed that one; every open page has it within a minute.
 */
export function AnnouncementEditor() {
  const { showToast } = useToast();
  const [current, setCurrent] = useState<Announcement | null | undefined>(undefined); // undefined: not read yet
  const [message, setMessage] = useState('');
  const [tone, setTone] = useState<AnnouncementTone>('info');
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const show = useCallback((announcement: Announcement | null) => {
    setCurrent(announcement);
    setMessage(announcement?.message ?? '');
    setTone(announcement?.tone ?? 'info');
  }, []);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      show((await api.getAnnouncement()).announcement);
    } catch {
      setFailed(true);
    }
  }, [show]);
  useEffect(() => { load(); }, [load]);

  const save = async (change: () => Promise<{ announcement: Announcement | null }>, done: string) => {
    if (saving) return;
    setSaving(true);
    try {
      const { announcement } = await change();
      show(announcement);
      window.dispatchEvent(new CustomEvent(ANNOUNCEMENT_CHANGED, { detail: announcement }));
      showToast(done, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to save the announcement', 'error');
    } finally {
      setSaving(false);
    }
  };
  const publish = (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    save(() => api.setAnnouncement(text, tone), current
      ? 'Announcement changed: every open page shows it within a minute.'
      : 'Announcement up: every open page shows it within a minute.');
  };
  const takeDown = () => save(api.clearAnnouncement, 'Announcement down: it leaves every open page within a minute.');

  const text = message.trim();
  const changed = !!text && (text !== current?.message || tone !== current?.tone);

  return (
    <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
      data-testid="announcement-editor">
      <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>Announcement</h3>
      <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
        A message under the nav on every page, for everyone, signed in or not: &ldquo;Songs are off until tomorrow: we
        are out of quota.&rdquo; Plain text. Each person can close it; a new or changed one shows again.
      </p>
      {current === undefined ? (
        failed ? (
          <button type="button" onClick={load} className="px-3 py-1 rounded text-xs font-bold uppercase tracking-wider"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>Try again</button>
        ) : (
          <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>Loading&hellip;</p>
        )
      ) : (
        <form onSubmit={publish} noValidate>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} disabled={saving} maxLength={MAX_LENGTH}
            rows={3} aria-label="Announcement" placeholder="Nothing is showing. Type a message to show everyone."
            className="w-full rounded px-2 py-1.5 text-sm resize-y"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="How the announcement looks">
              {TONES.map(({ tone: option, note }) => {
                const on = tone === option;
                return (
                  <button key={option} type="button" role="radio" aria-checked={on} disabled={saving} title={note}
                    onClick={() => setTone(option)}
                    className={`px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-colors rounded-full border ${
                      on ? 'text-[var(--btn-filled-text)] border-transparent' : 'text-[var(--accent-purple)] border-[var(--accent-purple)] hover:bg-[var(--accent-purple)]/10'
                    }`}
                    style={on ? { background: 'var(--accent-purple)' } : {}}>
                    {option}
                  </button>
                );
              })}
            </div>
            <span className="text-[10px] ml-auto tabular-nums" style={{ color: 'var(--text-muted)' }}>{text.length}/{MAX_LENGTH}</span>
            <button type="submit" disabled={saving || !changed}
              className="px-3 py-1 rounded text-xs font-bold uppercase tracking-wider disabled:opacity-50"
              style={{ color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)' }}>
              {current ? 'Change' : 'Show to everyone'}
            </button>
            {current && (
              <button type="button" onClick={takeDown} disabled={saving}
                className="px-3 py-1 rounded text-xs font-bold uppercase tracking-wider disabled:opacity-50"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                Take down
              </button>
            )}
          </div>
          <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }} data-testid="announcement-now">
            {current ? `Showing to everyone, put up ${timeAgo(current.updatedAt)}.` : 'Nothing is showing.'}{' '}
            {TONES.find((option) => option.tone === tone)?.note}
          </p>
        </form>
      )}
    </section>
  );
}
