'use client';

import dynamic from 'next/dynamic';
import type { Components } from 'react-markdown';

// Wide tables scroll inside the card rather than stretching it.
const components: Components = {
  table: ({ node: _node, ...props }) => <div className="md-table"><table {...props} /></div>,
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
  return function Markdown({ children }: { children: string }) {
    return <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>{children}</ReactMarkdown>;
  };
}, {
  loading: () => <span />,
});

export default Markdown;
