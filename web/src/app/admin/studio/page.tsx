'use client';

import { useEffect, useState, useCallback, type FormEvent } from 'react';
import {
  api, ArtSettings, BrainSettings, Effort, Knob, KnobValue, Painter, ReviewSettings, SongProvider, SongSettings,
  SpendSettings, StudioSettings,
} from '@/lib/api';
import { BRAIN_MODELS, type Brain } from '@/lib/brains';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { AnnouncementEditor } from '@/components/AnnouncementEditor';
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
  gemini: { label: 'Gemini Lyria 3.5', note: '$0.08 a song, on the Gemini key (GEMINI_API_KEY). Sings the whole song from one prompt and keeps to the planned timing only roughly; the studio finds each line in it. A song Lyria will not sing (it blocks some grave topics, the Holocaust among them) is made by Eleven Music instead.' },
};
const singerOf = (provider: string) => SINGERS[provider as SongProvider] ?? { label: provider, note: '' };

const EFFORT_NOTE = 'How hard Claude thinks when it writes and checks a lesson; thinking is billed as output. Muse ignores it.';

// The knobs (api/src/lib/knobs.ts): environment variables an admin can set here instead, by section, in the order
// the page shows them. .env.example says the same of each variable.
type KnobText = { label: string; note: string; unit?: string; suggest?: string[] };
const KNOB_SECTIONS: { id: string; title: string; note: string; knobs: Record<string, KnobText> }[] = [
  {
    id: 'limits', title: 'Limits', note: 'What learners may ask for. A change applies to the next request.',
    knobs: {
      QUESTIONS_PER_USER_PER_DAY: { label: 'Topics a learner may ask for in 24 hours', unit: 'topics', note: '0 stops new topics. Taking one back or deleting it does not give its place back.' },
      TOPICS_PER_DAY: { label: 'Topics everyone may ask for in 24 hours', unit: 'topics', note: 'Every learner’s together: the ceiling on a day’s model spend, at about 70¢ to $1 a droplet. 0: no ceiling.' },
      REPLIES_PER_USER_PER_HOUR: { label: 'Follow-ups a learner may post in an hour', unit: 'follow-ups', note: 'Each one is a run of the teacher. A deleted follow-up does not give its place back.' },
      DROPLET_COMMENTS_PER_USER_PER_HOUR: { label: 'Comments a learner may post on the Droplets tab in an hour', unit: 'comments', note: 'Likes are held to 60 a minute and need no setting.' },
      ATTACHMENT_MB_PER_USER_PER_DAY: { label: 'Files a learner may send in 24 hours', unit: 'MB', note: 'With their topics and follow-ups. 0: no limit. Deleting a request does not give its bytes back.' },
      ATTACHMENT_MB_HELD_PER_USER: { label: 'Files popsmosis keeps of one learner at a time', unit: 'MB', note: 'The files live in the database, so this keeps one account from filling it. 0: no limit. Deleting a request frees its files.' },
    },
  },
  {
    id: 'leases', title: 'Leases and renders', note: 'How long the API waits on the teacher. A change applies to the next request.',
    knobs: {
      QUESTION_CLAIM_MINUTES: { label: 'How long a run holds a topic', unit: 'minutes', note: 'The queue leases a topic to one run for this long, so two runs never make the same one. The studio renews it before each step and every 5 minutes while one runs: keep it above 5.' },
      VIDEO_STALE_MINUTES: { label: 'How long a render may go quiet', unit: 'minutes', note: 'A render that has not reported for this long has died with its machine: the page says it failed, and a later run recovers it.' },
      LESSON_HOLD_MINUTES: { label: 'How long LEARN holds a lesson for its videos', unit: 'minutes', note: 'After the lesson is posted, its card shows the bill filling in until the videos are done or this passes. 0 never waits.' },
      VIDEO_MAX_ATTEMPTS: { label: 'Attempts at a droplet’s videos', unit: 'attempts', note: 'The first render, then each recovery of one that died with its machine. A recovery pays for the song and the art again.' },
    },
  },
  {
    id: 'routine', title: 'Waking the teacher', note: 'Only while the routine’s API trigger is set (POPSMOSIS_ROUTINE_FIRE_URL and its token, in the environment). A change applies to the next write.',
    knobs: {
      POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS: { label: 'Cooldown between wake-ups', unit: 'seconds', note: 'Writes inside it collapse into one wake-up when it ends. Keep it at or above how long one run takes; 0 wakes the teacher on every write.' },
      POPSMOSIS_ROUTINE_BETA: { label: 'The fire endpoint’s beta header', note: 'Roll it forward when the routines API dates a new one.' },
    },
  },
  {
    id: 'studio', title: 'The teacher’s machine', note: 'The studio reads these as it starts each run and from every claim, over its own environment: a change applies from the next run. Not set here, the variable on the teacher’s machine decides, else the default.',
    knobs: {
      STUDIO_VIDEOS: { label: 'Videos', note: 'on makes the song and the lesson video. lesson leaves the song out from the start: a lesson written while it is set has no lyrics or music to write or fact-check, and gets only the narrated lesson video, with no hook clip and no Eleven Music or Lyria spend; it stays songless if the knob changes later. A lesson already written with its song keeps it checked, and any video not yet made is the lesson’s alone. off makes text-only droplets: no video claim, no media steps, no recovery.' },
      ELEVENLABS_VOICE_ID: { label: 'The narration’s voice', note: 'Any ElevenLabs voice id, on the ElevenLabs key. A droplet already narrated keeps its voice.' },
      GEMINI_SONG_MODEL: { label: 'The Lyria model, when Lyria sings', note: 'lyria-3.5 sings full songs ($0.08 a song); lyria-3-clip-preview makes 30-second clips ($0.04).', suggest: ['lyria-3.5', 'lyria-3-clip-preview'] },
      ELEVENLABS_USD_PER_1K_CREDITS: { label: 'What 1,000 ElevenLabs credits cost', unit: 'USD', note: 'On your plan, e.g. 0.30, so a droplet’s bill shows its ElevenLabs lines in dollars as well as credits. Unset: credits only.' },
      STUDIO_CLAUDE_DRAWS: { label: 'What Claude draws as SVG, whatever paints the rest', note: 'math: every chart and graph, and every picture of a math lesson but its hook and analogy, since a painter cannot count (Muse Image painted six fraction bars as seven). charts: charts and graphs only. brain: only Claude’s brain draws its charts; a Muse topic has them painted. On a Muse topic Claude Opus 5.5 draws them, on the Anthropic key, about 7¢ a picture; without that key they are painted.' },
    },
  },
];
// A knob an API newer than this page has: shown by its name rather than left out.
const knobText = (name: string): KnobText =>
  KNOB_SECTIONS.find((section) => name in section.knobs)?.knobs[name] ?? { label: name, note: '' };

