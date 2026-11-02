"""The song styles a droplet rotates through when the learner asks for none.

Left to choose, the model kept landing on the same loud genres. Each is a vocals-first sound a high school
listener plays for fun, from 92 to 112 bpm, with no artist named. Until round 17 most had rapped verses, for their
clear diction; with a lesson's lines in them, the verses came back recited, like sentences read aloud, and the
rotation went to sung verses. One rapped style is back (Oct 31): the owner's favourite song, Cash in the Carrier
(docs/reference-songs.md), whose rapped verses carry the lesson well. Whether rapped lines come back recited is the
lyrics' to answer (the bundle brief), not the style's (the owner). Since Nov 1 three genres that do so rap one
section, as their songs do, the rest sung (the owner: "it doesn't mean the whole song is rap"). Since Nov 2 every
style but indie-pop raps its second verse and sings the rest, the chorus always: the owner liked the derivative's
Latin pop song with its rapped second verse ("it just sounds good") and wants at least one rapped verse in most
songs. A second verse, not a bridge, as every song has one and not every song has a bridge. The style a droplet gets
follows from its job's name, so a retry keeps it and neighbouring droplets differ.
"""

from __future__ import annotations

import zlib

CASH_IN_THE_CARRIER = ("Hip-hop pop, upbeat 100 bpm groove, confident female rap verses with a catchy sung chorus, "
                       "punchy 808s and bright synth hook, tight quantized drum programming with crisp kick-snare "
                       "hits and precise vocal layering, clear diction")

SPLIT_THE_WATER = ("bright pop with hip-hop verses, rapped verses, big sing-along chorus, clear female vocal, "
                   "shimmering synths, claps, 104 bpm, clear diction")

SONG_STYLES = (
    "bright teen pop with a melodic sung first verse and a rapped second verse, female lead vocal, stacked "
    "harmonies on a big sing-along chorus, finger snaps, glossy synths, punchy drums, 104 bpm",
    "K-pop with a melodic sung first verse traded between male group vocals, a rapped second verse, a big catchy "
    "chorus, punchy synth bass, crisp claps, 100 bpm",
    "laid-back R&B with a smooth sung first verse and a rapped second verse, warm electric piano, deep round "
    "bass, soft finger snaps, smooth chorus with stacked harmonies, 92 bpm",
    "upbeat pop with a bouncy sung first verse and a rapped second verse, catchy sung hook, female vocal, bouncy "
    "bass, claps, 98 bpm",
    "electropop with a sung first verse and a rapped second verse, pulsing synth bass, clear female vocal, "
    "shimmering arpeggios, euphoric sung chorus, tight claps, 108 bpm",
    "indie-pop with melodic sung verses, warm male vocal, strummed acoustic guitar, handclaps, gang vocals on "
    "the chorus, bright and breezy, 110 bpm",
    "Latin pop with a light dembow groove, a sung first verse and a rapped second verse, clear male vocal, plucked "
    "guitar, handclaps, catchy sung chorus, 96 bpm",
    "dance-pop with a four-on-the-floor beat, a short sung first verse and a rapped second verse, energetic "
    "female vocal, bright plucked synths, handclaps, sing-along chorus, 112 bpm",
    CASH_IN_THE_CARRIER,
)


# Styles the owner liked, kept by name too, so one can be asked for outright (`plan --style cash-in-the-carrier`
# writes it out as the learner's style). Each says clear diction itself, so the studio does not ask for it twice
# (audio.with_diction). docs/reference-songs.md has the lyric each was made for.
REFERENCE_STYLES = {"cash-in-the-carrier": CASH_IN_THE_CARRIER, "split-the-water": SPLIT_THE_WATER}


def style_for(job_name: str) -> str:
    return SONG_STYLES[zlib.crc32(job_name.encode("utf-8")) % len(SONG_STYLES)]


def named(style: str) -> str:
    """A reference style by its name (REFERENCE_STYLES), written out; any other style as it was given."""
    return REFERENCE_STYLES.get(style.strip().lower(), style)
