/**
 * How a droplet was made, as the studio reports it (studio/ledger.py): a bill of materials.
 * Each step comes with its time and tries, what it cost, and the models that ran it.
 *
 * The lesson's steps arrive with the lesson (POST /api/questions/:id/answer), the videos'
 * with READY (PATCH /api/answers/:id/video). Like the bundle, they are stored as sent and
 * checked only for shape and size; serializeBreakdown reads them back keeping only the
 * fields it knows, of the types it expects, and works out the totals itself.
 */

// About 2 KB in practice (eleven steps, a model or two each); the cap keeps one from bloating a list of cards.
export const BREAKDOWN_MAX_BYTES = 16 * 1024;

const PARTS = ['lesson', 'videos'] as const;
type Part = (typeof PARTS)[number];

export interface BreakdownModel {
  model: string;
  calls: number;
  costUsd: number | null; // null: the provider charged in credits and no price was set for them
  credits: number | null; // what the provider charged in its own credits (ElevenLabs), when it said
}

export interface BreakdownStep {
  step: string; // the studio's step name: bundle, factcheck, art, audio, render...
  part: Part;
  seconds: number | null; // every try together; null when the only try was cut off
  tries: number;
  costUsd: number; // the priced calls
  models: BreakdownModel[]; // empty: code alone, no model
}

export interface Breakdown {
  steps: BreakdownStep[]; // the lesson's, then the videos'
  seconds: number | null; // wall time: the art and the audio run side by side, so less than the steps' sum
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

function readModel(raw: unknown): BreakdownModel | null {
  if (!isObject(raw) || !text(raw.model)) return null;
  return {
    model: text(raw.model),
    calls: Math.max(0, Math.round(amount(raw.calls) ?? 0)),
    costUsd: amount(raw.costUsd),
    credits: amount(raw.credits),
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
    });
  }
  return { steps, seconds: amount(raw.seconds) };
}

/** The droplet's bill of materials for the page, or null when the studio sent none. */
export function serializeBreakdown(lesson: unknown, videos: unknown): Breakdown | null {
  const parts = [readPart(lesson, 'lesson'), readPart(videos, 'videos')]
    .filter((p): p is NonNullable<typeof p> => p !== null);
  if (parts.length === 0) return null;
  const steps = parts.flatMap((p) => p.steps);
  const times = parts.map((p) => p.seconds).filter((s): s is number => s !== null);
  const models = steps.flatMap((s) => s.models);
  const unpriced = models.filter((m) => m.costUsd === null);
  const credits = unpriced.filter((m) => m.credits !== null);
  return {
    steps,
    seconds: times.length ? round(times.reduce((a, b) => a + b, 0), 1) : null,
    costUsd: round(steps.reduce((sum, s) => sum + s.costUsd, 0), 6),
    credits: credits.length ? credits.reduce((sum, m) => sum + (m.credits ?? 0), 0) : null,
    unpriced: unpriced.length - credits.length,
    complete: unpriced.length === 0,
  };
}
