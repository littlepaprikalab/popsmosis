/**
 * The style a droplet's song was made from. The learner only suggests one (or leaves it to the studio's rotation):
 * the studio's brain writes the style itself into the bundle (`music.positive_styles`, studio/schemas.py), and the
 * music model is given that (studio/steps/audio.py). The API copies it onto the answer when the lesson is posted
 * (`songStyle`), so the lesson card can say what the song was made from without reading the bundle.
 */

import { containsPersonalInfo } from './validation.js';

export const SONG_STYLE_SHOWN_MAX_LENGTH = 1000; // characters; a longer one is not a style, and is not kept

/**
 * The song's style from a bundle, its entries joined as the studio joins them for the music model, each on one line
 * without its closing full stop. Null for a bundle with no song (a lesson video alone), or a style that is too long or
 * reads as holding an email address or a phone number: it is shown to whoever sees the lesson.
 */
export function bundleSongStyle(bundle: unknown): string | null {
  const music = typeof bundle === 'object' && bundle !== null ? (bundle as { music?: unknown }).music : undefined;
  const styles = typeof music === 'object' && music !== null ? (music as { positive_styles?: unknown }).positive_styles : undefined;
  if (!Array.isArray(styles)) return null;
  const style = styles
    .filter((s): s is string => typeof s === 'string')
    .map((s) => s.split(/\s+/).join(' ').trim().replace(/[ .]+$/, ''))
    .filter(Boolean)
    .join(', ');
  return style && style.length <= SONG_STYLE_SHOWN_MAX_LENGTH && !containsPersonalInfo(style) ? style : null;
}
