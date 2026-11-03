/**
 * What the studio's checks found in a droplet, for an admin looking it over (GET /api/answers/:id/review, under the
 * lesson on /learn): the fact-check's verdict on every line and what it fixed, the code checks on the bundle, and the
 * art checker's look at each scene's picture, every try of it. None of it is shown to the learner or on the Droplets
 * tab.
 *
 * The fact-check comes with the lesson (bundle and factcheck, lib/droplet.ts), and so do the code checks (`checks`,
 * the studio's checks.json); the art checks come with READY (`artChecks`, from each scene's art/<scene>.json), with
 * where the kept picture is in the private bucket, so the page can show it beside what was said about it. The
 * picture is served through the API (GET /api/answers/:id/review/art/:scene): the bucket is private, and the site's
 * Content-Security-Policy loads pictures from itself and the public media domain alone.
 *
 * The studio writes both, so they are held to a shape and a size rather than trusted, and read back field by field:
 * a droplet made by an older studio, or with a field this API does not know, reads as what it has.
 */
import { fileError } from './storage.js';

export const CHECKS_MAX_BYTES = 64 * 1024;
export const ART_CHECKS_MAX_BYTES = 128 * 1024;
export const ART_SCENES_MAX = 40; // a bundle has 4 to 8 scenes
export const ART_TRIES_MAX = 10; // MAX_PAINTS is 3 (studio/steps/art.py)
// A kept picture: <attempt>/art/<scene>.png, inside the answer's folder in the private bucket (studio/steps/upload.py).
const ART_FILE_RE = /^(?:[A-Za-z0-9][A-Za-z0-9._-]*\/)?art\/[A-Za-z0-9][A-Za-z0-9._-]*\.png$/;

// Fact-check lines that are kept or dropped, never corrected: not counted as said (studio/steps/factcheck.py).
const KEPT_OR_DROPPED = ['tag', 'motion', 'avoid'];
// What the art checker reports on a picture (ArtCheck, studio/steps/art.py). `text` and `unnamed` are in checks made
// before Oct 31, when a picture was to have no labels of its own; `labels` (wrong ones) in those made since.
// `unfounded`: notes the checker wrote that name no fact of the lesson, dropped from the video (since Nov 3).
const ART_FINDINGS = ['labels', 'text', 'misplaced', 'unnamed', 'wrong', 'notes', 'unfounded'] as const;

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): string => (typeof v === 'string' ? v : '');
const texts = (v: unknown): string[] => (Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : []);
const count = (v: unknown): number | null => (Number.isInteger(v) && (v as number) >= 0 ? (v as number) : null);
const tooBig = (v: unknown, max: number) => Buffer.byteLength(JSON.stringify(v)) > max;

/** `checks` with the lesson: checks.json, { problems: [{ line_id, rule, detail, repaired }] }, or why it is not. */
export function checksError(checks: unknown): string | null {
  if (checks === undefined) return null;
  if (!isObject(checks) || !Array.isArray(checks.problems)) return 'checks must be an object with a problems array';
  if (!checks.problems.every((p) => isObject(p) && typeof p.rule === 'string' && typeof p.detail === 'string')) {
    return 'each of checks.problems must be an object with a rule and a detail';
  }
  if (tooBig(checks, CHECKS_MAX_BYTES)) return `checks must be ${CHECKS_MAX_BYTES / 1024} KB or less`;
  return null;
}

/** `artChecks` with READY: { scenes: [{ scene, file, model, kept, check, tries }] }, or why it is not. */
export function artChecksError(art: unknown): string | null {
  if (art === undefined) return null;
  if (!isObject(art) || !Array.isArray(art.scenes)) return 'artChecks must be an object with a scenes array';
  if (art.scenes.length > ART_SCENES_MAX) return `artChecks.scenes must have ${ART_SCENES_MAX} scenes or fewer`;
  for (const scene of art.scenes) {
    if (!isObject(scene) || typeof scene.scene !== 'string' || !scene.scene) return 'each of artChecks.scenes must name its scene';
    if (scene.file !== undefined && scene.file !== null) {
      if (fileError(scene.file) || !ART_FILE_RE.test(scene.file as string)) {
        return `artChecks: scene ${scene.scene}'s file must be its picture in the answer's folder, such as "a1/art/hook.png"`;
      }
    }
    if (scene.check !== undefined && scene.check !== null && !isObject(scene.check)) return `artChecks: scene ${scene.scene}'s check must be an object`;
    if (scene.tries !== undefined && !(Array.isArray(scene.tries) && scene.tries.every(isObject))) {
      return `artChecks: scene ${scene.scene}'s tries must be a list of objects`;
    }
    if (Array.isArray(scene.tries) && scene.tries.length > ART_TRIES_MAX) return `artChecks: scene ${scene.scene} has more than ${ART_TRIES_MAX} tries`;
  }
  if (tooBig(art, ART_CHECKS_MAX_BYTES)) return `artChecks must be ${ART_CHECKS_MAX_BYTES / 1024} KB or less`;
  return null;
}

