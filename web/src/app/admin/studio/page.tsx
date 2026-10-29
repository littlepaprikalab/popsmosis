'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ArtSettings, BrainSettings, Effort, Painter, SongProvider, SongSettings, StudioSettings } from '@/lib/api';
import { BRAIN_MODELS, type Brain } from '@/lib/brains';
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

// A painter or brain an API newer than this page offers: shown by its name rather than breaking the page.
const painterOf = (painter: string) => PAINTERS[painter as Painter] ?? { label: painter, note: '' };
const brainOf = (brain: string) => ({ label: BRAIN_MODELS[brain as Brain] ?? brain, note: BRAIN_NOTES[brain as Brain] ?? '' });

const BRAINS: { provider: keyof ArtSettings; title: string; who: string }[] = [
  { provider: 'claude', title: 'Pictures for Claude topics', who: 'Sent to Claude on the LEARN page, or on Default while the default brain is Claude. Claude Sonnet 5.5 checks each picture.' },
  { provider: 'meta', title: 'Pictures for Muse topics', who: 'Sent to Muse Wonder or Muse Spark, or on Default while the default brain is a Muse. Muse Spark 1.3 checks each picture.' },
];

// What each brain costs and bills to (studio/brain.py, PRICES), per million tokens in / out.
const BRAIN_NOTES: Record<Brain, string> = {
  claude: '$4 / $20 per million tokens, on the Anthropic key (POPSMOSIS_ANTHROPIC_API_KEY). About 70¢ a topic at high effort.',
  'muse-wonder': '$4 / $20 per million tokens, on the Meta key (MODEL_API_KEY).',
  'muse-spark': '$1.25 / $4.25 per million tokens, on the Meta key (MODEL_API_KEY): about a quarter of Wonder’s price.',
};

// Who sings the song (studio/steps/audio.py). The narration and the exam question are ElevenLabs' either way.
const SINGERS: Record<SongProvider, { label: string; note: string }> = {
  elevenlabs: { label: 'ElevenLabs Eleven Music', note: 'Sings each section to its planned length, so the words line up with the video closely. In ElevenLabs credits, on the ElevenLabs key (ELEVENLABS_API_KEY) the narration uses anyway.' },
  gemini: { label: 'Gemini Lyria 3.5', note: '$0.08 a song, on the Gemini key (GEMINI_API_KEY). Sings the whole song from one prompt and keeps to the planned timing only roughly; the studio finds each line in it.' },
};
const singerOf = (provider: string) => SINGERS[provider as SongProvider] ?? { label: provider, note: '' };

