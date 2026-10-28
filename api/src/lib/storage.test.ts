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

// A legacy Supabase key: a JWT whose role claim says which one it is.
const legacyKey = (role: string) => `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify({ iss: 'supabase', role })).toString('base64url')}.sig`;

describe('storageConfig', () => {
  it('needs both the URL and the key', () => {
    expect(storageConfig({})).toBeNull();
    expect(storageConfig({ SUPABASE_URL: 'https://x.supabase.co' })).toBeNull();
    expect(storageConfig({ SUPABASE_SERVICE_ROLE_KEY: 'sb_secret_k' })).toBeNull();
  });

  it('sends a secret key as apikey only, since it is not a JWT', () => {
    expect(storageConfig({ SUPABASE_URL: 'https://x.supabase.co/', SUPABASE_SERVICE_ROLE_KEY: ' sb_secret_k ' }))
      .toEqual({ url: 'https://x.supabase.co', headers: { apikey: 'sb_secret_k' }, legacy: false });
  });

  it('takes the project URL alone, since the upload and public URLs are built on it', () => {
    const config = (url: string) => storageConfig({ SUPABASE_URL: url, SUPABASE_SERVICE_ROLE_KEY: 'sb_secret_k' });
    expect(config(' https://X.supabase.co// ')).toMatchObject({ url: 'https://x.supabase.co' });
    expect(config('http://127.0.0.1:54321')).toMatchObject({ url: 'http://127.0.0.1:54321' }); // the Supabase CLI
    for (const bad of ['https://x.supabase.co/storage/v1', 'https://x.supabase.co/rest/v1/', 'x.supabase.co', 'https://x.supabase.co?a=1', 'ftp://x.supabase.co']) {
      expect(config(bad), bad).toEqual({ problem: expect.stringMatching(/SUPABASE_URL must be the project URL alone/) });
    }
  });

  it('sends the legacy service_role JWT as apikey and as a bearer token', () => {
    const key = legacyKey('service_role');
    expect(storageConfig({ SUPABASE_URL: 'https://x.supabase.co', SUPABASE_SERVICE_ROLE_KEY: key }))
      .toEqual({ url: 'https://x.supabase.co', headers: { apikey: key, authorization: `Bearer ${key}` }, legacy: true });
  });

  it('names a key that cannot upload, and the key it needs', () => {
    const problem = (key: string) => {
      const config = storageConfig({ SUPABASE_URL: 'https://x.supabase.co', SUPABASE_SERVICE_ROLE_KEY: key });
      return config && 'problem' in config ? config.problem : null;
    };
    expect(problem('sb_publishable_k')).toMatch(/publishable key.*sb_secret_/);
    expect(problem(legacyKey('anon'))).toMatch(/legacy anon key.*sb_secret_/);
    expect(problem('service-key')).toMatch(/not a Supabase secret key.*sb_secret_/);
    expect(problem('a.b.c')).toMatch(/not a Supabase secret key/);
    for (const key of ['sb_publishable_k', legacyKey('anon'), 'service-key']) expect(problem(key)).not.toContain(key);
  });
});

describe('signUpload', () => {
  const config = { url: 'https://x.supabase.co', headers: { apikey: 'sb_secret_k' }, legacy: false };

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
    expect(seen.init?.headers).toEqual({ apikey: 'sb_secret_k', 'content-type': 'application/json', 'x-upsert': 'true' });
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

  const failure = async (answer: () => Promise<Response>) => {
    try {
      await signUpload(config, 'droplets', 'a1', 'x.mp4', answer as unknown as typeof fetch);
    } catch (err) {
      expect(err).toBeInstanceOf(StorageError);
      return err as StorageError;
    }
    throw new Error('signUpload did not fail');
  };

  it("says what storage refused with, in each of its body shapes, and that waiting will not fix it", async () => {
    const refused = [
      [{ statusCode: '404', error: 'Bucket not found', message: 'Bucket not found' }, 'Bucket not found'],
      [{ statusCode: '403', error: 'Unauthorized', message: 'new row violates row-level security policy' }, 'new row violates row-level security policy'],
      [{ code: 'InvalidJWT', message: 'Invalid Compact JWS' }, 'Invalid Compact JWS'],
      [{ httpStatusCode: 400, code: 'InvalidKey' }, 'InvalidKey'],
    ] as const;
    for (const [body, said] of refused) {
      const err = await failure(async () => new Response(JSON.stringify(body), { status: 400 }));
      expect(err.message).toBe(`storage answered 400: ${said}`);
      expect(err.retryable).toBe(false);
    }
    expect((await failure(async () => new Response('<html>', { status: 401 }))).message).toBe('storage answered 401');
  });

  it('calls a failure a moment may fix retryable', async () => {
    const answers = [
      async () => new Response('{}', { status: 500 }),
      async () => new Response('{"message":"Too many requests"}', { status: 429 }),
      async () => new Response('{"statusCode":"544","error":"DatabaseTimeout"}', { status: 400 }),
      async () => new Response('{"nope":true}', { status: 200 }),
      async () => { throw new TypeError('fetch failed'); },
    ];
    for (const answer of answers) expect((await failure(answer)).retryable).toBe(true);
  });
});
