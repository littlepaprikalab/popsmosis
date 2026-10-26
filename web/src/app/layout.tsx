import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/lib/auth';
import { Nav } from '@/components/Nav';
import { Footer } from '@/components/Footer';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { AccessGate } from '@/components/AccessGate';
import { ToastProvider } from '@/components/Toast';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'ZOEY — Q&A support',
    template: '%s — ZOEY',
  },
  description: 'Ask a question. An AI advisor answers it, and you can talk it through.',
  applicationName: 'ZOEY',
  icons: { icon: '/icon.svg' },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="darkreader-lock" />
      </head>
      <body className="min-h-screen flex flex-col" suppressHydrationWarning>
        <AuthProvider>
          <ToastProvider>
            <Nav />
            <main className="flex-1">
              <ErrorBoundary>
                <AccessGate>{children}</AccessGate>
              </ErrorBoundary>
            </main>
            <Footer />
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
