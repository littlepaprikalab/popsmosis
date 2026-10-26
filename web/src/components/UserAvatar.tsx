export function UserAvatar({ name, avatarUrl, size = 20 }: { name: string | null | undefined; avatarUrl?: string | null; size?: number }) {
  const letter = (name || '?')[0].toUpperCase();
  if (avatarUrl) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={avatarUrl} alt="" width={size} height={size} className="rounded-full shrink-0" style={{ width: size, height: size }} referrerPolicy="no-referrer" />;
  }
  return (
    <div className="rounded-full flex items-center justify-center font-bold shrink-0"
      style={{ width: size, height: size, fontSize: Math.max(9, size * 0.5), background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
      {letter}
    </div>
  );
}

export function AgentAvatar({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <div className="rounded-full flex items-center justify-center font-bold shrink-0"
      style={{ width: size, height: size, fontSize: Math.max(9, size * 0.5), background: 'rgba(50, 173, 230, 0.15)', color: 'var(--accent-blue)', border: '1px solid rgba(50, 173, 230, 0.4)' }}>
      {(name || 'A')[0].toUpperCase()}
    </div>
  );
}
