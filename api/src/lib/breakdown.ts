/**
 * How a droplet was made, as the studio reports it (studio/ledger.py): a bill of materials.
 * Each step comes with its time and tries, what it cost, the models that ran it, and the
 * other software it ran on the studio's machine with the version installed there. Each model
 * and tool also gets who made it (makerOf), worked out here.
 *
 * The lesson's steps arrive with the lesson (POST /api/questions/:id/answer), the videos'
 * with READY (PATCH /api/answers/:id/video). Like the bundle, they are stored as sent and
 * checked only for shape and size; serializeBreakdown reads them back keeping only the
 * fields it knows, of the types it expects, and works out the totals itself.
 */

import { containsPersonalInfo, containsSecret } from './validation.js';

// About 2 KB in practice (eleven steps, a model or two each); the cap keeps one from bloating a list of cards.
export const BREAKDOWN_MAX_BYTES = 16 * 1024;

const PARTS = ['lesson', 'videos'] as const;
type Part = (typeof PARTS)[number];

export interface BreakdownModel {
  model: string;
  maker: string | null; // who made it (makerOf); null for a name not listed there
  calls: number;
  costUsd: number | null; // null: the provider charged in credits and no price was set for them
  credits: number | null; // what the provider charged in its own credits (ElevenLabs), when it said
  // 'session': calls the teacher's own Claude session answered after the API failed (studio/session.py), on the
  // routine's plan rather than the studio's API key, so they add nothing to the bill. Absent: the API answered.
  via?: 'session';
  // What the line's calls did, in a few words ("checks the pictures": studio/ledger.py's TASKS); absent when unknown.
  task?: string;
}

/** Software a step ran on the studio's machine besides its models (studio/ledger.py's TOOLS). */
export interface BreakdownTool {
  name: string; // as it is installed: demucs, torchaudio, torch, skia-python, ffmpeg
  version: string | null; // as installed on the machine that made the videos; null: it did not say
  maker: string | null;
  task?: string; // what it did in the step ("runs htdemucs": studio/ledger.py's TOOLS); absent on an older bill
}

export interface BreakdownStep {
  step: string; // the studio's step name: bundle, factcheck, art, audio, render...
  part: Part;
  seconds: number | null; // every try together; null when the only try was cut off
  tries: number;
  costUsd: number; // the priced calls
  models: BreakdownModel[]; // empty: code alone, no model
  tools: BreakdownTool[]; // empty: none, or a bill sent before the studio listed them
}

export interface Breakdown {
  steps: BreakdownStep[]; // the lesson's, then the videos'
  seconds: number | null; // wall time: the art and the audio run side by side, so less than the steps' sum
  // Each part's wall time, the two adding up to `seconds`; null: that part's bill is not here, or has no time.
  // The page sets them against the time from the request, to say how much of it was waiting (EndToEnd).
  partSeconds: Record<Part, number | null>;
  costUsd: number; // every priced call
  credits: number | null; // credits of the calls with no price
  unpriced: number; // models whose calls have neither a price nor credits (the provider did not say)
  complete: boolean; // every call priced
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Null when a breakdown sent with a lesson or a READY can be stored (or none was sent), else why not. */
export function breakdownError(value: unknown): string | null {
  if (value === undefined) return null;
  if (!isObject(value) || !Array.isArray(value.steps)) return 'breakdown must be an object with a steps array';
  if (Buffer.byteLength(JSON.stringify(value)) > BREAKDOWN_MAX_BYTES) {
    return `breakdown must be ${BREAKDOWN_MAX_BYTES / 1024} KB or less`;
  }
  return null;
}

const amount = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);
const text = (v: unknown, max = 100): string => (typeof v === 'string' ? v.slice(0, max) : '');
const round = (v: number, places: number) => Math.round(v * 10 ** places) / 10 ** places;

/**
 * Who made each model and tool the studio names, by the start of its name. Worked out here rather
 * than sent, so a bill stored before makers were shown has them too.
 */
