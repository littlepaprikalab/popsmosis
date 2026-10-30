import type { MadeWithMaker } from '@/lib/api';
import { MakerChip } from '@/components/MakerChip';

/**
 * What made a droplet, from its bill of materials (api/src/lib/breakdown.ts, madeWithOf): each maker's chip with its
 * models after it, by the name and version the studio ran, so anyone watching can tell what made it and compare one
 * made later. `compact`, for a card on the list, shows the chips alone, each naming its models when hovered and to a
 * screen reader. Nothing when the API does not say (a droplet made before the bills, or an older API).
 */
export function MadeWith({ makers, compact = false, className = '' }: {
  makers: MadeWithMaker[] | null | undefined; compact?: boolean; className?: string;
}) {
  if (!makers?.length) return null;
  return (
    <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${className}`} data-testid="made-with">
      <span aria-hidden className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Made with</span>
      <ul aria-label="Made with" className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {makers.map((m) => {
          const models = m.models.join(', ');
          // A model no maker is listed for has no chip to stand for it, so its name shows even on a card.
          const named = !compact || !m.maker;
          return (
            <li key={m.maker ?? ''} title={named ? undefined : models} className="inline-flex items-center gap-1 text-[11px] min-w-0">
              <MakerChip maker={m.maker} />
              {named
                ? <span className="break-all" style={{ color: 'var(--text-secondary)' }}>{models}</span>
                : <span className="sr-only">{models}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
