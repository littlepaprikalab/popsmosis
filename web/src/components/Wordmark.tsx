// In rem, so the mark grows with a reader's text-size setting like the rest of the
// page; padding and corners in em, so every size keeps the same proportions. The large
// one also stops at 9vw: at 200% text on a 320px phone it would be wider than the screen.
const FONT_SIZES = { sm: '0.75rem', md: '1.0625rem', lg: 'min(1.625rem, 9vw)' } as const;

/**
 * The POPsmosis wordmark: "POP" heavy and "smosis" lighter, white on the aqua chip.
 * The chip keeps its color in both themes, like the tab icon (src/app/icon.svg).
 */
export function Wordmark({ size = 'md' }: { size?: keyof typeof FONT_SIZES }) {
  return (
    <span
      className="inline-block whitespace-nowrap align-middle leading-none"
      style={{
        fontSize: FONT_SIZES[size],
        padding: '0.3em 0.55em',
        borderRadius: '0.3em',
        background: 'var(--brand-chip)',
        color: '#ffffff',
        letterSpacing: 0,
      }}
    >
      <span style={{ fontWeight: 800 }}>POP</span>
      <span style={{ fontWeight: 400, opacity: 0.8 }}>smosis</span>
    </span>
  );
}
