import Link from 'next/link';
import PageContainer from '@/components/PageContainer';
import { CONTACT_EMAIL, LAST_UPDATED } from '@/lib/legal';

/** The frame of /privacy and /terms: the title, the date, a short version up top, then numbered sections. */
export function LegalPage({ title, summary, children }: { title: string; summary: React.ReactNode; children: React.ReactNode }) {
  return (
    <PageContainer maxWidth="3xl" className="py-8 px-4">
      <article className="legal">
        <h1 className="text-2xl font-bold tracking-wider mb-1" style={{ color: 'var(--text-primary)' }}>{title}</h1>
        <p className="text-xs mb-6" style={{ color: 'var(--text-muted)' }}>Last updated {LAST_UPDATED}</p>
        <section className="rounded-lg p-4 mb-8" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
          aria-labelledby="short-version">
          <h2 id="short-version" className="legal-short">The short version</h2>
          {summary}
        </section>
        {children}
        <p className="text-xs mt-10" style={{ color: 'var(--text-muted)' }}>
          See also: <Link href="/privacy">Privacy Policy</Link> · <Link href="/terms">Terms of Use</Link> · <Link href="/account">Your account</Link>
        </p>
      </article>
    </PageContainer>
  );
}

/** A numbered section with an anchor, so a page can link to #license or #delete. */
export function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}><a href={`#${id}`} className="legal-anchor">{title}</a></h2>
      {children}
    </section>
  );
}

/** The contact address as a link, or, until one is set in lib/legal.ts, a notice where it will go. */
export function Contact() {
  if (CONTACT_EMAIL) return <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>;
  return <span data-testid="contact-missing" style={{ color: 'var(--accent-orange)' }}>[contact email not set yet]</span>;
}
