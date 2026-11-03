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
 *
 * Each machine that starts on a topic is counted, so a topic whose every run dies
 * before its lesson is posted is given up after QUESTION_MAX_RUNS of them rather
 * than paid for again on every sweep (questionMaxRuns, below).
 */
import { knob } from './knobs.js';

// The studio takes 20 minutes or more to write and fact-check a lesson, and renews the claim
// before each step (POST /api/questions/:id/claim) and every 5 minutes while one runs, so 20
// holds a topic for a run that is alive however long its steps, and frees it soon after one dies.
const DEFAULT_CLAIM_MINUTES = 20;

export function claimMinutes(): number {
  const n = parseInt(knob('QUESTION_CLAIM_MINUTES') ?? '', 10);
  // Long enough to outlast a run writing its batch; a bad value must not mean
  // "no lease at all", so anything unparseable falls back rather than to zero.
  if (Number.isNaN(n) || n <= 0) return DEFAULT_CLAIM_MINUTES;
  return n;
}

// How many runs may start on one topic. A run whose machine goes away before it posts the lesson (it outlasted the
// routine's hour, or died) lets the lease lapse, and the queue would hand the topic to the next run, which starts it
// over and pays for it again, for ever. The studio sends its run's id with every claim of the run; the first claim
// with an id the topic has not seen last counts one more run (the question's `runs` and `lastRunId`), and a renewal
// or a retry with the same id counts nothing, even when the reply to the first was lost. Not the hand-out: a run
// that stops before it tries the topic (its settings are wrong) uses none. Once a topic has had this many, a new
// run's claim is turned away while the last run still holds the lease, and gives the topic up once that lease has
// lapsed, as the queue does.
const DEFAULT_MAX_RUNS = 3;

export function questionMaxRuns(): number {
  const n = parseInt(knob('QUESTION_MAX_RUNS') ?? '', 10);
  return Number.isNaN(n) || n <= 0 ? DEFAULT_MAX_RUNS : n;
}

// Callers compare against `now() - make_interval(mins => claimMinutes())` in
// SQL rather than computing a cutoff here. claimedAt is written by the database
// clock, so the comparison has to use that same clock: an app server running
// ahead by one lease hands out questions a live run is already answering, which
// is silent and brings back the duplicated work the lease exists to remove.
// In production those are two machines — the API on Render, Postgres on Supabase.
