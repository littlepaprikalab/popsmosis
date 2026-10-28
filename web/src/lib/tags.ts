/**
 * "#AP Bio" -> "apbio": a hashtag the way the API stores and searches it (api/src/lib/tags.ts),
 * so what someone types in the search box reads back as the tag it finds.
 */
export function tagOf(value: string): string {
  return value.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}_]+/gu, '');
}

/** The droplets tab searched by one tag. */
export function tagHref(tag: string): string {
  return `/droplets?tag=${encodeURIComponent(tag)}`;
}

/** Both videos' tags as one list, each once, in the order the brain ranked them: for a card. */
export function allTags(tags: { song: string[]; lesson: string[] } | undefined): string[] {
  return tags ? [...new Set([...tags.song, ...tags.lesson])] : [];
}
