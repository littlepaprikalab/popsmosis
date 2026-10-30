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

// Inkmory's site (docs/PLAN.md, section 11), for the link on each lesson to what the teacher
// wrote about it there. Unset: no link. An origin and nothing else, so a typo fails the build
// rather than sending every learner somewhere else.
const inkmoryUrl = (() => {
  const raw = sanitize(process.env.NEXT_PUBLIC_INKMORY_URL || '').replace(/\/+$/, '');
  if (!raw) return '';
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`NEXT_PUBLIC_INKMORY_URL is not a URL: "${raw}"`);
  }
  const originOnly = url.pathname === '/' && !/[?#]/.test(raw) && !url.username && !url.password;
  if ((url.protocol !== 'https:' && url.protocol !== 'http:') || !originOnly) {
    throw new Error(`NEXT_PUBLIC_INKMORY_URL must be an http(s) origin, like https://inkmory.com: "${raw}"`);
  }
  return url.origin;
})();

// Where the droplet videos and posters are served from, the one place besides this site that pages may load
// pictures and video from (the Content-Security-Policy below): the R2 bucket's public domain, the API's
// R2_PUBLIC_URL (render.yaml copies it), and, only while a droplet still plays from the old Supabase bucket (the
// API's boot log says so), that project's URL. Built in, like the others. Unset: pages load media from this site
// alone, which is how the seeded sample droplets play.
const mediaOrigin = (name: string, example: string): string => {
  const raw = sanitize(process.env[name] || '').replace(/\/+$/, '');
  if (!raw) return '';
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} is not a URL: "${raw}"`);
  }
  const originOnly = url.pathname === '/' && !/[?#]/.test(raw) && !url.username && !url.password;
  if ((url.protocol !== 'https:' && url.protocol !== 'http:') || !originOnly) {
    throw new Error(`${name} must be an origin alone, like ${example}: "${raw}"`);
  }
  return url.origin;
};
const mediaUrl = mediaOrigin('NEXT_PUBLIC_MEDIA_URL', 'https://media.popsmosis.com');
const legacyMediaUrl = mediaOrigin('NEXT_PUBLIC_SUPABASE_URL', 'https://<project-ref>.supabase.co');
if (!mediaUrl && process.env.NODE_ENV === 'production' && process.env.RENDER) {
  console.warn('NEXT_PUBLIC_MEDIA_URL is not set: the Content-Security-Policy blocks the droplet videos and posters');
}
const mediaSources = [mediaUrl, legacyMediaUrl].filter(Boolean).map((origin) => ` ${origin}`).join('');

const nextConfig: NextConfig = {
  outputFileTracingRoot: resolve(process.cwd()),
  env: {
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || '',
    NEXT_PUBLIC_GOOGLE_REDIRECT_URI: process.env.NEXT_PUBLIC_GOOGLE_REDIRECT_URI || '',
    NEXT_PUBLIC_API_URL: apiUrl,
    NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL || '',
    NEXT_PUBLIC_INKMORY_URL: inkmoryUrl,
    NEXT_PUBLIC_MEDIA_URL: mediaUrl,
    NEXT_PUBLIC_SUPABASE_URL: legacyMediaUrl,
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
              // Pictures from this site, the droplets' posters where their videos are, and the Google profile pictures
              // of people signed in; never from any other host, so a lesson or reply cannot pull an image (or a
              // tracking pixel) from anywhere onto pages minors read (components/Markdown.tsx says the same).
              `img-src 'self' data: https://*.googleusercontent.com${mediaSources}`,
              // The droplet videos come from the R2 bucket's own domain, R2_PUBLIC_URL on the API (docs/PLAN.md, section 10).
              `media-src 'self'${mediaSources}`,
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
