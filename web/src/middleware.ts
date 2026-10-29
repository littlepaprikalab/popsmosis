import { NextRequest, NextResponse } from 'next/server';

// The header that carries the visitor's address to the API (api/src/lib/public-limit.ts), which believes it only
// beside the proxy secret.
const CLIENT_IP_HEADER = 'x-popsmosis-client-ip';

const first = (value: string | null) => value?.split(',')[0]?.trim() || null;

// Attach the shared proxy secret to /api/* requests forwarded to the Fastify
// backend, so the API can refuse browsers that bypass the Next.js proxy. And
// say who the visitor is, as the edge in front of this app saw them: the API
// keys its rate limits on it. Read here rather than at the API, because when
// the API is reached by its public URL the second trip through the edge puts
// this server's own address in the edge's headers.
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (pathname.startsWith('/api/')) {
    const secret = process.env.PROXY_SECRET;
    if (!secret) return NextResponse.next();
    const headers = new Headers(request.headers);
    headers.set('x-proxy-token', secret);
    // Cloudflare (in front of Render) sets these two itself; X-Forwarded-For a client can start, so it comes last.
    const client = first(request.headers.get('cf-connecting-ip')) || first(request.headers.get('true-client-ip'))
      || first(request.headers.get('x-forwarded-for'));
    if (client) headers.set(CLIENT_IP_HEADER, client);
    else headers.delete(CLIENT_IP_HEADER); // never one a client sent
    return NextResponse.next({ request: { headers } });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ['/api/:path*'],
};
