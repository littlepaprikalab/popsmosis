"""The knobs an admin sets on /admin/studio over this machine's environment (api/src/lib/knobs.ts).

Each is an environment variable the studio reads: STUDIO_VIDEOS, ELEVENLABS_VOICE_ID, GEMINI_SONG_MODEL,
GEMINI_TTS_VOICE, ELEVENLABS_USD_PER_1K_CREDITS, STUDIO_CLAUDE_DRAWS (studio/steps/art.py), STUDIO_SESSION_FALLBACK
(studio/session.py) and STUDIO_STEP_MINUTES (studio/retry.py).
Set on the page, its value wins; cleared there, the variable is read again, else the code's default.

The page's values come with the studio's settings, as `knobs`, by name: from GET /api/studio/settings
before a run claims anything, from every claim after it (studio/jobs.py keeps each claim's with the
job, and puts its knobs in force), and from the job's settings.json for `step` and `make`, which read
no settings of their own. They hold for the rest of the process, as the brain in force does
(studio/brain.py); a detached `videos` reads the settings again itself.
"""

from __future__ import annotations

import os

_set: dict[str, str] = {}


def use(studio) -> None:
    """Put the knobs the studio's settings carry in force. Settings without them (an older API, a local
    job) leave what is in force."""
    knobs = studio.get("knobs") if isinstance(studio, dict) else None
    if not isinstance(knobs, dict):
        return
    _set.clear()
    _set.update({name: str(value) for name, value in knobs.items()
                 if isinstance(value, (str, int, float)) and not isinstance(value, bool)})


def env(name: str) -> str:
    """The knob's value: the page's, else this machine's environment's, else ""."""
    return _set[name] if name in _set else os.environ.get(name, "")


def named(name: str) -> str:
    """The knob as a message names it: where to change it."""
    return f"{name}, set on /admin/studio" if name in _set else name
