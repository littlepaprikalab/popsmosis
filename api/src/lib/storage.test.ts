import { describe, it, expect } from 'vitest';
import {
  contentType, dropletFileUrlError, fileError, presign, PUBLIC_CACHE_CONTROL, signUpload, storageConfig, storageReachable,
  UPLOAD_URL_SECONDS, type StorageConfig,
} from './storage.js';

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

const ACCOUNT = '0123456789abcdef0123456789abcdef';
const ENV = {
  R2_ACCOUNT_ID: ACCOUNT,
  R2_ACCESS_KEY_ID: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  R2_SECRET_ACCESS_KEY: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY+3bf8a2',
  R2_PUBLIC_URL: 'https://media.popsmosis.com',
};
const CONFIG: StorageConfig = {
  endpoint: `https://${ACCOUNT}.r2.cloudflarestorage.com`,
  accessKeyId: ENV.R2_ACCESS_KEY_ID,
  secretAccessKey: ENV.R2_SECRET_ACCESS_KEY,
  publicUrl: 'https://media.popsmosis.com',
};

describe('storageConfig', () => {
  const problem = (env: NodeJS.ProcessEnv) => {
    const config = storageConfig(env);
    return config && 'problem' in config ? config.problem : null;
  };

  it('is off with none of the four settings, and builds the S3 endpoint from the account ID', () => {
    expect(storageConfig({})).toBeNull();
    expect(storageConfig({ ...ENV, R2_ACCOUNT_ID: ` ${ACCOUNT.toUpperCase()} `, R2_PUBLIC_URL: 'https://media.popsmosis.com//' })).toEqual(CONFIG);
  });

  it('names the settings that are missing when only some are set', () => {
    expect(problem({ R2_ACCOUNT_ID: ACCOUNT })).toMatch(/^R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL are not set/);
    expect(problem({ ...ENV, R2_PUBLIC_URL: ' ' })).toMatch(/^R2_PUBLIC_URL is not set/);
  });

  it('wants the account ID itself, not its endpoint', () => {
    expect(problem({ ...ENV, R2_ACCOUNT_ID: `https://${ACCOUNT}.r2.cloudflarestorage.com` })).toMatch(/32-character account ID/);
    expect(problem({ ...ENV, R2_ACCOUNT_ID: 'my-account' })).toMatch(/32-character account ID/);
  });

  it('takes the public address alone, over https, and never the S3 endpoint', () => {
    const publicUrl = (url: string) => storageConfig({ ...ENV, R2_PUBLIC_URL: url });
    expect(publicUrl('https://Media.Popsmosis.com/')).toMatchObject({ publicUrl: 'https://media.popsmosis.com' });
    expect(publicUrl('http://localhost:9000')).toMatchObject({ publicUrl: 'http://localhost:9000' });
    for (const bad of ['https://media.popsmosis.com/droplets', 'media.popsmosis.com', 'https://media.popsmosis.com?a=1', 'ftp://media.popsmosis.com']) {
      expect(problem({ ...ENV, R2_PUBLIC_URL: bad }), bad).toMatch(/R2_PUBLIC_URL must/);
    }
    expect(problem({ ...ENV, R2_PUBLIC_URL: 'http://media.popsmosis.com' })).toMatch(/https/);
    expect(problem({ ...ENV, R2_PUBLIC_URL: `https://${ACCOUNT}.r2.cloudflarestorage.com` })).toMatch(/S3 API endpoint/);
  });

  it('never repeats the secret in a problem', () => {
    expect(problem({ ...ENV, R2_ACCOUNT_ID: 'x' })).not.toContain(ENV.R2_SECRET_ACCESS_KEY);
  });
});

describe('presign', () => {
  // The same requests signed by botocore, AWS's own signer, at the same time with the same key.
  const now = new Date('2026-10-29T12:34:56.789Z');

  it('signs a PUT with its headers exactly as AWS Signature Version 4 does', () => {
    const url = presign(CONFIG, 'PUT', '/droplets/cmabc123/a1/out/song_h.mp4',
      { 'Content-Type': 'video/mp4', 'Cache-Control': 'public, max-age=31536000, immutable' }, {}, 7200, now);
    expect(url).toBe(`https://${ACCOUNT}.r2.cloudflarestorage.com/droplets/cmabc123/a1/out/song_h.mp4`
      + '?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c%2F20261029%2Fauto%2Fs3%2Faws4_request'
      + '&X-Amz-Date=20261029T123456Z&X-Amz-Expires=7200&X-Amz-SignedHeaders=cache-control%3Bcontent-type%3Bhost'
      + '&X-Amz-Signature=24fff1edbbeee68e91a253a7597cec4eb1a92388080a9fb1c4e2aaead7c03507');
  });

  it('signs a request with its own query, as the check at boot sends', () => {
    const url = new URL(presign(CONFIG, 'GET', '/studio', {}, { 'list-type': '2', 'max-keys': '1' }, 60, now));
    expect(url.pathname).toBe('/studio');
    expect(url.searchParams.get('list-type')).toBe('2');
    expect(url.searchParams.get('X-Amz-SignedHeaders')).toBe('host');
    expect(url.searchParams.get('X-Amz-Signature')).toBe('c2d9eccf66cc91f39484dafb176e3b34c5b09435c527c6ba9f6b1b49697f266c');
  });
});