/** What the art checker said about one picture: each kind of finding, a few words each. */
function findings(v: unknown) {
  const o = isObject(v) ? v : {};
  return Object.fromEntries(ART_FINDINGS.map((k) => [k, texts(o[k])])) as Record<(typeof ART_FINDINGS)[number], string[]>;
}

function factcheckReview(v: unknown) {
  if (!isObject(v)) return null;
  const lines = (Array.isArray(v.lines) ? v.lines : []).filter(isObject).map((l) => ({
    kind: text(l.kind), id: text(l.id), text: text(l.text), verdict: text(l.verdict) || null, source: text(l.source),
    round: count(l.round), problem: text(l.problem),
  }));
  const said = lines.filter((l) => !KEPT_OR_DROPPED.includes(l.kind));
  const tally = (verdict: string) => said.filter((l) => l.verdict === verdict).length;
  return {
    status: text(v.status), reason: text(v.reason), rounds: count(v.rounds),
    counts: { supported: tally('SUPPORTED'), filler: tally('FILLER'), unsupported: tally('UNSUPPORTED') },
    lines,
    corrections: (Array.isArray(v.corrections) ? v.corrections : []).filter(isObject).map((c) => ({
      round: count(c.round), kind: text(c.kind), ids: texts(c.ids), before: text(c.before), after: text(c.after),
      problem: text(c.problem),
    })),
  };
}

function checksReview(v: unknown) {
  if (!isObject(v) || !Array.isArray(v.problems)) return null;
  return {
    problems: v.problems.filter(isObject).map((p) => ({
      lineId: text(p.line_id), rule: text(p.rule), detail: text(p.detail), repaired: p.repaired === true,
    })),
  };
}

/** The address the page shows a scene's picture from: the API reads it out of the private bucket for an admin. */
export const artImagePath = (answerId: string, scene: string) =>
  `/api/answers/${encodeURIComponent(answerId)}/review/art/${encodeURIComponent(scene)}`;

function artReview(v: unknown, answerId: string, imagesServed: boolean) {
  if (!isObject(v) || !Array.isArray(v.scenes)) return null;
  return {
    scenes: v.scenes.filter(isObject).map((s) => {
      const scene = text(s.scene);
      return {
        scene,
        // Null for a scene with no picture uploaded, or while the API cannot read the bucket.
        imageUrl: imagesServed && typeof s.file === 'string' ? artImagePath(answerId, scene) : null,
        model: text(s.model) || null, // who painted it; null: drawn as SVG by the brain
        kept: count(s.kept), // the try kept
        check: isObject(s.check) ? findings(s.check) : null, // null: not looked at (a drawing)
        tries: (Array.isArray(s.tries) ? s.tries : []).filter(isObject).map((t) => ({
          try: count(t.try),
          // The painter would not make it (its content filter), or a redraw of a reference failed and was painted afresh.
          declined: text(t.declined) || null,
          redrawFailed: text(t.redraw_failed) || null,
          ...findings(t),
        })),
      };
    }),
  };
}

/** A scene's kept picture in the private bucket, from the art checks READY carried; null when there is none. */
export function artFile(artChecks: unknown, scene: string): string | null {
  if (!isObject(artChecks) || !Array.isArray(artChecks.scenes)) return null;
  const found = artChecks.scenes.find((s) => isObject(s) && s.scene === scene);
  const file = isObject(found) ? found.file : null;
  return typeof file === 'string' && !fileError(file) && ART_FILE_RE.test(file) ? file : null;
}

/** GET /api/answers/:id/review. imagesServed: the API has the bucket's settings, so it can serve each picture. */
export function serializeReview(row: { id: string; factcheck: unknown; checks: unknown; artChecks: unknown }, imagesServed: boolean) {
  return {
    id: row.id,
    factcheck: factcheckReview(row.factcheck), // null: a lesson posted without a bundle
    checks: checksReview(row.checks), // null: made before the code checks were sent
    art: artReview(row.artChecks, row.id, imagesServed), // null: no videos yet, or made before the art checks were sent
  };
}

export type Review = ReturnType<typeof serializeReview>;
