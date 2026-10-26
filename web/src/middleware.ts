import { NextRequest, NextResponse } from 'next/server';

// Attach the shared proxy secret to /api/* requests forwarded to the Fastify
// backend, so the API can refuse browsers that bypass the Next.js proxy.
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (pathname.startsWith('/api/')) {
    const secret = process.env.PROXY_SECRET;
    if (!secret) return NextResponse.next();
    const headers = new Headers(request.headers);
    headers.set('x-proxy-token', secret);
    return NextResponse.next({ request: { headers } });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ['/api/:path*'],
};
