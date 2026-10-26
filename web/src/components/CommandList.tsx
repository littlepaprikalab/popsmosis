import type { Command } from '@/lib/api';

/**
 * Commands the advisor attached for the asker's editor to run. This page cannot run them, so it lists them and says
 * where they run: Zed's Zoey panel, which shows the same list with a Play button beside it.
 */
export default function CommandList({ commands }: { commands: Command[] }) {
  if (commands.length === 0) return null;
  return (
    <div className="mt-2 rounded px-2.5 py-2" style={{ border: '1px dashed var(--border)' }} data-testid="commands">
      <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: 'var(--text-muted)' }}>
        {commands.length === 1 ? '1 command' : `${commands.length} commands`} — run from Zed&apos;s Zoey panel
      </p>
      <ol className="space-y-1 list-decimal list-inside">
        {commands.map((c, i) => (
          <li key={i} className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <code className="break-all px-1 rounded" style={{ color: 'var(--text-primary)', background: 'var(--code-block-bg)' }}>{c.command}</code>
            {c.description && <span className="block text-[11px] ml-4" style={{ color: 'var(--text-secondary)' }}>{c.description}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
