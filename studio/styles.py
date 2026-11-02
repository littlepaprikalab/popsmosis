"""The song styles a droplet rotates through when the learner asks for none.

Left to choose, the model kept landing on the same loud genres. Each is a vocals-first sound a high school
listener plays for fun, with no artist named, from 100 to 130 bpm. None is slower since Nov 2: rap at 92 and 96
sounded laid back, and the owner wants songs that perk people up; the K-pop (110) and the dance-pop (120) went faster
the same day, to spread the rotation's tempos (the owner: "some song can have higher bpms"). Until round 17 most had rapped verses, for their
clear diction; with a lesson's lines in them, the verses came back recited, like sentences read aloud, and the
rotation went to sung verses. One rapped style is back (Oct 31): the owner's favourite song, Cash in the Carrier
(docs/reference-songs.md), whose rapped verses carry the lesson well. Whether rapped lines come back recited is the
lyrics' to answer (the bundle brief), not the style's (the owner). Since Nov 1 three genres that do so rap one
section, as their songs do, the rest sung (the owner: "it doesn't mean the whole song is rap"). Since Nov 2 every
style raps both its verses, the chorus always sung: the owner liked the derivative's Latin pop song with its rapped
second verse ("it just sounds good"), then asked for two rapped verses in every song, as they "sound so much better
and perk me up" (the owner). The first style is the owner's own, from the Oct 27 brief's examples; it took teen
pop's place, which sounded the same once it rapped both verses.

Neighbouring droplets never share a style (the owner, Nov 2): three of five videos in one batch had drawn the same
teen pop from their names. A job takes the style none of the jobs made just before it took (style_for_job), and
keeps it in the job, so a retry keeps it too. Electropop and a bouncy upbeat pop were dropped the same day: one
sounded like teen pop, the other, once it rapped its verses, like Cash in the Carrier. The owner's style is near
Cash in the Carrier too (both 100 bpm, a woman rapping), with warm synths and snaps for its 808s and bright hook. The scorecard still seeds each topic by its
id (style_for), so its runs stay comparable.
"""

from __future__ import annotations

import fcntl
import json
import time
import zlib
from pathlib import Path

CASH_IN_THE_CARRIER = ("Hip-hop pop, upbeat 100 bpm groove, confident female rap verses with a catchy sung chorus, "
                       "punchy 808s and bright synth hook, tight quantized drum programming with crisp kick-snare "
                       "hits and precise vocal layering, clear diction")

SPLIT_THE_WATER = ("bright pop with hip-hop verses, rapped verses, big sing-along chorus, clear female vocal, "
                   "shimmering synths, claps, 104 bpm, clear diction")

SONG_STYLES = (
    "upbeat pop with hip-hop verses, rapped verses, catchy sung chorus, clear female vocal, warm synths, finger "
    "snaps, 100 bpm",
    "K-pop with rapped verses traded between male group vocals, a catchy sung pre-chorus, a big catchy sung "
    "chorus, punchy synth bass, crisp claps, 110 bpm",
    "laid-back R&B with smooth rapped verses, warm electric piano, deep round bass, soft finger snaps, smooth sung "
    "chorus with stacked harmonies, 100 bpm",
    "indie-pop with rapped verses, warm male vocal, strummed acoustic guitar, handclaps, stacked harmonies on the "
    "sung chorus, bright and breezy, 110 bpm",
    "Latin pop with a light dembow groove, rapped verses, clear male vocal, plucked guitar, handclaps, catchy sung "
    "chorus, 100 bpm",
    "dance-pop with a four-on-the-floor beat, rapped verses, energetic female vocal, bright plucked synths, "
    "handclaps, sing-along chorus, 120 bpm",
    CASH_IN_THE_CARRIER,
    "K-pop electro-pop with tight vocal comping; confident androgynous female lead, clipped catchy phrasing, rap-sung "
    "verses, soaring chorus, backing vocals on parenthetical echoes; stacked string synths, bright synth stabs, punchy "
    "electro beat, trap hi-hats in verses, four-on-the-floor chorus, half-time bridge with bass and claps; driving "
    "130 BPM feel, clear diction",
)


# Styles the owner liked, kept by name too, so one can be asked for outright (`plan --style cash-in-the-carrier`
# writes it out as the learner's style). Each says clear diction itself, so the studio does not ask for it twice
# (audio.with_diction). docs/reference-songs.md has the lyric each was made for.
REFERENCE_STYLES = {"cash-in-the-carrier": CASH_IN_THE_CARRIER, "split-the-water": SPLIT_THE_WATER}


def style_for(job_name: str) -> str:
    return SONG_STYLES[zlib.crc32(job_name.encode("utf-8")) % len(SONG_STYLES)]


CHOSEN = "style.json"  # the rotation's style a job took, and when


def style_for_job(job: Path) -> str:
    """The rotation's style for this job: the one it took before, else the first, from where its name points in the
    rotation (style_for), that none of the len(SONG_STYLES) - 1 jobs beside it to choose most recently took, so no
    style repeats within that many jobs. Kept in the job (CHOSEN); chosen under a lock, as a batch starts at once."""
    kept = job / CHOSEN
    try:
        return json.loads(kept.read_text(encoding="utf-8"))["style"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    job.parent.mkdir(parents=True, exist_ok=True)
    with open(job.parent / ".styles.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        taken = []
        for other in job.parent.iterdir():
            try:
                record = json.loads((other / CHOSEN).read_text(encoding="utf-8"))
                taken.append((float(record["at"]), record["style"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        recent = {style for _, style in sorted(taken, reverse=True)[:len(SONG_STYLES) - 1]}
        start = SONG_STYLES.index(style_for(job.name))
        order = SONG_STYLES[start:] + SONG_STYLES[:start]
        style = next(s for s in order if s not in recent)
        job.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps({"style": style, "at": time.time()}), encoding="utf-8")
    return style


def named(style: str) -> str:
    """A reference style by its name (REFERENCE_STYLES), written out; any other style as it was given."""
    return REFERENCE_STYLES.get(style.strip().lower(), style)
