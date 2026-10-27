/**
 * Inkmory (docs/PLAN.md, section 11): the teacher's public record, its journal, cycle logs and
 * life story. The teacher tags what it writes there about a topic with `popsmosis:<topic id>`
 * (docs/popsmosis-agent-skill.md, Step 5), and Inkmory's search page lists every record carrying
 * that tag, so a lesson card can link to what the teacher wrote about making it.
 */

/** Inkmory's site, from NEXT_PUBLIC_INKMORY_URL at build time (next.config.ts). Empty: no links. */
const INKMORY_URL = process.env.NEXT_PUBLIC_INKMORY_URL || '';

/** The tag the teacher puts on its Inkmory records about one topic: the question's id. */
export function inkmoryRef(questionId: string): string {
  return `popsmosis:${questionId}`;
}

/** Inkmory's page of every record about one topic, or null when no Inkmory is configured. */
export function inkmoryRecordsUrl(questionId: string, base: string = INKMORY_URL): string | null {
  if (!base) return null;
  return `${base}/search?${new URLSearchParams({ ref: inkmoryRef(questionId) })}`;
}
