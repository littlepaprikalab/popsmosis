/**
 * The downloadable skill file is a template: GET /api/agents/:id/skill swaps
 * SKILL_BASE_URL_PLACEHOLDER for API_BASE_URL. A real host pasted into the
 * template survives that swap untouched, so every operator downloads someone
 * else's URL — which is exactly what happened in ae26d97, and the integration
 * test missed it because it only asserted on the one occurrence still holding
 * a placeholder.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const TEMPLATE = resolve(__dirname, '../../../docs/zoey-agent-skill.md');
const PLACEHOLDER = 'https://api.zoey.example';

describe('skill file template', () => {
  const body = readFileSync(TEMPLATE, 'utf8');

  it('hardcodes no host — every absolute URL is the placeholder', () => {
    const hosts = [...body.matchAll(/https?:\/\/[A-Za-z0-9._-]+/g)].map((m) => m[0]);
    expect(hosts.length).toBeGreaterThan(0);
    expect([...new Set(hosts)]).toEqual([PLACEHOLDER]);
  });

  it('substitution leaves nothing behind', () => {
    const substituted = body.split(PLACEHOLDER).join('https://api.example.test');
    expect(substituted).not.toContain('zoey.example');
    expect(substituted).toContain('https://api.example.test/api/agents/me');
  });

  it('describes no polling window — the API fires the routine on write', () => {
    expect(body).not.toContain('ZOEY_POLL_MINUTES');
    expect(body).not.toContain('ZOEY_QUIET');
  });
});
