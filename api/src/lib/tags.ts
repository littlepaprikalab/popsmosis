/**
 * A droplet's hashtags: the words its two videos are tagged with, the way a TikTok is,
 * so the Droplets tab can be searched by them. The studio's brain writes them into the
 * bundle (`tags.song`, `tags.lesson`; studio/schemas.py), and the API stores each video's
 * on the answer when the lesson is posted (`songTags`, `lessonTags`).
 *
 * The API holds them to the studio's rules again rather than trusting the bundle: a tag
 * is what a search must match, so it is stored the one way a search is written.
 */

import { containsPersonalInfo } from './validation.js';

export const TAGS_PER_VIDEO = 10;
export const TAG_MAX_LENGTH = 40; // characters; a longer one is dropped, not cut short
const TAG_QUERY_MAX_INPUT = 200; // a search longer than this is not a tag, so it is not normalised
// Tags that find nothing a student searches for, dropped as the studio drops them (studio/schemas.py, NO_TAGS).
const NO_TAGS = new Set(['fyp', 'fypシ', 'foryou', 'foryoupage', 'viral', 'trending']);

/**
 * "#AP Bio" -> "apbio": lowercase letters (with their accents), digits and underscores,
 * with no "#". The same characters as the studio's tag_of(), so the tags it wrote and the
 * tags a search names match. Normalised again at the end, so a tag cleaned twice reads the
 * same (a stored tag, sent back as a search, must find itself): a spacing accent ("A¨")
 * leaves its combining mark beside the letter, which only then composes ("ä").
 */
export function tagOf(value: string): string {
  return value.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}_]+/gu, '').normalize('NFKC');
}

/**
 * Each tag as tagOf() writes it, the first of each kept, at most TAGS_PER_VIDEO, none of
 * NO_TAGS. Anything that is not a string is skipped, and so is a tag that reads as a phone
 * number: tags are public. (Cleaning already leaves no "@" for an email address.)
 */
export function cleanTags(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const tags: string[] = [];
  for (const value of values) {
    if (typeof value !== 'string' || value.length > TAG_QUERY_MAX_INPUT) continue;
    const tag = tagOf(value);
    if (tag && [...tag].length <= TAG_MAX_LENGTH && !tags.includes(tag) && !NO_TAGS.has(tag) && !containsPersonalInfo(tag)) {
      tags.push(tag);
    }
    if (tags.length === TAGS_PER_VIDEO) break;
  }
  return tags;
}

/** Each video's tags from a bundle; none for a bundle written before there were tags. */
export function bundleTags(bundle: unknown): { songTags: string[]; lessonTags: string[] } {
  const tags = typeof bundle === 'object' && bundle !== null ? (bundle as { tags?: unknown }).tags : undefined;
  const lists = typeof tags === 'object' && tags !== null ? (tags as { song?: unknown; lesson?: unknown }) : {};
  return { songTags: cleanTags(lists.song), lessonTags: cleanTags(lists.lesson) };
}

/**
 * The tag a search names ("#APBio", "ap bio"), or null when it names none ("", "#"): then
 * there is nothing to search for. One longer than any tag can be is still a search, which
 * finds nothing. A query naming it twice (?tag=a&tag=b) searches the first.
 */
export function tagQueryOf(value: unknown): string | null {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== 'string') return null;
  return tagOf(first.slice(0, TAG_QUERY_MAX_INPUT)) || null;
}

/**
 * How the page and the Droplets tab read them: each video's, in the order the brain ranked
 * them. The columns allow NULL (Prisma's lists do), so a row written by hand reads as none.
 */
export function serializeTags(a: { songTags: string[] | null; lessonTags: string[] | null }) {
  return { song: a.songTags ?? [], lesson: a.lessonTags ?? [] };
}
