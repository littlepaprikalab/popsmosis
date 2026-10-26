export function LoadingMessage({ text = 'Loading...' }: { text?: string }) {
  return (
    <div className="text-center py-20 animate-loading-pulse" style={{ color: 'var(--text-muted)' }}>
      {text}
    </div>
  );
}
