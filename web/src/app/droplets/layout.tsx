import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Droplets',
  description: 'Every finished droplet, free to watch: an AI-checked lesson, its song and its lesson video. No sign-in needed.',
};

export default function DropletsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
