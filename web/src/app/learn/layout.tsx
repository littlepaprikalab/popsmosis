import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Learn',
  description: 'Ask for any topic and get a droplet: a short lesson with a narrated video and a song.',
};

export default function LearnLayout({ children }: { children: React.ReactNode }) {
  return children;
}