describe('signUpload', () => {
  const now = new Date('2026-10-29T12:34:56Z');

  it('signs a public file with its type and a long cache, and says where anyone will read it', () => {
    const signed = signUpload(CONFIG, 'droplets', 'cmabc123', 'a1/out/song_h.mp4', now);
    expect(signed.headers).toEqual({ 'Content-Type': 'video/mp4', 'Cache-Control': PUBLIC_CACHE_CONTROL });
    expect(signed.uploadUrl).toBe(presign(CONFIG, 'PUT', '/droplets/cmabc123/a1/out/song_h.mp4', signed.headers, {}, UPLOAD_URL_SECONDS, now));
    expect(signed.uploadUrl.startsWith(`https://${ACCOUNT}.r2.cloudflarestorage.com/droplets/cmabc123/a1/out/song_h.mp4?`)).toBe(true);
    expect(new URL(signed.uploadUrl).searchParams.get('X-Amz-Expires')).toBe('7200');
    expect(signed.path).toBe('droplets/cmabc123/a1/out/song_h.mp4');
    expect(signed.publicUrl).toBe('https://media.popsmosis.com/cmabc123/a1/out/song_h.mp4');
    // The URL the studio will report passes the check the video PATCH makes.
    expect(dropletFileUrlError(signed.publicUrl!, 'songVideoUrl', 'cmabc123', ENV)).toBeNull();
  });

  it('gives no public URL and no cache header for the private bucket', () => {
    const signed = signUpload(CONFIG, 'studio', 'cmabc123', 'a1/audio/song.mp3', now);
    expect(signed.headers).toEqual({ 'Content-Type': 'audio/mpeg' });
    expect(signed.publicUrl).toBeNull();
    expect(signed.uploadUrl).toContain('/studio/cmabc123/a1/audio/song.mp3?');
  });

  it('types each file the studio uploads from its extension', () => {
    expect(['out/x.mp4', 'out/x.jpg', 'out/x.JPEG', 'art/x.png', 'out/x.webp', 'audio/x.mp3', 'timeline_song.json', 'notes']
      .map(contentType)).toEqual(['video/mp4', 'image/jpeg', 'image/jpeg', 'image/png', 'image/webp', 'audio/mpeg', 'application/json', 'application/octet-stream']);
  });
});

describe('dropletFileUrlError', () => {
  const own = 'https://media.popsmosis.com/ans1/a1/out/song_h.mp4';

  it('takes only this droplet\'s own public files once storage is set up', () => {
    expect(dropletFileUrlError(own, 'songVideoUrl', 'ans1', ENV)).toBeNull();
    for (const bad of [
      'https://evil.example/ans1/a1/out/song_h.mp4',
      'https://media.popsmosis.com/other/a1/out/song_h.mp4',
      'https://media.popsmosis.com/ans1/../other/song_h.mp4',
      'https://media.popsmosis.com/ans1/a1/out/page.html',
      `${own}?download=page.html`,
      `https://${ACCOUNT}.r2.cloudflarestorage.com/droplets/ans1/a1/out/song_h.mp4`,
    ]) {
      expect(dropletFileUrlError(bad, 'songVideoUrl', 'ans1', ENV), bad).toMatch(/under https:\/\/media\.popsmosis\.com\/ans1\//);
    }
  });

  it('takes any URL on a local API without storage, and none on a production one', () => {
    expect(dropletFileUrlError('https://x.example/s.mp4', 'songVideoUrl', 'ans1', {})).toBeNull();
    expect(dropletFileUrlError(own, 'songVideoUrl', 'ans1', { NODE_ENV: 'production' })).toMatch(/not set up/);
    expect(dropletFileUrlError(own, 'songVideoUrl', 'ans1', { NODE_ENV: 'production', R2_ACCOUNT_ID: ACCOUNT })).toMatch(/not set up/);
  });
});

describe('storageReachable', () => {
  const answering = (status: number, body = '') => {
    const asked: string[] = [];
    const fetchImpl = (async (url: string) => {
      asked.push(url);
      return new Response(body, { status });
    }) as unknown as typeof fetch;
    return { asked, fetchImpl };
  };

  it('lists one file of each bucket with the key', async () => {
    const { asked, fetchImpl } = answering(200, '<ListBucketResult/>');
    expect(await storageReachable(CONFIG, fetchImpl)).toBeNull();
    expect(asked.map((u) => new URL(u).pathname)).toEqual(['/droplets', '/studio']);
    expect(asked.every((u) => u.includes('list-type=2') && u.includes('max-keys=1') && u.includes('X-Amz-Signature='))).toBe(true);
  });

  it('says what R2 refused with, and what to check', async () => {
    const key = await storageReachable(CONFIG, answering(403,
      '<?xml version="1.0"?><Error><Code>SignatureDoesNotMatch</Code><Message>The request signature we calculated does not match</Message></Error>').fetchImpl);
    expect(key).toMatch(/^R2 refused the droplets bucket \(403 SignatureDoesNotMatch: The request signature we calculated does not match\); check R2_ACCESS_KEY_ID/);
    const bucket = await storageReachable(CONFIG, answering(404, '<Error><Code>NoSuchBucket</Code><Message>The specified bucket does not exist.</Message></Error>').fetchImpl);
    expect(bucket).toMatch(/NoSuchBucket.*named exactly "droplets"/);
    // A failure that is not the key's or the bucket's does not send anyone to check them.
    const down = await storageReachable(CONFIG, answering(503).fetchImpl);
    expect(down).toBe('R2 did not take the request for the droplets bucket (503); it may be down for a moment, so the settings may be fine');
  });

  it('says when R2 does not answer', async () => {
    const down = (async () => { throw new TypeError('fetch failed'); }) as unknown as typeof fetch;
    expect(await storageReachable(CONFIG, down)).toBe('R2 did not answer for the droplets bucket: fetch failed');
  });
});
