import type { NextConfig } from 'next';
import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';

// Load the repo-root .env so a single file configures api and web (sparks pattern).
const rootEnvPath = resolve(process.cwd(), '..', '.env');
if (existsSync(rootEnvPath)) {
  for (const line of readFileSync(rootEnvPath, 'utf-8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (!process.env[key]) process.env[key] = value;
  }
}

const sanitize = (v: string) => v.replace(/[\x00-\x1F\x7F]/g, '').trim();
const apiUrl = sanitize(process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001');

const nextConfig: NextConfig = {
  outputFileTracingRoot: resolve(process.cwd()),
  env: {
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || '',
    NEXT_PUBLIC_GOOGLE_REDIRECT_URI: process.env.NEXT_PUBLIC_GOOGLE_REDIRECT_URI || '',
    NEXT_PUBLIC_API_URL: apiUrl,
    NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL || '',
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''}`,
              "style-src 'self' 'unsafe-inline'",
              'img-src \'self\' https: data:',
              // The droplet videos live in Supabase Storage's public bucket (docs/PLAN.md, section 10).
              "media-src 'self' https:",
              `connect-src 'self' ${apiUrl} https://accounts.google.com`,
              "font-src 'self'",
              "object-src 'none'",
              "base-uri 'self'",
              'frame-src https://accounts.google.com',
            ].join('; '),
          },
        ],
      },
    ];
  },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
