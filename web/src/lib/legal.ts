/**
 * The facts the Privacy Policy (/privacy), the Consumer Health Data Privacy Policy (/consumer-health-data) and the
 * Terms of Use (/terms) are built on, in one place. Change them here, and move LAST_UPDATED whenever a page changes
 * what it promises.
 */

/** Who runs Popsmosis: the "we" in both pages, and who people's data is entrusted to. */
export const OPERATOR_NAME = 'Little Paprika Lab';

/** Where the operator is based, as the pages say it. */
export const OPERATOR_LOCATION = 'Washington State, USA';

/**
 * Where people write about their data, takedowns, copyright notices, mistakes in a lesson and these terms: the
 * address Washington's My Health My Data Act requires for requests, and the DMCA agent's (Terms, section 12). Someone
 * has to read it: health data requests have 45 days, and a lawsuit's first step (Terms, section 17) arrives here.
 */
export const CONTACT_EMAIL = 'spicy.ginger.labs@gmail.com';

/** The law the Terms are governed by, as it reads after "the laws of". Empty reads as the place the operator is based. */
export const GOVERNING_LAW = 'the State of Washington, USA';

/** Where a dispute is heard, unless a consumer's own law lets them go to their local courts (Terms, section 15). */
export const COURTS = 'the state and federal courts located in King County, Washington';

export const LAST_UPDATED = '28 October 2026';

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
