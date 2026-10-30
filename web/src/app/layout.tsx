import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/lib/auth';
import { Nav } from '@/components/Nav';
import { Footer } from '@/components/Footer';
import { TermsGate } from '@/components/TermsGate';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { ToastProvider } from '@/components/Toast';
import { PlayCountScope } from '@/components/PlayCountScope';
import { AnnouncementBanner } from '@/components/AnnouncementBanner';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'POPsmosis — droplets for learning',
    template: '%s — POPsmosis',
  },
  description: 'Droplets for learning. Ask for any topic and get a short lesson you can ask follow-ups under.',
  applicationName: 'POPsmosis',
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
            <AnnouncementBanner />
            <main className="flex-1">
              <ErrorBoundary>
                {children}
              </ErrorBoundary>
            </main>
            <Footer />
            <TermsGate />
            <PlayCountScope />
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
