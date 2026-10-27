const SIZES = {
  sm: { fontSize: 12, padding: '3px 7px', borderRadius: 4 },
  md: { fontSize: 17, padding: '5px 9px', borderRadius: 5 },
  lg: { fontSize: 26, padding: '8px 14px', borderRadius: 8 },
} as const;

/**
 * The POPsmosis wordmark: "POP" heavy and "smosis" lighter, white on the aqua chip.
 * The chip keeps its color in both themes, like the tab icon (public/icon.svg).
 */
export function Wordmark({ size = 'md' }: { size?: keyof typeof SIZES }) {
  return (
    <span
      className="inline-block whitespace-nowrap align-middle leading-none"
      style={{ ...SIZES[size], background: 'var(--brand-chip)', color: '#ffffff', letterSpacing: 0 }}
    >
      <span style={{ fontWeight: 800 }}>POP</span>
      <span style={{ fontWeight: 400, opacity: 0.8 }}>smosis</span>
    </span>
  );
}
