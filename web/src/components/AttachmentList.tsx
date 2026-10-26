import type { Attachment } from '@/lib/api';

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The files a person sent with a question or reply (a screenshot pasted into Zed's Zoey panel, a log): an image as a
 * thumbnail opening it whole, anything else as a link. One past the retention is named, greyed, with nothing to open.
 */
export default function AttachmentList({ attachments }: { attachments: Attachment[] }) {
  if (attachments.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2" data-testid="attachments">
      {attachments.map((a) => {
        const label = `${a.name} (${sizeLabel(a.size)})`;
        if (a.expired) {
          return (
            <span key={a.id} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-muted)', border: '1px dashed var(--border)' }} title="No longer kept">
              {label} — expired
            </span>
          );
        }
        if (a.mimeType.startsWith('image/')) {
          return (
            <a key={a.id} href={a.url} target="_blank" rel="noopener noreferrer" title={label} className="block rounded overflow-hidden" style={{ border: '1px solid var(--border)' }}>
              <img src={a.url} alt={a.name} loading="lazy" className="block max-h-40 max-w-[16rem] object-contain" />
            </a>
          );
        }
        return (
          <a key={a.id} href={a.url} target="_blank" rel="noopener noreferrer" className="text-xs px-2 py-1 rounded underline break-all"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--border)' }}>
            {label}
          </a>
        );
      })}
    </div>
  );
}
