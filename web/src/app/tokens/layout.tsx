import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Access tokens',
  description: 'Personal access tokens for signing in to zoey from something that is not a browser.',
};

export default function TokensLayout({ children }: { children: React.ReactNode }) {
  return children;
}
