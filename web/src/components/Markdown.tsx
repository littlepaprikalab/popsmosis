'use client';

import { memo } from 'react';
import dynamic from 'next/dynamic';
import type { Components } from 'react-markdown';

// Where a lesson or reply may show a picture from: this site, or where the droplets' files are (NEXT_PUBLIC_MEDIA_URL,
// and NEXT_PUBLIC_SUPABASE_URL while a droplet still plays from there; built in). The Content-Security-Policy
// (next.config.ts) blocks every other host; this shows such a picture as its words instead of a broken image, and
// never asks the other host for it.
const MEDIA_ORIGINS = [process.env.NEXT_PUBLIC_MEDIA_URL, process.env.NEXT_PUBLIC_SUPABASE_URL].filter((o): o is string => !!o);

export function pictureAllowed(src: unknown, here: string, media: readonly string[] = MEDIA_ORIGINS): boolean {
  if (typeof src !== 'string' || !src) return false;
  try {
    const origin = new URL(src, here).origin;
    return origin === new URL(here).origin || media.includes(origin);
  } catch {
    return false;
  }
}

// Links in a lesson or reply were written by a model or a learner, never vouched for by popsmosis: search engines are
// told so (nofollow ugc), and the page they open neither gets a handle on this one nor learns which page sent it.
export const LINK_REL = 'nofollow ugc noopener noreferrer';

const components: Components = {
  // Wide tables scroll inside the card rather than stretching it.
  table: ({ node: _node, ...props }) => <div className="md-table"><table {...props} /></div>,
  a: ({ node: _node, ...props }) => <a {...props} rel={LINK_REL} />,
  img: ({ node: _node, src, alt, ...props }) => {
    const here = typeof window === 'undefined' ? 'http://localhost/' : window.location.href;
    if (pictureAllowed(src, here)) {
      // eslint-disable-next-line @next/next/no-img-element
      return <img src={src} alt={alt ?? ''} loading="lazy" referrerPolicy="no-referrer" {...props} />;
    }
    return <span className="md-image-left-out" data-testid="md-image-left-out">{alt ? `[picture: ${alt}]` : '[picture]'}</span>;
  },
};

/**
 * Lazy-loaded ReactMarkdown wrapper — keeps react-markdown out of the initial
 * page bundle. The library is loaded on demand when first rendered, together
 * with remark-gfm: agents write GitHub-flavored markdown (tables, strikethrough,
 * task lists, bare links), which plain CommonMark leaves as literal text.
 */
const Markdown = dynamic(async () => {
  const [{ default: ReactMarkdown }, { default: remarkGfm }] = await Promise.all([
    import('react-markdown'),
    import('remark-gfm'),
  ]);
  const remarkPlugins = [remarkGfm];
  // Memoized on the text: a card folding or a count changing re-renders the page around a lesson, and
  // parsing the same markdown again each time would be wasted work.
  return memo(function Markdown({ children }: { children: string }) {
    return <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>{children}</ReactMarkdown>;
  });
}, {
  loading: () => <span />,
});

export default Markdown;
