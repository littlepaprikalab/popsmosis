import Link from 'next/link';
import { Wordmark } from '@/components/Wordmark';

export function Footer() {
  return (
    <footer
      className="w-full py-6 px-6 mt-auto flex flex-col items-center gap-3 text-center"
      style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}
    >
      {/* The bars go on a phone, where the links wrap and a bar would end a line */}
      <div className="flex gap-x-4 gap-y-2 text-xs flex-wrap justify-center" style={{ letterSpacing: 2 }}>
        <Link href="/learn" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          LEARN
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <Link href="/droplets" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          DROPLETS
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <Link href="/about" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          ABOUT
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <Link href="/how-its-made" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          HOW IT&rsquo;S MADE
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <Link href="/privacy" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          PRIVACY
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        {/* Washington's My Health My Data Act wants its own link, on the home page (every page has this footer) */}
        <Link href="/consumer-health-data" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          CONSUMER HEALTH DATA
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <Link href="/terms" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          TERMS
        </Link>
        <span className="hidden sm:inline" style={{ color: 'var(--border)' }}>|</span>
        <a href="https://github.com/littlepaprikalab/popsmosis" className="hover-text transition-colors" style={{ color: 'var(--text-muted)' }}>
          SOURCE
        </a>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1 }}>
        <Wordmark size="sm" />
        {' · droplets for learning. Every lesson is made by AI and shared with everyone; your requests stay private.'}
      </div>
    </footer>
  );
}
