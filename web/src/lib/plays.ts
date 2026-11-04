import { useState, type SyntheticEvent } from 'react';

/**
 * Play counts (api/src/routes/plays.ts): how far plays of each droplet video get, and how often a hook clip is
 * shared or downloaded, so the owner can see which droplets work for students (/admin/plays). The starts of the song
 * and the lesson are shown to everyone beside each, as its views (components/ViewCount.tsx, useViews).
 *
 * Nothing in a count says who sent it. It goes without the session cookie, so even a signed-in person's plays are
 * counted with no account; it carries no id of the browser, the device or the visit; and nothing is stored here.
 * A page view counts each step of each video once, however often it is played or rewound: the start, then 25%, 50%,
 * 75% and 95% ("finished") of it actually played, so a skip to the end counts as nothing played. A new page view
 * (components/PlayCountScope.tsx) counts afresh.
 *
 * Counting never gets in the way of watching: the handlers only read the player, nothing waits for an answer, and a
 * count that fails is dropped. The steps of one play go one after another, and a start the API did not count (over
 * a limit, offline) sends nothing more of that play, so every play counted is counted from its start. An API that
 * turns counting away for good (one older than the counts answers 404) hears no more from this page load: each
 * refusal would otherwise spend the visitor's share of its limits.
 */

export type PlayVideo = 'song' | 'lesson' | 'hook';
type PlayEvent = 'started' | 'reached25' | 'reached50' | 'reached75' | 'finished' | 'shared' | 'downloaded';

// The share of a video played that each step needs, in order.
const STEPS: [number, PlayEvent][] = [[0.25, 'reached25'], [0.5, 'reached50'], [0.75, 'reached75'], [0.95, 'finished']];

let counted = new Set<string>(); // "droplet|video|event", this page view
let plays = new Map<string, Promise<boolean>>(); // "droplet|video": this page view's play, true while it is being counted
let counting = true;
let refused = false; // until the page is loaded again, as after a deploy

/** A new page view: every video may be counted again. */
export function newPageView() {
  counted = new Set();
  plays = new Map();
}

/** Off for an admin, so the owner's own watching does not count as a student's. */
export function setCounting(on: boolean) {
  counting = on;
}

function once(play: string, event: PlayEvent): boolean {
  const key = `${play}|${event}`;
  if (!counting || counted.has(key)) return false;
  counted.add(key);
  return true;
}

// Not through lib/api.ts: that sends the session cookie and retries, and a count wants neither.
function send(droplet: string, video: PlayVideo, event: PlayEvent): Promise<boolean> {
  if (refused) return Promise.resolve(false);
  return fetch(`/api/droplets/${encodeURIComponent(droplet)}/plays`, {
    method: 'POST',
    credentials: 'omit',
    keepalive: true, // a step reached as the page closes still goes
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video, event }),
  }).then((res) => {
    // 429 is this play over a limit, and 5xx a moment's trouble: only the play stops. Any other refusal is the same
    // for every count.
    if (res.status >= 400 && res.status < 500 && res.status !== 429) refused = true;
    return res.ok;
  }, () => false);
}

/** How much of the video has played, 0 to 1: the parts played, each once, over its length. */
function playedShare(v: HTMLVideoElement): number {
  const length = v.duration;
  if (!Number.isFinite(length) || length <= 0) return 0;
  let played = 0;
  for (let i = 0; i < v.played.length; i++) played += v.played.end(i) - v.played.start(i);
  return played / length;
}

/**
 * A droplet video's counting, as props for its <video>. `onStart` is handed this page view's start of it as it is
 * sent: true once the API took it (useViews adds it to the views shown).
 */
export function countPlays(droplet: string, video: PlayVideo, onStart?: (counted: Promise<boolean>) => void) {
  const play = `${droplet}|${video}`;
  const progress = (e: SyntheticEvent<HTMLVideoElement>) => {
    let steps = plays.get(play);
    if (!steps || counted.has(`${play}|finished`)) return;
    const share = playedShare(e.currentTarget);
    for (const [at, event] of STEPS) {
      if (share < at) break;
      if (once(play, event)) steps = steps.then((ok) => ok && send(droplet, video, event));
    }
    plays.set(play, steps);
  };
  return {
    onPlaying: () => {
      if (!once(play, 'started')) return;
      const started = send(droplet, video, 'started');
      plays.set(play, started);
      onStart?.(started);
    },
    onTimeUpdate: progress,
    onEnded: progress,
  };
}

/** The hook tile's Share (once it went through) or Download. */
export function countClip(droplet: string, event: 'shared' | 'downloaded') {
  if (once(`${droplet}|hook`, event)) void send(droplet, 'hook', event);
}

/**
 * A video's views as the page shows them: the API's count, and this viewer's own play once the API took it, the way a
 * count moves when you press play. `views` is the API's number as the page last read it; the play is added to that
 * number as it stood when the play began, so a page that reads the count again after (the Learn page's polling) shows
 * the API's own and never counts the play twice. Nothing is added for a play that was not counted: an admin's, or
 * one turned away. Pass `onStart` to countPlays.
 */
export function useViews(views: number) {
  const [floor, setFloor] = useState(0);
  const onStart = (counted: Promise<boolean>) => {
    const before = views;
    void counted.then((ok) => { if (ok) setFloor(before + 1); });
  };
  return { views: Math.max(views, floor), onStart };
}
