import { formatCount } from '@/components/LikeButton';

/** "1 view", "842 views", "1.2K views": a video's views, beside it (lib/plays.ts, useViews). */
export function viewsLabel(views: number) {
  return `${formatCount(views)} ${views === 1 ? 'view' : 'views'}`;
}

export function ViewCount({ views, className = '' }: { views: number; className?: string }) {
  return (
    <span className={`text-[11px] tabular-nums ${className}`} style={{ color: 'var(--text-muted)' }} data-testid="view-count">
      {viewsLabel(views)}
    </span>
  );
}
