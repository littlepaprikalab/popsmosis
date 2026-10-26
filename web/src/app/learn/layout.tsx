import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Learn',
  description: 'Ask for any topic and get a droplet: a short lesson you can ask follow-ups under.',
};

export default function LearnLayout({ children }: { children: React.ReactNode }) {
  return children;
}
