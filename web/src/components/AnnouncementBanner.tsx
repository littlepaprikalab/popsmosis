'use client';

import { useCallback, useEffect, useState } from 'react';
import { ExclamationTriangleIcon, InformationCircleIcon, XMarkIcon } from '@heroicons/react/20/solid';
import { api, type Announcement, type AnnouncementTone } from '@/lib/api';
import { usePolling } from '@/lib/usePolling';

// How often an open page asks again. The API keeps the announcement 10 seconds, so one put up or taken down on
// /admin/studio reaches every open page within about a minute, and a page coming back into view asks at once.
const POLL_MS = 60_000;
const DISMISSED_KEY = 'popsmosis.announcement-dismissed';

/** Sent by /admin/studio with the announcement it just saved (or null), so the admin's own page shows it at once. */
export const ANNOUNCEMENT_CHANGED = 'announcement-changed';

const TONES: Record<AnnouncementTone, { icon: typeof InformationCircleIcon; color: string; tint: string }> = {
  info: { icon: InformationCircleIcon, color: 'var(--accent-blue)', tint: 'bg-[var(--accent-blue)]/10' },
  warning: { icon: ExclamationTriangleIcon, color: 'var(--accent-orange)', tint: 'bg-[var(--accent-orange)]/15' },
};

/** The announcement this browser closed, by when it was put up; null when none or storage is blocked. */
function dismissedAt(): string | null {
  try {
    return window.localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
}

/**
 * The announcement an admin set on /admin/studio, under the nav on every page, for everyone, signed in or not. Closing
 * it hides that one on this browser; the next one put up shows again. While the API cannot be reached, the last one
 * read stays.
 */
export function AnnouncementBanner() {
  const [announcement, setAnnouncement] = useState<Announcement | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);

  const load = useCallback(() => api.getAnnouncement().then((res) => setAnnouncement(res.announcement)).catch(() => {}), []);
  useEffect(() => {
    setDismissed(dismissedAt());
    load();
    const onChanged = (event: Event) => setAnnouncement((event as CustomEvent<Announcement | null>).detail);
    window.addEventListener(ANNOUNCEMENT_CHANGED, onChanged);
    return () => window.removeEventListener(ANNOUNCEMENT_CHANGED, onChanged);
  }, [load]);
  usePolling(load, POLL_MS);

  if (!announcement || dismissed === announcement.updatedAt) return null;
  const { icon: Icon, color, tint } = TONES[announcement.tone] ?? TONES.info;
  const dismiss = () => {
    setDismissed(announcement.updatedAt);
    try {
      window.localStorage.setItem(DISMISSED_KEY, announcement.updatedAt);
    } catch {
      // Private mode or blocked storage: closed until the page is loaded again.
    }
  };

  return (
    <div role="status" className={`${tint} px-6 py-2.5`} style={{ borderBottom: `1px solid ${color}` }} data-testid="announcement">
      <div className="max-w-4xl mx-auto flex items-start gap-2.5">
        <Icon className="w-[18px] h-[18px] shrink-0 mt-px" style={{ color }} aria-hidden="true" />
        <p className="flex-1 min-w-0 text-sm whitespace-pre-line break-words" style={{ color: 'var(--text-primary)' }}>
          {announcement.message}
        </p>
        <button type="button" onClick={dismiss} aria-label="Close the announcement" title="Close"
          className="shrink-0 -m-1 p-1 rounded transition-colors text-[var(--text-muted)] hover:text-[var(--text-hover)]">
          <XMarkIcon className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
