"""The song styles a droplet rotates through when the learner asks for none.

Left to choose, the model kept landing on the same loud genres. Each style is a vocals-first sound a high school
listener plays for fun, naming no artist, from 100 to 130 bpm, and raps both its verses, the chorus sung: the owner's
choice (Nov 2), as rap "perks people up" and slower rap sounded laid back. Three are the owner's own: Cash in the
Carrier, their favourite song's (docs/reference-songs.md), an upbeat pop with hip-hop verses, and a K-pop electro-pop.

Neighbouring droplets never share a style: a job takes the first style, from where its name points in the rotation,
that none of the jobs made just before it took (style_for_job), and keeps it, so a retry keeps it too. The scorecard
still seeds each topic by its id (style_for), so its runs stay comparable.
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
