/**
 * A short lease on a question, taken when the queue hands it out.
 *
 * Two runs can overlap — the schedule can fire inside an API-triggered run — and
 * without a lease both are handed the same oldest five. Nothing breaks (the row
 * lock and the one-answer-per-question constraint see to that) but the loser
 * writes a full answer for every question and throws it away, which is the
 * expensive half of a run spent twice.
 *
 * The lease expires rather than being released. A run that dies mid-answer must
 * not hold a question out of the queue forever, and nothing outside the database
 * can be trusted to say "I finished". Answering removes the question from the
 * queue on its own, by flipping status, so the happy path never needs to clear
 * a claim.
 */
import { knob } from './knobs.js';

// The studio writes and fact-checks a lesson in about 5 minutes and renews the claim
// before each step (POST /api/questions/:id/claim), so 20 outlasts a slow step.
const DEFAULT_CLAIM_MINUTES = 20;

export function claimMinutes(): number {
  const n = parseInt(knob('QUESTION_CLAIM_MINUTES') ?? '', 10);
  // Long enough to outlast a run writing its batch; a bad value must not mean
  // "no lease at all", so anything unparseable falls back rather than to zero.
  if (Number.isNaN(n) || n <= 0) return DEFAULT_CLAIM_MINUTES;
  return n;
}

// Callers compare against `now() - make_interval(mins => claimMinutes())` in
// SQL rather than computing a cutoff here. claimedAt is written by the database
// clock, so the comparison has to use that same clock: an app server running
// ahead by one lease hands out questions a live run is already answering, which
// is silent and brings back the duplicated work the lease exists to remove.
// In production those are two machines — the API on Render, Postgres on Supabase.
