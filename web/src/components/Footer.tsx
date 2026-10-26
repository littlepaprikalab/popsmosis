import Link from 'next/link';

export function Footer() {
  return (
    <footer
      className="w-full py-6 px-6 mt-auto flex flex-col items-center gap-3 text-center"
      style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}
    >
      <div className="flex gap-4 text-xs flex-wrap justify-center" style={{ letterSpacing: 2 }}>
        <Link href="/learn" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          LEARN
        </Link>
        <span style={{ color: 'var(--border)' }}>|</span>
        <a href="https://github.com/littlepaprikalab/popsmosis" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          SOURCE
        </a>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1 }}>
        <span style={{ fontWeight: 700, color: 'var(--accent-red)' }}>POPSMOSIS</span>
        {' · droplets for learning. Lessons are written by an AI teacher.'}
      </div>
    </footer>
  );
}