const MAKERS: [prefix: string, maker: string][] = [
  ['claude-', 'Anthropic'],
  ['muse-', 'Meta'], // a Muse brain, its helper and Muse Image (studio/brain.py, studio/muse.py)
  ['sam-', 'Meta'], // SAM 3.1, which finds the structures in painted art (studio/steps/locate.py)
  ['htdemucs', 'Meta'], // Demucs, the model and its package, from Meta AI Research (facebookresearch/demucs)
  ['demucs', 'Meta'],
  ['mms-', 'Meta'], // the forced aligner from Meta's Massively Multilingual Speech
  ['torch', 'Meta'], // PyTorch and torchaudio: started at Meta, run by the PyTorch Foundation since 2022
  ['lyria-', 'Google'],
  ['gemini-', 'Google'], // Gemini 3.1 Flash Image, when it paints the art (studio/gemini.py)
  ['skia-python', 'Google'], // Python bindings for Google's Skia
  ['eleven_', 'ElevenLabs'],
  ['music_', 'ElevenLabs'], // Eleven Music
  ['ffmpeg', 'FFmpeg'],
];

export function makerOf(name: string): string | null {
  const lower = name.toLowerCase();
  return MAKERS.find(([prefix]) => lower.startsWith(prefix))?.[1] ?? null;
}

function readModel(raw: unknown): BreakdownModel | null {
  if (!isObject(raw) || !text(raw.model)) return null;
  return {
    model: text(raw.model),
    maker: makerOf(text(raw.model)),
    calls: Math.max(0, Math.round(amount(raw.calls) ?? 0)),
    costUsd: amount(raw.costUsd),
    credits: amount(raw.credits),
    ...(raw.via === 'session' && { via: 'session' as const }),
    ...(text(raw.task) && { task: text(raw.task).slice(0, 60) }),
  };
}

function readTool(raw: unknown): BreakdownTool | null {
  if (!isObject(raw) || !text(raw.name)) return null;
  return {
    name: text(raw.name), version: text(raw.version) || null, maker: makerOf(text(raw.name)),
    ...(text(raw.task) && { task: text(raw.task).slice(0, 60) }),
  };
}

function readPart(raw: unknown, part: Part): { steps: BreakdownStep[]; seconds: number | null } | null {
  if (!isObject(raw) || !Array.isArray(raw.steps)) return null;
  const steps: BreakdownStep[] = [];
  for (const s of raw.steps) {
    if (!isObject(s) || !text(s.step, 40)) continue;
    const models = (Array.isArray(s.models) ? s.models : []).map(readModel).filter((m): m is BreakdownModel => !!m);
    steps.push({
      step: text(s.step, 40),
      part,
      seconds: amount(s.seconds),
      tries: Math.max(0, Math.round(amount(s.tries) ?? 0)),
      // Worked out from the models rather than taken as sent, so a step always adds up.
      costUsd: round(models.reduce((sum, m) => sum + (m.costUsd ?? 0), 0), 6),
      models,
      tools: (Array.isArray(s.tools) ? s.tools : []).map(readTool).filter((t): t is BreakdownTool => !!t),
    });
  }
  return { steps, seconds: amount(raw.seconds) };
}

/**
 * Every model a stored bill names, step by step, as stored: any type, repeats and all; null when there is no bill.
 * The Droplets tab's list has Postgres pick out the same (BILL_MODELS_PATH), so only the names leave the database.
 */
export function billModels(bill: unknown): unknown[] | null {
  if (!isObject(bill)) return null;
  const steps = Array.isArray(bill.steps) ? bill.steps.filter(isObject) : [];
  return steps.flatMap((s) => (Array.isArray(s.models) ? s.models.filter(isObject).map((m) => m.model) : []));
}

/** billModels() in SQL: jsonb_path_query_array(bill, BILL_MODELS_PATH::jsonpath), NULL for no bill. */
export const BILL_MODELS_PATH = '$.steps[*].models[*].model';

/**
 * A model's name as the studio writes them: claude-opus-5-5, gemini-3.1-flash-image, eleven_multilingual_v2, mms-fa.
 * The bill is the agent's to send, and the agent is an LLM that reads what learners write, so on the public tab a name
 * of any other shape is left out, as is one that reads as a key or as personal information (lib/validation.ts).
 */
const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const publicModelName = (name: string) => MODEL_NAME.test(name) && !containsSecret(name) && !containsPersonalInfo(name);
// A droplet names 8 or so; the cap holds a card's row to a line or two whatever a bill says.
export const MADE_WITH_MAX_MODELS = 32;