/** The page's sections of knobs, with only the knobs the API has, and any this page does not know at the end. */
function knobSections(knobs: Record<string, Knob>) {
  const known = new Set(KNOB_SECTIONS.flatMap((section) => Object.keys(section.knobs)));
  const other = Object.keys(knobs).filter((name) => !known.has(name));
  return [
    ...KNOB_SECTIONS.map(({ id, title, note, knobs: texts }) => ({ id, title, note, names: Object.keys(texts).filter((name) => name in knobs) })),
    { id: 'other', title: 'Other', note: '', names: other },
  ].filter((section) => section.names.length > 0);
}
const dollars = (usd: number) => (usd === 0 ? 'no ceiling' : `$${usd.toFixed(2)}`);

/**
 * Admin page: first the announcement under the nav on every page (components/AnnouncementEditor.tsx), for news like
 * songs being off. Then the studio's settings (api/src/lib/studio-settings.ts). The default brain, who sings and who paints
 * each droplet's pictures, by the brain that wrote its lesson; the teacher reads them as it starts each run, so a
 * change applies from the next one, with no redeploy. Then the knobs (api/src/lib/knobs.ts): the environment
 * variables an admin can set here instead, the API's in force at once, the studio's from the next run.
 */
export default function AdminStudioPage() {
  const { user: me, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [settings, setSettings] = useState<StudioSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [ceiling, setCeiling] = useState(''); // the spend ceiling as typed, until it is saved

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await api.getStudioSettings();
      setSettings(loaded);
      setCeiling(loaded.spend ? String(loaded.spend.topicUsd) : '');
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

  const save = async (what: string, change: {
    brain?: Partial<BrainSettings>; song?: SongSettings; art?: Partial<ArtSettings>; knobs?: Record<string, KnobValue | null>;
    spend?: SpendSettings; review?: ReviewSettings;
  }, done: string) => {
    if (saving) return;
    setSaving(what);
    try {
      const saved = await api.setStudioSettings(change);
      setSettings(saved);
      if (change.spend && saved.spend) setCeiling(String(saved.spend.topicUsd));
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
  const saveCeiling = (event: FormEvent) => {
    event.preventDefault();
    const topicUsd = Number(ceiling);
    const max = settings?.choices.spend?.topicUsd.max ?? 100;
    if (ceiling.trim() === '' || !Number.isFinite(topicUsd) || topicUsd < 0 || topicUsd > max) {
      showToast(`The ceiling is a number of dollars from 0 (no ceiling) to ${max}`, 'error');
      return;
    }
    if (settings?.spend?.topicUsd === topicUsd) return;
    save('spend', { spend: { topicUsd } },
      topicUsd === 0 ? 'Topics have no spend ceiling from the next step.' : `Each topic stops at ${dollars(topicUsd)} from the next step.`);
  };
  const pickReview = (required: boolean) => {
    if (settings?.review?.required === required) return;
    save('review', { review: { required } }, required
      ? 'From the next droplet, each waits for an admin before it lists on the Droplets tab.'
      : 'From the next droplet, each lists on the Droplets tab as soon as its videos are done.');
  };
  const pickEffort = (effort: Effort) => {
    if (settings?.brain.effort === effort) return;
    save('effort', { brain: { effort } }, `Claude thinks at ${effort} effort from the next run.`);
  };
  const setKnob = (name: string, value: KnobValue | null) => {
    const knob = settings?.knobs[name];
    if (!knob || knob.value === value) return;
    const { label, unit } = knobText(name);
    const when = knob.where === 'api' ? 'from now on' : 'from the teacher’s next run';
    save(`knob:${name}`, { knobs: { [name]: value } }, value === null
      ? `${label}: back to ${knob.where === 'api' ? 'the API’s' : 'the teacher’s'} environment, else the default, ${when}.`
      : `${label}: ${value}${unit ? ` ${unit}` : ''} ${when}.`);
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
      <div className="mb-10">
        <AnnouncementEditor />
      </div>

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">STUDIO</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          The brain that writes each lesson, who sings its song, who paints its pictures and the most a topic may cost. The teacher reads this as it starts each topic
          and each droplet&rsquo;s videos, so a change applies from the next run. Its machine holds only the API keys. Below them,
          the <a href="#knobs" className="underline">knobs</a> that used to need a redeploy.
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

        {settings.review && (
          <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            data-testid="review">
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>Review before the Droplets tab</h3>
            <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
              Whether a person looks at each droplet before it lists on the public Droplets tab, which people 13 and up
              watch. With review on, a droplet whose videos are done waits until an admin approves it on the LEARN page;
              the learner who asked sees it either way. A change applies to droplets finished after it.
            </p>
            <div className="space-y-2" role="radiogroup" aria-label="Review before the Droplets tab">
              <Choice on={settings.review.required} disabled={saving !== null} label="An admin approves each droplet"
                note="Nothing lists until someone has watched it. Approve on the LEARN page, where each waiting droplet says so."
                isDefault={settings.defaults.review?.required === true} onClick={() => pickReview(true)} />
              <Choice on={!settings.review.required} disabled={saving !== null} label="List as soon as the videos are done"
                note="The fact-check and the classroom check are the only gates. An admin can still hide a droplet afterwards."
                isDefault={settings.defaults.review?.required === false} onClick={() => pickReview(false)} />
            </div>
          </section>
        )}

        {settings.spend && (
          <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            data-testid="spend">
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>Spend ceiling</h3>
            <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
              The most one topic may cost in model calls: its lesson, its videos and every retry together. Once a topic has
              cost this much, the teacher makes no more calls for it, and the topic or its videos are given up with the reason.
              A topic has cost $0.50 to $2 so far. ElevenLabs calls count only when the teacher has
              ELEVENLABS_USD_PER_1K_CREDITS. 0 for no ceiling.
            </p>
            <form className="flex flex-wrap items-center gap-2" onSubmit={saveCeiling} noValidate>
              <label className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}
                htmlFor="spend-ceiling">Dollars a topic:</label>
              <input id="spend-ceiling" type="number" min={0} max={settings.choices.spend?.topicUsd.max ?? 100} step="0.01"
                inputMode="decimal" value={ceiling} onChange={(e) => setCeiling(e.target.value)} disabled={saving !== null}
                className="w-24 rounded px-2 py-1 text-sm"
                style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
              <button type="submit" disabled={saving !== null || ceiling === String(settings.spend.topicUsd)}
                className="px-3 py-1 rounded text-xs font-bold uppercase tracking-wider disabled:opacity-50"
                style={{ color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)' }}>Save</button>
            </form>
            <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }} data-testid="spend-now">
              Now: {dollars(settings.spend.topicUsd)}.{settings.defaults.spend ? ` Default: ${dollars(settings.defaults.spend.topicUsd)}.` : ''}
            </p>
          </section>
        )}

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
        the next time its videos run. Charts, graphs and a math lesson&rsquo;s pictures are drawn by Claude as SVG
        whoever paints the rest, as the knob <a href="#knobs" className="underline">What Claude draws</a> says.
      </p>

      <div id="knobs" className="mt-10 mb-4 scroll-mt-20">
        <h2 className="text-2xl font-bold tracking-wider mb-1">KNOBS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Environment variables you can set here instead, so a change needs no redeploy. Set here, a value wins over the
          variable; cleared, the variable is read again, else the default. The API&rsquo;s own apply at once (on another
          API instance within seconds); the teacher&rsquo;s apply from its next run. Keys, secrets and URLs stay in the environment.
        </p>
      </div>
      <div className="space-y-4">
        {knobSections(settings.knobs).map(({ id, title, note, names }) => (
          <section key={id} className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            data-testid={`knobs-${id}`}>
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-purple)' }}>{title}</h3>
            {note && <p className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>{note}</p>}
            <div className="mt-2 divide-y divide-[var(--border)]">
              {names.map((name) => (
                <KnobRow key={name} name={name} knob={settings.knobs[name]} text={knobText(name)}
                  disabled={saving !== null} onSave={(value) => setKnob(name, value)} />
              ))}
            </div>
          </section>
        ))}
      </div>
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

/** What a knob takes, in words, for the line under it. */
function knobRange(knob: Knob): string {
  if (knob.kind === 'int') return `a whole number from ${knob.min} to ${knob.max}`;
  if (knob.kind === 'number') return `a number from ${knob.min} to ${knob.max}`;
  if (knob.kind === 'choice') return (knob.choices ?? []).join(' or ');
  return knob.rule ?? 'text';
}

/** The value typed for a knob: undefined while the box is empty, or why it is not one the knob takes. */
function parseKnobDraft(knob: Knob, draft: string): { value?: KnobValue; error?: string } {
  const text = draft.trim();
  if (text === '') return {};
  if (knob.kind === 'text' || knob.kind === 'choice') return { value: text };
  const n = knob.kind === 'int' ? (/^-?\d+$/.test(text) ? Number(text) : NaN) : (/^-?\d*\.?\d+$/.test(text) ? Number(text) : NaN);
  if (Number.isNaN(n) || (knob.min !== undefined && n < knob.min) || (knob.max !== undefined && n > knob.max)) {
    return { error: `Takes ${knobRange(knob)}.` };
  }
  return { value: n };
}

/**
 * One knob: what it does, the value set here if any, and where the value in force comes from. A choice saves on a
 * click; anything else saves from its box (Enter or Save). Clear gives it back to the environment.
 */
function KnobRow({ name, knob, text, disabled, onSave }: {
  name: string; knob: Knob; text: KnobText; disabled: boolean; onSave: (value: KnobValue | null) => void;
}) {
  const shown = knob.value === null ? '' : String(knob.value);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const parsed = parseKnobDraft(knob, draft);
  const changed = parsed.value !== undefined && parsed.value !== knob.value;
  const source = knob.value !== null ? 'Set here'
    : knob.where === 'studio' ? 'Not set here'
    : knob.env ? 'From the environment' : 'Default';
  const unit = text.unit ? ` ${text.unit}` : '';
  const facts = knob.where === 'api'
    ? [`in force: ${knob.inForce}${unit}`, `environment: ${knob.env ? knob.env : 'unset'}`, `default: ${knob.default}${unit}`]
    : [`default: ${knob.default ?? 'none'}${knob.default === null ? '' : unit}`,
      knob.value === null ? 'the teacher’s environment decides' : 'wins over the teacher’s environment'];
  const listId = `knob-${name}-suggest`;
  const clear = knob.value !== null && (
    <button type="button" disabled={disabled} onClick={() => onSave(null)} aria-label={`Clear ${text.label}`}
      title="Back to the environment's value, else the default"
      className="px-2.5 py-1 rounded text-[10px] font-bold uppercase tracking-wider disabled:opacity-50 disabled:cursor-wait"
      style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
      Clear
    </button>
  );

  return (
    <div className="py-3 first:pt-1" data-testid={`knob-${name}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{text.label}</span>
        <span className="text-[9px] uppercase tracking-wider" data-testid="knob-source"
          style={{ color: knob.value !== null ? 'var(--accent-purple)' : 'var(--text-muted)' }}>{source}</span>
      </div>
      {text.note && <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-secondary)' }}>{text.note}</p>}
      {knob.kind === 'choice' ? (
        <div className="flex flex-wrap items-center gap-2 mt-2" role="radiogroup" aria-label={text.label}>
          {(knob.choices ?? []).map((choice) => {
            const on = (knob.value ?? (knob.where === 'api' ? knob.inForce : null)) === choice;
            return (
              <button key={choice} type="button" role="radio" aria-checked={on} disabled={disabled} onClick={() => onSave(choice)}
                className={`px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-colors rounded-full border disabled:cursor-wait ${
                  on ? 'text-[var(--btn-filled-text)] border-transparent' : 'text-[var(--accent-purple)] border-[var(--accent-purple)] hover:bg-[var(--accent-purple)]/10'
                }`}
                style={on ? { background: 'var(--accent-purple)' } : {}}>
                {choice}
              </button>
            );
          })}
          {clear}
        </div>
      ) : (
        <form className="flex flex-wrap items-center gap-2 mt-2"
          onSubmit={(e) => { e.preventDefault(); if (changed && !disabled) onSave(parsed.value!); }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={text.label}
            inputMode={knob.kind === 'int' ? 'numeric' : knob.kind === 'number' ? 'decimal' : 'text'}
            placeholder={knob.where === 'api' ? String(knob.inForce) : knob.default === null ? 'unset' : String(knob.default)}
            list={text.suggest ? listId : undefined} autoComplete="off" spellCheck={false}
            className={`${knob.kind === 'text' ? 'flex-1 min-w-0 max-w-sm' : 'w-32'} px-2 py-1 rounded text-sm font-mono`}
            style={{ background: 'var(--bg-tertiary)', border: `1px solid ${parsed.error ? 'var(--accent-red)' : 'var(--border)'}`, color: 'var(--text-primary)' }} />
          {text.suggest && (
            <datalist id={listId}>{text.suggest.map((option) => <option key={option} value={option} />)}</datalist>
          )}
          <button type="submit" disabled={disabled || !changed}
            className="px-2.5 py-1 rounded text-[10px] font-bold uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)' }}>
            Save
          </button>
          {clear}
        </form>
      )}
      {parsed.error && <p className="text-[11px] mt-1" style={{ color: 'var(--accent-red)' }} role="alert">{parsed.error}</p>}
      <p className="text-[10px] mt-1.5 break-words" style={{ color: 'var(--text-muted)' }} data-testid="knob-facts">
        <span className="font-mono">{name}</span> · {facts.join(' · ')} · takes {knobRange(knob)}
      </p>
    </div>
  );
}
