"""The song styles a droplet rotates through when the learner asks for none.

Left to choose, the model kept landing on the same loud genres. Each is a vocals-first sound a high school
listener plays for fun, with sung verses and a sung chorus, from 92 to 112 bpm, with no artist named. Until round 17
most had rapped verses, for their clear diction; with a lesson's lines in them, the verses came back recited, like
sentences read aloud. The style a droplet gets follows from its job's name, so a retry keeps it and neighbouring
droplets differ.
"""

from __future__ import annotations

import zlib

SONG_STYLES = (
    "bright teen pop with melodic sung verses, female lead vocal, stacked harmonies on a big sing-along chorus, "
    "finger snaps, glossy synths, punchy drums, 104 bpm",
    "K-pop with melodic sung verses traded between male group vocals, a big catchy chorus, punchy synth bass, "
    "crisp claps, 100 bpm",
    "laid-back R&B with smooth sung verses, warm electric piano, deep round bass, soft finger snaps, "
    "smooth chorus with stacked harmonies, 92 bpm",
    "upbeat pop with a bouncy sung verse melody, catchy sung hook, female vocal, bouncy bass, claps, 98 bpm",
    "electropop with sung verses, pulsing synth bass, clear female vocal, shimmering arpeggios, euphoric sung "
    "chorus, tight claps, 108 bpm",
    "indie-pop with melodic sung verses, warm male vocal, strummed acoustic guitar, handclaps, gang vocals on "
    "the chorus, bright and breezy, 110 bpm",
    "Latin pop with a light dembow groove, sung verses, clear male vocal, plucked guitar, handclaps, catchy sung "
    "chorus, 96 bpm",
    "dance-pop with a four-on-the-floor beat and short sung verses, energetic female vocal, bright plucked "
    "synths, handclaps, sing-along chorus, 112 bpm",
)


def style_for(job_name: str) -> str:
    return SONG_STYLES[zlib.crc32(job_name.encode("utf-8")) % len(SONG_STYLES)]