/** One maker's models on a droplet, as the Droplets tab says what made it. */
export interface MadeWith {
  maker: string | null; // makerOf; null: the models no maker is listed for
  models: string[];
}

/**
 * What made a droplet, for anyone watching it: every model its bill names, each once, under who made it. The makers
 * come in the order the bill first names one of theirs (the brain first), with the models no maker is listed for
 * last; the software that is not a model (ffmpeg, Skia) and what each call cost stay on the bill.
 *
 * Null unless both bills are there: a droplet on the tab has videos, and a lesson's bill alone would leave out who
 * drew the diagrams and who sang. Null as well when the bills name no model fit to show (publicModelName). At most
 * MADE_WITH_MAX_MODELS, the first the bill names.
 */
export function madeWithOf(lessonModels: unknown, videoModels: unknown): MadeWith[] | null {
  if (!Array.isArray(lessonModels) || !Array.isArray(videoModels)) return null;
  const seen = new Set<string>();
  const makers = new Map<string | null, string[]>();
  for (const raw of [...lessonModels, ...videoModels]) {
    const model = text(raw).trim();
    if (seen.has(model) || !publicModelName(model)) continue;
    if (seen.size === MADE_WITH_MAX_MODELS) break;
    seen.add(model);
    const maker = makerOf(model);
    const models = makers.get(maker);
    if (models) models.push(model);
    else makers.set(maker, [model]);
  }
  const groups = [...makers].map(([maker, models]) => ({ maker, models }));
  return groups.length ? [...groups.filter((g) => g.maker), ...groups.filter((g) => !g.maker)] : null;
}

/**
 * How long a droplet took from its request, by the API's clock, in seconds: to its lesson posted, and to its videos
 * READY. The bill's own time counts the studio's steps alone (the art and the audio side by side); this counts what
 * the learner waited through as well: the queue before the studio started, the hand-off from the lesson to the
 * videos, and the gap before a retry.
 */
export interface EndToEnd {
  lesson: number;
  videos: number | null; // null: the videos are not READY (still rendering, given up), or there are none
}

const since = (from: Date, to: Date) => Math.max(0, Math.round((to.getTime() - from.getTime()) / 100) / 10);

/**
 * How long the droplet took from `askedAt`, its question's createdAt. READY is final, so its videoUpdatedAt is when.
 * The videos never before the lesson: the database's clock stamps the lesson, this server's the READY, and the two
 * may be a moment apart.
 */
export function endToEndOf(
  askedAt: Date, answer: { createdAt: Date; videoStatus: string | null; videoUpdatedAt: Date | null },
): EndToEnd {
  const lesson = since(askedAt, answer.createdAt);
  const ready = answer.videoStatus === 'READY' ? answer.videoUpdatedAt : null;
  return { lesson, videos: ready ? Math.max(lesson, since(askedAt, ready)) : null };
}

/** The droplet's bill of materials for the page, or null when the studio sent none. */
export function serializeBreakdown(lesson: unknown, videos: unknown): Breakdown | null {
  const lessonPart = readPart(lesson, 'lesson');
  const videosPart = readPart(videos, 'videos');
  const parts = [lessonPart, videosPart].filter((p): p is NonNullable<typeof p> => p !== null);
  if (parts.length === 0) return null;
  const steps = parts.flatMap((p) => p.steps);
  const times = parts.map((p) => p.seconds).filter((s): s is number => s !== null);
  const models = steps.flatMap((s) => s.models);
  const unpriced = models.filter((m) => m.costUsd === null);
  const credits = unpriced.filter((m) => m.credits !== null);
  return {
    steps,
    seconds: times.length ? round(times.reduce((a, b) => a + b, 0), 1) : null,
    partSeconds: { lesson: lessonPart?.seconds ?? null, videos: videosPart?.seconds ?? null },
    costUsd: round(steps.reduce((sum, s) => sum + s.costUsd, 0), 6),
    credits: credits.length ? credits.reduce((sum, m) => sum + (m.credits ?? 0), 0) : null,
    unpriced: unpriced.length - credits.length,
    complete: unpriced.length === 0,
  };
}
