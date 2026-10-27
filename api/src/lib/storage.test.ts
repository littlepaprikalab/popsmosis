import { describe, it, expect } from 'vitest';
import { fileError, storageConfig, signUpload, StorageError } from './storage.js';

describe('fileError', () => {
  it('takes relative paths inside the answer folder', () => {
    expect(fileError('out/song_h.mp4')).toBeNull();
    expect(fileError('masks/limbic-detail/amygdala.png')).toBeNull();
    expect(fileError('bundle.json')).toBeNull();
  });
  it('refuses anything that could leave it, or needs escaping', () => {
    for (const bad of ['', '/etc/passwd', '../other/x.mp4', 'out/../../x', 'out//x', 'out/', '.hidden', "Broca's area.png", 'a b.png', 'x?.png']) {
      expect(fileError(bad), bad).not.toBeNull();
    }
    expect(fileError(42)).toBe('file is required');
    expect(fileError('a'.repeat(301))).toMatch(/300/);
  });
});

describe('storageConfig', () => {
  it('needs both the URL and the key', () => {
    expect(storageConfig({})).toBeNull();
    expect(storageConfig({ SUPABASE_URL: 'https://x.supabase.co' })).toBeNull();
    expect(storageConfig({ SUPABASE_URL: 'https://x.supabase.co/', SUPABASE_SERVICE_ROLE_KEY: 'k' }))
      .toEqual({ url: 'https://x.supabase.co', key: 'k' });
  });
});

describe('signUpload', () => {
  const config = { url: 'https://x.supabase.co', key: 'service-key' };

  it('asks Supabase for an upserting upload URL and says where the file will be read', async () => {
    const seen: { url?: string; init?: RequestInit } = {};
    const fakeFetch = (async (url: string, init: RequestInit) => {
      seen.url = url;
      seen.init = init;
      return new Response(JSON.stringify({ url: '/object/upload/sign/droplets/a1/out/song_h.mp4?token=t0k' }), { status: 200 });
    }) as unknown as typeof fetch;
    const signed = await signUpload(config, 'droplets', 'a1', 'out/song_h.mp4', fakeFetch);
    expect(seen.url).toBe('https://x.supabase.co/storage/v1/object/upload/sign/droplets/a1/out/song_h.mp4');
    expect(seen.init?.method).toBe('POST');
    expect(seen.init?.headers).toMatchObject({ authorization: 'Bearer service-key', apikey: 'service-key', 'x-upsert': 'true' });
    expect(signed).toEqual({
      uploadUrl: 'https://x.supabase.co/storage/v1/object/upload/sign/droplets/a1/out/song_h.mp4?token=t0k',
      path: 'droplets/a1/out/song_h.mp4',
      publicUrl: 'https://x.supabase.co/storage/v1/object/public/droplets/a1/out/song_h.mp4',
    });
  });

  it('never gives a public URL for the private bucket', async () => {
    const fakeFetch = (async () => new Response(JSON.stringify({ url: '/object/upload/sign/studio/a1/art/hook.png?token=t' }))) as unknown as typeof fetch;
    expect((await signUpload(config, 'studio', 'a1', 'art/hook.png', fakeFetch)).publicUrl).toBeNull();
  });

  it('turns a refusal, a bad body or no answer into a StorageError', async () => {
    const answers = [
      async () => new Response('{"error":"Bucket not found"}', { status: 404 }),
      async () => new Response('{"nope":true}', { status: 200 }),
      async () => { throw new TypeError('fetch failed'); },
    ];
    for (const answer of answers) {
      await expect(signUpload(config, 'droplets', 'a1', 'x.mp4', answer as unknown as typeof fetch)).rejects.toBeInstanceOf(StorageError);
    }
  });
});
