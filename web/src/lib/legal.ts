/**
 * The facts the Privacy Policy (/privacy) and the Terms of Use (/terms) are built on, in one place. Change them
 * here, and move LAST_UPDATED whenever either page changes what it promises.
 */

/** Who runs Popsmosis: the "we" in both pages, and who people's data is entrusted to. */
export const OPERATOR_NAME = 'Little Paprika Lab';

/**
 * Where people write about their data, takedowns, mistakes in a lesson and these terms. Set it before launch: while
 * it is empty both pages say so in place of the address. A role address (privacy@ or hello@ your domain) keeps a
 * personal inbox off the pages.
 */
export const CONTACT_EMAIL = '';

/**
 * The law the Terms are governed by, as it reads after "the laws of": "the State of California, USA". Empty reads as
 * the place the operator is based.
 */
export const GOVERNING_LAW = '';

export const LAST_UPDATED = '27 October 2026';

/** The youngest someone may be to have an account. Watching the Droplets tab needs none. */
export const MIN_AGE = 13;

/** How long a sent file keeps its bytes. Keep it equal to the API's ATTACHMENT_RETENTION_DAYS (default 30). */
export const ATTACHMENT_RETENTION_DAYS = 30;

/** The license the droplets are shared under (Terms, section 7). */
export const CONTENT_LICENSE = {
  name: 'CC BY-NC-SA 4.0',
  title: 'Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International',
  url: 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
} as const;
