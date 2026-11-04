import { describe, it, expect } from 'vitest';
import { PLAY_EVENTS, countPlaySql, playEventOf, serializePlays, viewsOf } from './plays.js';

describe('playEventOf', () => {
  it('takes a video and one of the events', () => {
    expect(playEventOf({ video: 'song', event: 'started' })).toEqual({ video: 'song', event: 'started' });
    expect(playEventOf({ video: 'lesson', event: 'finished' })).toEqual({ video: 'lesson', event: 'finished' });
    expect(playEventOf({ video: 'hook', event: 'shared' })).toEqual({ video: 'hook', event: 'shared' });
  });

  it('refuses anything else, and a share or download of a video that is not the hook', () => {
    expect(playEventOf(null)).toMatch(/^video must be/);
    expect(playEventOf({ video: 'SONG', event: 'started' })).toMatch(/^video must be/);
    expect(playEventOf({ video: 'song', event: 'reached90' })).toMatch(/^event must be/);
    expect(playEventOf({ video: 'song', event: 'toString' })).toMatch(/^event must be/);
    expect(playEventOf({ video: 'lesson', event: 'downloaded' })).toBe('downloaded is counted for the hook clip only');
  });
});

describe('countPlaySql', () => {
  const sql = (event: (typeof PLAY_EVENTS)[number], video: 'song' | 'lesson' | 'hook' = 'song') => countPlaySql('a1', video, event);

  it('makes the row on a start, a share or a download, only for a READY droplet with that video', () => {
    for (const [event, video, url] of [['started', 'lesson', 'lessonVideoUrl'], ['shared', 'hook', 'hookVideoUrl'], ['downloaded', 'hook', 'hookVideoUrl']] as const) {
      const { text, values } = sql(event, video);
      expect(text).toMatch(/INSERT INTO droplet_plays/);
      expect(text).toContain(`"${url}" IS NOT NULL`);
      expect(text).toContain(`"videoStatus" = 'READY'`);
      expect(text).toContain(`SET "${event}" = droplet_plays."${event}" + 1`);
      expect(values).toEqual([video.toUpperCase(), 'a1']);
    }
  });

  it('adds a later step only to a row whose step before it is higher', () => {
    expect(sql('reached25').text).toMatch(/UPDATE droplet_plays SET "reached25" = "reached25" \+ 1[\s\S]*"reached25" < "started"/);
    expect(sql('reached50').text).toContain('"reached50" < "reached25"');
    expect(sql('reached75').text).toContain('"reached75" < "reached50"');
    expect(sql('finished').text).toContain('"finished" < "reached75"');
    expect(sql('finished').text).not.toMatch(/INSERT/);
  });
});

describe('serializePlays', () => {
  const zero = Object.fromEntries(PLAY_EVENTS.map((e) => [e, 0])) as Record<(typeof PLAY_EVENTS)[number], number>;

  it('gives the counts and the share of starts that finished', () => {
    expect(serializePlays({ ...zero, started: 8, reached25: 6, finished: 2 })).toEqual({ ...zero, started: 8, reached25: 6, finished: 2, finishRate: 0.25 });
  });

  it('has no finish rate before a start', () => {
    expect(serializePlays({ ...zero, shared: 3 }).finishRate).toBeNull();
  });
});

describe('viewsOf', () => {
  it("reads the song's and the lesson's starts, 0 for a video with no row", () => {
    expect(viewsOf([{ video: 'SONG', started: 7 }, { video: 'LESSON', started: 2 }])).toEqual({ song: 7, lesson: 2 });
    expect(viewsOf([{ video: 'LESSON', started: 2 }])).toEqual({ song: 0, lesson: 2 });
    expect(viewsOf([])).toEqual({ song: 0, lesson: 0 });
  });

  it('leaves out the hook, and anything that is not a count', () => {
    expect(viewsOf([{ video: 'HOOK', started: 50 }, { video: 'SONG', started: 'x' }, null])).toEqual({ song: 0, lesson: 0 });
    expect(viewsOf(null)).toEqual({ song: 0, lesson: 0 });
  });
});
