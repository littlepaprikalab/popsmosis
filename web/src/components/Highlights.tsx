// Words that pop out of a sentence: the About page and the sign-in card use them to say what a droplet is.

export const tint = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;
// An accent for small text: blended toward the text colour, so it darkens in the light theme and lightens in the dark
// one, enough to read at 4.5:1 in both (yellow needs the most).
export const ink = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, var(--text-primary))`;

/** A word as a pill: tinted rather than solid, so it sits beside the POPsmosis chip without copying it. */
export function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-[0.3em] whitespace-nowrap align-middle leading-none rounded-full font-bold"
      style={{
        fontSize: '0.75rem',
        padding: '0.3em 0.7em',
        color: ink(color, 85),
        background: tint(color, 14),
        border: `1px solid ${tint(color, 45)}`,
      }}>
      {children}
    </span>
  );
}

/** "droplet" as a pill in the brand aqua, with the drop from the Droplets heading. */
export function DropletPill() {
  return (
    <Pill color="var(--accent-brand)">
      <svg aria-hidden="true" viewBox="18 10 28 42" style={{ height: '1em', width: 'auto', marginLeft: '-0.15em' }}>
        <path d="M32 12.5C32 12.5 20.3 28 20.3 38a11.7 11.7 0 0 0 23.4 0C43.7 28 32 12.5 32 12.5Z" fill="currentColor" />
      </svg>
      droplet
    </Pill>
  );
}

/** "song" on the wordmark's chip, white on the same aqua as POP: the POP in POPsmosis is the song. */
export function SongChip() {
  return (
    <span className="inline-block whitespace-nowrap align-middle leading-none"
      style={{ fontSize: '0.75rem', fontWeight: 800, padding: '0.3em 0.55em', borderRadius: '0.3em', background: 'var(--brand-chip)', color: '#ffffff' }}>
      song
    </span>
  );
}

/** A highlighter stroke across the lower half of the words. */
export function Marked({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <mark style={{
      color: 'var(--text-primary)',
      background: `linear-gradient(transparent 55%, ${tint(color, 50)} 55%)`,
      padding: '0 0.1em',
    }}>{children}</mark>
  );
}