const EFFORT_NOTE = 'How hard Claude thinks when it writes and checks a lesson; thinking is billed as output. Muse ignores it.';

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
  const [saving, setSaving] = useState<string | null>(null);

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

  const save = async (what: string, change: { brain?: Partial<BrainSettings>; song?: SongSettings; art?: Partial<ArtSettings> }, done: string) => {
    if (saving) return;
    setSaving(what);
    try {
      setSettings(await api.setStudioSettings(change));
      showToast(done, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to save', 'error');
    } finally {
      setSaving(null);
    }
  };
  const pick = (provider: keyof ArtSettings, painter: Painter) => {
    if (settings?.art[provider] === painter) return;
    save(provider, { art: { [provider]: painter } },
      `${provider === 'claude' ? 'Claude' : 'Muse'} topics: ${painterOf(painter).label} paints from the next run.`);
  };
  const pickBrain = (brain: Brain) => {
    if (settings?.brain.default === brain) return;
    save('brain', { brain: { default: brain } }, `${brainOf(brain).label} writes the next topics sent on Default.`);
  };
  const pickSinger = (provider: SongProvider) => {
    if (settings?.song.provider === provider) return;
    save('song', { song: { provider } }, `${singerOf(provider).label} sings from the next droplet's videos.`);
  };
  const pickEffort = (effort: Effort) => {
    if (settings?.brain.effort === effort) return;
    save('effort', { brain: { effort } }, `Claude thinks at ${effort} effort from the next run.`);
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
          The brain that writes each lesson, who sings its song and who paints its pictures. The teacher reads this as it starts each topic
          and each droplet&rsquo;s videos, so a change applies from the next run. Its machine holds only the API keys.
        </p>
        <p className="text-[10px] uppercase tracking-wider mt-2" style={{ color: 'var(--text-muted)' }} data-testid="studio-updated">
          {settings.updatedAt ? `Last changed ${timeAgo(settings.updatedAt)}` : 'The defaults: never changed'}
        </p>
      </div>

      <div className="space-y-4">
        <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
          data-testid="brain-default">
          <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>Default brain</h3>
          <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
            Writes and fact-checks every topic sent on Default, learners&rsquo; included. An admin can still send one topic
            to another brain on the LEARN page.
          </p>
          <div className="space-y-2" role="radiogroup" aria-label="Default brain">
            {settings.choices.brain.default.map((brain) => (
              <Choice key={brain} on={settings.brain.default === brain} disabled={saving !== null}
                label={brainOf(brain).label} note={brainOf(brain).note} isDefault={settings.defaults.brain.default === brain}
                onClick={() => pickBrain(brain)} />
            ))}
          </div>
          <div className="mt-4" data-testid="brain-effort">
            <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Claude's effort">
              <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Claude&rsquo;s effort:</span>
              {settings.choices.brain.effort.map((effort) => {
                const on = settings.brain.effort === effort;
                return (
                  <button key={effort} type="button" role="radio" aria-checked={on} disabled={saving !== null}
                    onClick={() => pickEffort(effort)}
                    className={`px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-colors rounded-full border ${
                      on ? 'text-[var(--btn-filled-text)] border-transparent' : 'text-[var(--accent-purple)] border-[var(--accent-purple)] hover:bg-[var(--accent-purple)]/10'
                    }`}
                    style={on ? { background: 'var(--accent-purple)' } : {}}>
                    {effort}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>{EFFORT_NOTE} Default: {settings.defaults.brain.effort}.</p>
          </div>
        </section>

        <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
          data-testid="song">
          <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>Song</h3>
          <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
            Who sings each droplet&rsquo;s song, whatever the brain. The narration and the exam question are ElevenLabs&rsquo;
            either way. A droplet whose song another singer made has it made again the next time its videos run.
          </p>
          <div className="space-y-2" role="radiogroup" aria-label="Who sings the song">
            {settings.choices.song.provider.map((provider) => (
              <Choice key={provider} on={settings.song.provider === provider} disabled={saving !== null}
                label={singerOf(provider).label} note={singerOf(provider).note}
                isDefault={settings.defaults.song.provider === provider} onClick={() => pickSinger(provider)} />
            ))}
          </div>
        </section>

        {BRAINS.map(({ provider, title, who }) => (
          <section key={provider} className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            data-testid={`art-${provider}`}>
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>{title}</h3>
            <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>{who}</p>
            <div className="space-y-2" role="radiogroup" aria-label={`Who paints the pictures for ${title}`}>
              {settings.choices.art[provider].map((painter) => (
                <Choice key={painter} on={settings.art[provider] === painter} disabled={saving !== null}
                  label={painterOf(painter).label} note={painterOf(painter).note}
                  isDefault={settings.defaults.art[provider] === painter} onClick={() => pick(provider, painter)} />
              ))}
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

/** One option of a setting: a radio row with its name, a note on what it costs, and whether it is the default. */
function Choice({ on, disabled, label, note, isDefault, onClick }: {
  on: boolean; disabled: boolean; label: string; note: string; isDefault: boolean; onClick: () => void;
}) {
  return (
    <button type="button" role="radio" aria-checked={on} disabled={disabled} onClick={onClick}
      className="w-full text-left rounded-md px-3 py-2 transition-colors disabled:cursor-wait"
      style={{
        background: on ? 'rgba(175, 82, 222, 0.10)' : 'var(--bg-tertiary)',
        border: `1px solid ${on ? 'var(--accent-purple)' : 'var(--border)'}`,
      }}>
      <span className="flex items-center gap-2">
        <span className="inline-block w-3 h-3 rounded-full shrink-0"
          style={{ border: '2px solid var(--accent-purple)', background: on ? 'var(--accent-purple)' : 'transparent' }} />
        <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{label}</span>
        {isDefault && <span className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>default</span>}
      </span>
      <span className="block text-[11px] mt-1 ml-5" style={{ color: 'var(--text-secondary)' }}>{note}</span>
    </button>
  );
}
