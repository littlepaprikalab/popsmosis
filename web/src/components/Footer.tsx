import Link from 'next/link';

export function Footer() {
  return (
    <footer
      className="w-full py-6 px-6 mt-auto flex flex-col items-center gap-3 text-center"
      style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}
    >
      <div className="flex gap-4 text-xs flex-wrap justify-center" style={{ letterSpacing: 2 }}>
        <Link href="/advice" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          ADVICE
        </Link>
        <span style={{ color: 'var(--border)' }}>|</span>
        <a href="https://github.com/Iztera/zoey" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          SOURCE
        </a>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1 }}>
        <span style={{ fontWeight: 700, color: 'var(--accent-red)' }}>ZOEY</span>
        {' · Q&A support. Answers are written by an AI advisor.'}
      </div>
    </footer>
  );
}
