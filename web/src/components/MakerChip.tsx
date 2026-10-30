/** Each maker's colour, from the theme's accents so it holds in light and dark. A maker not here is grey. */
const MAKER_COLORS: Record<string, string> = {
  Meta: 'var(--accent-blue)',
  Anthropic: 'var(--accent-orange)',
  Google: 'var(--accent-green)',
  ElevenLabs: 'var(--accent-purple)',
  FFmpeg: 'var(--accent-pink)',
};

const tint = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;

/**
 * Who made a model or tool, as a chip in the maker's colour: on the bill of materials (DropletBreakdown) and in
 * what made a droplet (MadeWith). Nothing when the API does not know.
 */
export function MakerChip({ maker, className = '' }: { maker: string | null; className?: string }) {
  if (!maker) return null;
  const color = MAKER_COLORS[maker];
  const style = color
    ? { background: tint(color, 14), border: `1px solid ${tint(color, 45)}`, color }
    : { background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' };
  return (
    <span data-testid="maker" style={style}
      className={`inline-block align-middle whitespace-nowrap text-[9px] uppercase px-1.5 py-px rounded-full font-semibold ${className}`}>
      {maker}
    </span>
  );
}
