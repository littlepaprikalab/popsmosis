'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { HandThumbUpIcon as ThumbOutline } from '@heroicons/react/24/outline';
import { HandThumbUpIcon as ThumbSolid } from '@heroicons/react/24/solid';
import { api, ApiError, LikeState } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useToast } from './Toast';

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
export const formatCount = (n: number) => compact.format(Math.max(0, n));

/**
 * One person's thumbs-up on one droplet, shown at once and sent behind the scenes.
 *
 * What the person last asked for wins. One request is in flight at a time; when it comes
 * back and they have changed their mind since, the next one goes. So clicking fast can
 * never leave the page saying one thing and the API another, whatever order the network
 * answers in. The count shown is the API's last word, moved by the one click it has not
 * heard yet; a failure puts back what the API last said.
 */
export function useDropletLike(dropletId: string, initial: LikeState) {
  const { showToast } = useToast();
  const confirmed = useRef(initial); // what the API last said
  const desired = useRef(initial.liked); // what the person last asked for
  const running = useRef(false);
  const [view, setView] = useState(initial);

  const show = useCallback(() => {
    const c = confirmed.current;
    const want = desired.current;
    setView({ liked: want, likeCount: Math.max(0, c.likeCount - (c.liked ? 1 : 0) + (want ? 1 : 0)) });
  }, []);

  // A fresh read of the droplet replaces what this holds, unless a click is still on its way.
  useEffect(() => {
    if (running.current) return;
    confirmed.current = { liked: initial.liked, likeCount: initial.likeCount };
    desired.current = initial.liked;
    show();
  }, [initial.liked, initial.likeCount, show]);

  const sync = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    try {
      while (desired.current !== confirmed.current.liked) {
        confirmed.current = await api.setDropletLiked(dropletId, desired.current);
      }
    } catch (err) {
      desired.current = confirmed.current.liked;
      const gone = err instanceof ApiError && err.status === 404;
      showToast(gone ? 'This droplet is no longer on the Droplets tab.' : err instanceof Error ? err.message : 'Could not save your like', 'error');
    } finally {
      running.current = false;
      show();
    }
  }, [dropletId, show, showToast]);

  const toggle = useCallback(() => {
    desired.current = !desired.current;
    show();
    void sync();
  }, [show, sync]);

  return { ...view, toggle };
}

interface Props {
  dropletId: string;
  liked: boolean;
  likeCount: number;
  size?: 'sm' | 'md';
}

/** The thumbs-up and its count. Signed out, it is a way to sign in and come back here. */
export function LikeButton({ dropletId, liked, likeCount, size = 'sm' }: Props) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const like = useDropletLike(dropletId, { liked, likeCount });
  const icon = size === 'md' ? 'w-5 h-5' : 'w-4 h-4';
  const text = size === 'md' ? 'text-sm' : 'text-xs';
  const count = `${like.likeCount} ${like.likeCount === 1 ? 'like' : 'likes'}`;
  const base = `inline-flex items-center gap-1.5 rounded-full font-bold transition-colors ${text} ${size === 'md' ? 'px-3 py-1.5' : 'px-2 py-1'}`;
  const idle = { color: 'var(--text-secondary)', border: '1px solid var(--border)' };

  if (loading) {
    return (
      <span className={base} style={{ ...idle, opacity: 0.6 }} aria-label={count}>
        <ThumbOutline className={icon} aria-hidden /> {formatCount(like.likeCount)}
      </span>
    );
  }
  if (!user) {
    return (
      <Link href={`/auth/callback?returnTo=${encodeURIComponent(pathname)}`} className={`${base} hover-text`} style={idle}
        title="Sign in to like" aria-label={`${count}. Sign in to like this droplet`} data-testid="like-signin">
        <ThumbOutline className={icon} aria-hidden /> {formatCount(like.likeCount)}
      </Link>
    );
  }
  return (
    <button type="button" onClick={like.toggle} aria-pressed={like.liked} data-testid="like-button"
      aria-label={`${like.liked ? 'Unlike' : 'Like'} this droplet (${count})`} title={like.liked ? 'Unlike' : 'Like'}
      className={`${base} hover:brightness-110`}
      style={like.liked
        ? { color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)', background: 'rgba(50, 173, 230, 0.12)' }
        : idle}>
      {like.liked ? <ThumbSolid className={icon} aria-hidden /> : <ThumbOutline className={icon} aria-hidden />}
      <span data-testid="like-count">{formatCount(like.likeCount)}</span>
    </button>
  );
}
