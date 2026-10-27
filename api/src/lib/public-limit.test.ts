import { describe, it, expect } from 'vitest';
import type { FastifyRequest } from 'fastify';
import { publicClientKey, WindowCounter } from './public-limit.js';

const req = (headers: Record<string, string>, ip = '10.0.0.5') => ({ headers, ip }) as unknown as FastifyRequest;

describe('publicClientKey', () => {
  it('believes the forwarded address only on a request that came through the web app', () => {
    const forwarded = { 'x-forwarded-for': '203.0.113.7, 10.1.1.1' };
    expect(publicClientKey(req(forwarded), 'secret')).toBe('public:10.0.0.5');
    expect(publicClientKey(req({ ...forwarded, 'x-proxy-token': 'wrong' }), 'secret')).toBe('public:10.0.0.5');
    expect(publicClientKey(req({ ...forwarded, 'x-proxy-token': 'secret' }), 'secret')).toBe('public:203.0.113.7');
    expect(publicClientKey(req(forwarded), undefined)).toBe('public:10.0.0.5'); // no proxy configured
  });

  it("prefers the edge's own header over X-Forwarded-For, which a client can start", () => {
    const headers = { 'x-proxy-token': 's', 'x-forwarded-for': '1.1.1.1', 'true-client-ip': '2.2.2.2', 'cf-connecting-ip': '3.3.3.3' };
    expect(publicClientKey(req(headers), 's')).toBe('public:3.3.3.3');
    const { 'cf-connecting-ip': _, ...render } = headers;
    expect(publicClientKey(req(render), 's')).toBe('public:2.2.2.2');
  });
});

describe('WindowCounter', () => {
  it('lets max requests through a window, then none until the next', () => {
    const c = new WindowCounter(2, 1000);
    expect([c.take(0), c.take(10), c.take(20)]).toEqual([true, true, false]);
    expect(c.take(999)).toBe(false);
    expect(c.take(1000)).toBe(true);
  });
});
