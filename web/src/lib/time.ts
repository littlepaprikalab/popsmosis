/** Format a date as a day label for grouping (e.g. "Today", "Yesterday", "Mar 15") */
export function dayLabel(date: string | Date): string {
  const d = new Date(date);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.floor((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/**
 * A moment soon, by this browser's clock and in its time zone: "at 3:42 PM", "tomorrow at 9:15 AM", or further off
 * "on Thu, Nov 5 at 9:15 AM". Rounded up to the minute, so it never names a time before the moment itself.
 */
export function clockLabel(at: number, now = Date.now()): string {
  const d = new Date(Math.ceil(at / 60_000) * 60_000);
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const day = new Date(now);
  if (d.toDateString() === day.toDateString()) return `at ${time}`;
  day.setDate(day.getDate() + 1);
  if (d.toDateString() === day.toDateString()) return `tomorrow at ${time}`;
  return `on ${d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} at ${time}`;
}

/** Format a date as a relative time string (e.g. "2h ago", "3d ago") */
export function timeAgo(date: string | Date): string {
  const now = Date.now();
  const then = new Date(date).getTime();
  const seconds = Math.floor((now - then) / 1000);

  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}
