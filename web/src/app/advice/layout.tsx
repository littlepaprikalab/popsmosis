import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Advice',
  description: 'Ask a question, get an answer from the AI advisor, and talk it through.',
};

export default function AdviceLayout({ children }: { children: React.ReactNode }) {
  return children;
}
