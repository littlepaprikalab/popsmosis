import Link from 'next/link';
import { tagHref } from '@/lib/tags';

/**
 * A video's hashtags, the way a TikTok shows them. Each one opens the Droplets tab
 * searched by it. `max` shows only the first few, which the brain ranked most useful.
 */
export function TagList({ tags, max, label = 'Tags', className = '' }: { tags: string[]; max?: number; label?: string; className?: string }) {
  const shown = max === undefined ? tags : tags.slice(0, max);
  if (shown.length === 0) return null;
  return (
    <ul className={`flex flex-wrap gap-x-2 gap-y-0.5 ${className}`} aria-label={label} data-testid="tags">
      {shown.map((tag) => (
        <li key={tag} className="min-w-0">
          <Link href={tagHref(tag)} className="text-xs font-semibold hover:underline break-all" style={{ color: 'var(--accent-blue)' }}>
            #{tag}
          </Link>
        </li>
      ))}
    </ul>
  );
}
