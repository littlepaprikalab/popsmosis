'use client';

import Link from 'next/link';
import { MIN_AGE } from '@/lib/legal';

/**
 * The box a person ticks to agree to the Terms of Use and Privacy Policy: on the sign-in page before Google, and in
 * TermsGate when the Terms have changed since they last agreed. The API records the version and the time
 * (api/src/lib/terms.ts). The words name what matters most, arbitration and the class action waiver, next to the box
 * itself, in readable type: what a court looks for before it holds someone to terms they clicked through.
 */
export function AgreeToTerms({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex items-start gap-2.5 text-left text-xs cursor-pointer" style={{ color: 'var(--text-primary)', lineHeight: 1.6 }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 w-4 h-4 shrink-0 cursor-pointer"
        style={{ accentColor: 'var(--accent-brand)' }}
        data-testid="agree-terms"
      />
      <span>
        I agree to the{' '}
        <Link href="/terms" target="_blank" rel="noopener" className="underline" style={{ color: 'var(--accent-blue)' }}>Terms of Use</Link>,
        including <strong>binding individual arbitration and a waiver of class actions</strong>{' '}
        (<Link href="/terms#disputes" target="_blank" rel="noopener" className="underline" style={{ color: 'var(--accent-blue)' }}>section 17</Link>),
        and I have read the{' '}
        <Link href="/privacy" target="_blank" rel="noopener" className="underline" style={{ color: 'var(--accent-blue)' }}>Privacy Policy</Link>.
        I am {MIN_AGE} or older.
      </span>
    </label>
  );
}
