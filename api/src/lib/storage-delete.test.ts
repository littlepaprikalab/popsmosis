import { describe, it, expect } from 'vitest';
import { clearDropletFiles } from './storage-delete.js';
import type { Bucket, StorageConfig } from './storage.js';

const ACCOUNT = '0123456789abcdef0123456789abcdef';
const CONFIG: StorageConfig = {
  endpoint: `https://${ACCOUNT}.r2.cloudflarestorage.com`,
  accessKeyId: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  secretAccessKey: 'a'.repeat(64),
  publicUrl: 'https://media.popsmosis.com',
};

/** R2's S3 API behind a fetch: the two buckets' keys, and every request, signed or not. */
function fakeR2(files: Partial<Record<Bucket, string[]>> = {}) {
  const store: Record<Bucket, Set<string>> = { droplets: new Set(files.droplets), studio: new Set(files.studio) };
  const asked: string[] = [];
  let refuse: { status: number; code: string } | null = null;
  const fetchImpl = (async (input: string | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    const method = init.method ?? 'GET';
    if (url.origin !== CONFIG.endpoint) throw new Error(`unexpected ${method} ${url}`);
    expect(url.searchParams.get('X-Amz-Signature')).toMatch(/^[0-9a-f]{64}$/);
    const [, bucket, ...rest] = url.pathname.split('/') as [string, Bucket, ...string[]];
    const key = rest.map(decodeURIComponent).join('/');
    asked.push(method === 'GET' ? `LIST ${bucket} ${url.searchParams.get('prefix')}` : `${method} ${bucket}/${key}`);
    if (refuse) return new Response(`<Error><Code>${refuse.code}</Code><Message>no</Message></Error>`, { status: refuse.status });
    if (method === 'GET' && url.searchParams.get('list-type') === '2') {
      const prefix = url.searchParams.get('prefix') ?? '';
      const all = [...store[bucket]].filter((k) => k.startsWith(prefix)).sort();
      const page = all.slice(0, Number(url.searchParams.get('max-keys') || 1000));
      const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      return new Response(`<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><Name>${bucket}</Name><Prefix>${escape(prefix)}</Prefix>`
        + `<KeyCount>${page.length}</KeyCount><IsTruncated>${page.length < all.length}</IsTruncated>`
        + page.map((k) => `<Contents><Key>${escape(k)}</Key><Size>3</Size></Contents>`).join('')
        + '</ListBucketResult>');
    }
    if (method === 'DELETE') {
      store[bucket].delete(key);
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected ${method} ${url}`);
  }) as unknown as typeof fetch;
  return { store, asked, fetchImpl, refuse: (status: number, code: string) => { refuse = { status, code }; } };
}

describe('clearDropletFiles', () => {
  it('deletes every file in the droplet\'s folder of both buckets, and nothing of any other droplet', async () => {
    const r2 = fakeR2({
      droplets: ['ans1/a1/out/song_h.mp4', 'ans1/a1/out/song_h.jpg', 'ans1/a2/out/lesson_h.mp4', 'ans10/a1/out/song_h.mp4', 'other/a1/out/x.mp4'],
      studio: ['ans1/a1/art/scene-1.png', 'ans1/a1/masks/limbic-detail/amygdala.png', 'ans1/a1/timeline_song.json', 'ans10/a1/art/s.png'],
    });
    expect(await clearDropletFiles(CONFIG, 'ans1', r2.fetchImpl)).toBe(6);
    expect([...r2.store.droplets]).toEqual(['ans10/a1/out/song_h.mp4', 'other/a1/out/x.mp4']); // "ans1/", not "ans1"
    expect([...r2.store.studio]).toEqual(['ans10/a1/art/s.png']);
    expect(r2.asked.filter((a) => a.startsWith('LIST'))).toEqual([
      'LIST droplets ans1/', 'LIST droplets ans1/', 'LIST studio ans1/', 'LIST studio ans1/',
    ]);
  });

  it('lists again until the folder is empty, past a page of 1000', async () => {
    const keys = Array.from({ length: 2345 }, (_, i) => `ans1/a1/art/scene-${i}.png`);
    const r2 = fakeR2({ studio: keys });
    expect(await clearDropletFiles(CONFIG, 'ans1', r2.fetchImpl)).toBe(2345);
    expect(r2.store.studio.size).toBe(0);
    expect(r2.asked.filter((a) => a === 'LIST studio ans1/')).toHaveLength(4);
  });

  it('is done at once for a droplet with no files', async () => {
    const r2 = fakeR2();
    expect(await clearDropletFiles(CONFIG, 'ans1', r2.fetchImpl)).toBe(0);
    expect(r2.asked).toEqual(['LIST droplets ans1/', 'LIST studio ans1/']);
  });

  it('says what R2 refused with, and deletes nothing it was not asked to', async () => {
    const r2 = fakeR2({ droplets: ['ans1/a1/out/song_h.mp4'] });
    r2.refuse(403, 'AccessDenied');
    await expect(clearDropletFiles(CONFIG, 'ans1', r2.fetchImpl)).rejects.toThrow('R2 refused to list droplets/ans1/ (403 AccessDenied: no)');
    expect(r2.store.droplets.size).toBe(1);
  });

  it('says when R2 does not answer', async () => {
    const down = (async () => { throw new Error('fetch failed'); }) as unknown as typeof fetch;
    await expect(clearDropletFiles(CONFIG, 'ans1', down)).rejects.toThrow('R2 did not answer: fetch failed');
  });

  it('never deletes a file it could not have signed an upload for, and never a folder that is not one answer\'s', async () => {
    const r2 = fakeR2({ droplets: ['ans1/a1/out/song_h.mp4', 'ans1/x&y/../../other.mp4'] });
    await expect(clearDropletFiles(CONFIG, 'ans1', r2.fetchImpl)).rejects.toThrow(/never signed, such as ans1\/x&y\/\.\.\/\.\.\/other\.mp4/);
    expect(r2.asked.some((a) => a.startsWith('DELETE'))).toBe(false);
    for (const bad of ['', '.', '..', 'a/b', 'ans1/']) {
      await expect(clearDropletFiles(CONFIG, bad, r2.fetchImpl), bad).rejects.toThrow('is not an answer id');
    }
  });
});
