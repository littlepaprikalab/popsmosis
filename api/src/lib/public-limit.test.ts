import { describe, it, expect } from 'vitest';
import type { FastifyRequest } from 'fastify';
import { KeyedWindowCounter, addressGroup, publicClientKey, WindowCounter } from './public-limit.js';

const req = (headers: Record<string, string>, ip = '10.0.0.5') => ({ headers, ip }) as unknown as FastifyRequest;

describe('publicClientKey', () => {
  it('believes the forwarded address only on a request that came through the web app', () => {
    const forwarded = { 'x-forwarded-for': '203.0.113.7, 10.1.1.1' };
    expect(publicClientKey(req(forwarded), 'secret')).toBe('public:10.0.0.5');
    expect(publicClientKey(req({ ...forwarded, 'x-proxy-token': 'wrong' }), 'secret')).toBe('public:10.0.0.5');
    expect(publicClientKey(req({ ...forwarded, 'x-proxy-token': 'secret' }), 'secret')).toBe('public:203.0.113.7');
    expect(publicClientKey(req(forwarded), undefined)).toBe('public:10.0.0.5'); // no proxy configured
  });

  it("takes the web app's own header first, which it sets from the edge's on the visitor's first hop", () => {
    const headers = { 'x-proxy-token': 's', 'x-popsmosis-client-ip': '4.4.4.4', 'cf-connecting-ip': '10.9.9.9' };
    expect(publicClientKey(req(headers), 's')).toBe('public:4.4.4.4');
    // Not without the proxy secret: a client could send it straight to the API.
    expect(publicClientKey(req({ 'x-popsmosis-client-ip': '4.4.4.4' }), 's')).toBe('public:10.0.0.5');
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

describe('addressGroup', () => {
  it('keeps an IPv4 address whole, also when it comes as IPv6', () => {
    expect(addressGroup('203.0.113.7')).toBe('203.0.113.7');
    expect(addressGroup('::ffff:203.0.113.7')).toBe('203.0.113.7');
  });

  it('counts an IPv6 address by its /64, however it is written', () => {
    const net = '2001:db8:85a3:0::/64';
    expect(addressGroup('2001:db8:85a3::1')).toBe(net);
    expect(addressGroup('2001:0DB8:85a3:0000:dead:beef:0:7334')).toBe(net);
    expect(addressGroup('2001:db8:85a3:0:ffff:ffff:ffff:ffff')).toBe(net);
    expect(addressGroup('2001:db8:85a3:1::1')).toBe('2001:db8:85a3:1::/64');
    expect(addressGroup('::1')).toBe('0:0:0:0::/64');
    expect(addressGroup('fe80::1%eth0')).toBe('fe80:0:0:0::/64');
  });

  it('leaves anything else as it came', () => {
    expect(addressGroup('unknown')).toBe('unknown');
  });
});

describe('KeyedWindowCounter', () => {
  it('lets max through for each key in a window, then none for that key until the next', () => {
    const c = new KeyedWindowCounter(2, 1000);
    expect([c.take(['a'], 0), c.take(['a'], 10), c.take(['a'], 20)]).toEqual([true, true, false]);
    expect(c.take(['b'], 30)).toBe(true); // another key is untouched
    expect(c.take(['a'], 999)).toBe(false);
    expect(c.take(['a'], 1000)).toBe(true);
  });

  it('tells keys apart by every part, not by how they join', () => {
    const c = new KeyedWindowCounter(1, 1000);
    expect(c.take(['ab', 'c'], 0)).toBe(true);
    expect(c.take(['a', 'bc'], 0)).toBe(true);
    expect(c.take(['ab', 'c'], 0)).toBe(false);
  });

  it('refuses a new key once a window holds maxKeys, and takes them again in the next', () => {
    const c = new KeyedWindowCounter(5, 1000, 2);
    expect([c.take(['a'], 0), c.take(['b'], 0), c.take(['c'], 0)]).toEqual([true, true, false]);
    expect(c.take(['a'], 0)).toBe(true); // a key it already holds still counts
    expect(c.take(['c'], 1000)).toBe(true);
  });

  it('holds no part as it was given', () => {
    const c = new KeyedWindowCounter(5, 1000);
    c.take(['203.0.113.7', 'droplet'], 0);
    const held = JSON.stringify([...(c as unknown as { counts: Map<string, number> }).counts.keys()]);
    expect(held).not.toContain('203.0.113');
    expect(held).not.toContain('droplet');
  });
});
