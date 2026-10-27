import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Account',
  description: 'Your Popsmosis account: what it holds about you, and deleting it.',
};

export default function AccountLayout({ children }: { children: React.ReactNode }) {
  return children;
}
