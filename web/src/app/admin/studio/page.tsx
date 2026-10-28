'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ArtSettings, Painter, StudioSettings } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { timeAgo } from '@/lib/time';

// What each painter is and costs (studio/gemini.py, studio/muse.py, studio/steps/art.py). Up to three pictures a scene.
const PAINTERS: Record<Painter, { label: string; note: string }> = {
  gemini: { label: 'Gemini 3.1 Flash Image', note: 'About 7¢ a picture, on the Gemini key (GEMINI_API_KEY). SAM 3.1 then finds the parts to label, on the Meta key.' },
  muse: { label: 'Muse Image', note: '1¢ a picture, on the Meta key (MODEL_API_KEY). SAM 3.1 then finds the parts to label, on the same key.' },
  svg: { label: 'Claude draws it (SVG)', note: 'The brain draws each scene and says where each part is, on the Anthropic key: no Gemini or Meta key needed. Plainer pictures.' },
};

const BRAINS: { provider: keyof ArtSettings; title: string; who: string }[] = [
  { provider: 'claude', title: 'Claude topics', who: 'Sent to Claude on the LEARN page, or on Default while the teacher runs on Claude. Claude Haiku 5.5 checks each picture.' },
  { provider: 'meta', title: 'Muse topics', who: 'Sent to Muse Wonder or Muse Spark, or on Default while the teacher runs on Muse. Muse Spark 1.3 checks each picture.' },
];

/**
 * Admin page: the studio's settings (api/src/lib/studio-settings.ts). Who paints each droplet's pictures, by the
 * brain that wrote its lesson. The teacher reads them when it starts a droplet's videos, so a change applies from
 * the next run, with no redeploy.
 */
export default function AdminStudioPage() {
  const { user: me, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [settings, setSettings] = useState<StudioSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<keyof ArtSettings | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSettings(await api.getStudioSettings());
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load the studio settings', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (me?.role === 'ADMIN') load(); else setLoading(false);
  }, [authLoading, me, load]);

  const pick = async (provider: keyof ArtSettings, painter: Painter) => {
    if (!settings || settings.art[provider] === painter || saving) return;
    setSaving(provider);
    try {
      setSettings(await api.setStudioArt({ [provider]: painter }));
      showToast(`${provider === 'claude' ? 'Claude' : 'Muse'} topics: ${PAINTERS[painter].label} paints from the next run.`, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to save', 'error');
    } finally {
      setSaving(null);
    }
  };

  if (authLoading || (loading && me?.role === 'ADMIN')) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (me?.role !== 'ADMIN') {
    return (
      <PageContainer className="py-20 text-center">
        <p style={{ color: 'var(--accent-red)' }}>Admin access required.</p>
      </PageContainer>
    );
  }
  if (!settings) {
    return (
      <PageContainer className="py-20 text-center">
        <button onClick={load} className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>Try again</button>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="py-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">STUDIO</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Who paints each droplet&rsquo;s pictures, by the brain that writes its lesson. The teacher reads this when it
          starts a droplet&rsquo;s videos, so a change applies from the next run.
        </p>
        <p className="text-[10px] uppercase tracking-wider mt-2" style={{ color: 'var(--text-muted)' }} data-testid="studio-updated">
          {settings.updatedAt ? `Last changed ${timeAgo(settings.updatedAt)}` : 'The defaults: never changed'}
        </p>
      </div>

      <div className="space-y-4">
        {BRAINS.map(({ provider, title, who }) => (
          <section key={provider} className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            data-testid={`art-${provider}`}>
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>{title}</h3>
            <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>{who}</p>
            <div className="space-y-2" role="radiogroup" aria-label={`Who paints the pictures for ${title}`}>
              {settings.choices.art[provider].map((painter) => {
                const on = settings.art[provider] === painter;
                return (
                  <button key={painter} type="button" role="radio" aria-checked={on} disabled={saving !== null}
                    onClick={() => pick(provider, painter)}
                    className="w-full text-left rounded-md px-3 py-2 transition-colors disabled:cursor-wait"
                    style={{
                      background: on ? 'rgba(175, 82, 222, 0.10)' : 'var(--bg-tertiary)',
                      border: `1px solid ${on ? 'var(--accent-purple)' : 'var(--border)'}`,
                    }}>
                    <span className="flex items-center gap-2">
                      <span className="inline-block w-3 h-3 rounded-full shrink-0"
                        style={{ border: '2px solid var(--accent-purple)', background: on ? 'var(--accent-purple)' : 'transparent' }} />
                      <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{PAINTERS[painter].label}</span>
                      {settings.defaults.art[provider] === painter && (
                        <span className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>default</span>
                      )}
                    </span>
                    <span className="block text-[11px] mt-1 ml-5" style={{ color: 'var(--text-secondary)' }}>{PAINTERS[painter].note}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <p className="text-[11px] mt-6" style={{ color: 'var(--text-muted)' }}>
        The teacher&rsquo;s machine needs the key of the painter picked here. Without it, the teacher stops before making
        the videos and reports which key is missing. A droplet whose pictures another painter made has them made again
        the next time its videos run.
      </p>
    </PageContainer>
  );
}
