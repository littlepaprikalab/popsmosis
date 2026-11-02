"""Step 5: audio. The song, then the narration read aloud.

    audio/song.mp3                the song: Eleven Music, one composition-plan chunk per
                                  section, or Lyria on the Gemini API, from one prompt
    audio/song.plan.json          what was asked for, which lines each section sings, who made it
    audio/narration.mp3           text-to-speech, the narration sentences in order
    audio/narration.chars.json    what was read, where each line sits in it, who read it, and
                                  from ElevenLabs the character timings it returned (step 6
                                  turns them into words without an aligner; a Gemini
                                  narration has none, and step 6 aligns it)

Who makes the audio is an admin's setting on /admin/studio, as the claim said (the job's
settings.json, song_provider()): elevenlabs (the default) or gemini. It picks the song's singer
and the narration's voice together, so one switch moves a droplet off a service whose quota has
run out: ElevenLabs sings with Eleven Music and reads with its text-to-speech; Gemini sings with
Lyria and reads with Gemini 3.8 Flash TTS (gemini_tts_model()). The singer takes the
time the lines need: Eleven Music makes each section a chunk of the length asked for, so
each is asked for as long as its lines take to sing (line_ms), and step 6 looks for each
section's lines where it was planned. Lyria is given the style and the lyrics and no
length or timings at all, so its plan says the sections are not pinned and step 6 looks
for its lines across the whole song, leaving out any its own text of the song says it did
not sing. A song Lyria will not sing (it blocks the prompt, or
stops for safety: it blocked the Holocaust's lyrics) is made by Eleven Music instead, and
its plan says so. A narration already made stays, whoever read it: switching the setting makes
only the song again.

While STUDIO_VIDEOS is `lesson` (studio/media.py, lesson_only()) there is no song: only
the narration is made, and only its key is needed.

The two are asked for at the same time (the song takes longest), after the keys and the
speech's length are checked. One that fails fails the step once the others are saved, so
a retry asks only for what is missing. Every call costs credits, so a file that is already
there is never asked for again, unless the bundle's lines have changed since it was made,
or for the song, who sings it (its .json says what it was made from and by whom). To redo one by hand, delete its
.mp3. A busy or failing server (429, 5xx) is retried three times; a timeout is not, since
the server may still be making it. Needs ELEVENLABS_API_KEY for ElevenLabs, GEMINI_API_KEY for
Gemini; ELEVENLABS_VOICE_ID and GEMINI_TTS_VOICE pick another voice and GEMINI_SONG_MODEL another
Lyria model, on /admin/studio or in the environment (studio/knobs.py).

What it spent. Each call that comes back made is logged to calls.jsonl (studio/ledger.py),
with its model, its time and its price: a Lyria song at its model's price per song; a Gemini
narration at its tokens' price (gemini_speech_cost()); an ElevenLabs call in the credits its
character-cost header says, in dollars too when ELEVENLABS_USD_PER_1K_CREDITS gives the plan's
price of 1,000 credits.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from studio import brain, checks, jobs, knobs, ledger, media
from studio.api import USER_AGENT
from studio.schemas import Bundle

ELEVENLABS_API = "https://api.elevenlabs.io"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
SONG_PROVIDERS = ("elevenlabs", "gemini")
MUSIC_MODEL = "music_v2_5"
GEMINI_SONG_MODEL = "lyria-3.5"  # Lyria's full-length songs, sung from lyrics given in the prompt
SONG_BITRATE = "192k"  # a song Lyria sends as anything but an MP3 is encoded at this rate
SPEECH_BITRATE = "128k"  # a Gemini narration, as ElevenLabs' mp3_44100_128
MP3_TYPES = ("audio/mpeg", "audio/mp3")  # kept as sent, not encoded again
TTS_MODEL = "eleven_multilingual_v2"
TTS_MAX_CHARS = 10000  # that model's limit per request; 3 minutes of narration is about 3,000
DEFAULT_VOICE = "Xb7hH8MSUJpSbSDYk0k2"  # Alice: a clear, engaging British educator
GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"  # on the Interactions API (ai.google.dev/gemini-api/docs/speech-generation)
DEFAULT_GEMINI_VOICE = "Sadaltager"  # one of Gemini's prebuilt voices: "Knowledgeable"
# How the narration is read: Gemini reads the text as written and takes its delivery from a style kept apart from it.
GEMINI_TTS_STYLE = "a warm, clear teacher explaining to a student, at an easy, steady pace"
# USD per million tokens, text in and audio out, from each date (Gemini API pricing); 25 audio tokens a second.
GEMINI_TTS_PRICES = ((datetime.date(2027, 1, 1), 1.00, 18.00), (datetime.date.min, 0.50, 9.00))
AUDIO_TOKENS_PER_S = 25
# How long a line takes to sing: its syllables at a singing pace, and a rest. Round 17's Lyria, told no length,
# sang lines of about 8 syllables at 4.1 a second and took about 3 s a line with the rests between them; held to one
# bar a line (2.2 s at 110 bpm), every line came out at the speed of speech, sung or rapped.
SUNG_SYLLABLES_PER_S = 4.0
LINE_REST_S = 1.0
MIN_CHUNK_MS, MAX_CHUNK_MS = 3000, 120000  # what Eleven Music accepts per chunk
TIMEOUT_S = 600  # a song takes a minute or two to come back
SPEECH = ("narration",)
# USD per song, by Lyria model (Gemini API pricing). A model not here is logged with no price.
GEMINI_SONG_PRICES = {"lyria-3.5": 0.08, "lyria-3-clip-preview": 0.04}
ELEVENLABS_PRICE_VAR = "ELEVENLABS_USD_PER_1K_CREDITS"

# send(method, url, headers, body) -> (status, response headers, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class NoAnswer(media.StepFailed):
    """The request never reached the server, or the connection dropped: safe to send again."""


class Blocked(media.StepFailed):
    """Gemini will not sing what the song says: its prompt was blocked, or its answer stopped for safety."""


# Why a Gemini answer stops with no song when what it would sing is refused. RECITATION (too close to a known song)
# is not here: the song fails as before.
BLOCKED_FINISH_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)
    host = urllib.parse.urlsplit(url).hostname
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except TimeoutError as e:
        # Not retried: the server may still be making, and charging for, what was asked.
        raise media.StepFailed(f"{host} took over {TIMEOUT_S}s to answer") from e
    except (urllib.error.URLError, OSError) as e:
        raise NoAnswer(f"{host} did not answer: {getattr(e, 'reason', e)}") from e


send: Send = _urllib_send
RETRY_STATUSES = {429, 500, 502, 503, 504}  # busy (too many songs at once) or a passing server fault
RETRY_WAITS_S = (5, 20, 60)
sleep = time.sleep  # tests pass their own


def plan_path(kind: str) -> str:
    """What an audio file was made from: audio/song.plan.json, audio/<speech>.chars.json."""
    return f"audio/{kind}.plan.json" if kind == "song" else f"audio/{kind}.chars.json"


def run(job: Path, ask=brain.ask) -> str:
    bundle = jobs.read(job / "bundle.json", Bundle)
    todo = [kind for kind in media.timed(job) if not _current(job, bundle, kind)]
    provider = song_provider(job)
    if "song" in todo and provider not in SONG_PROVIDERS:
        raise media.StepFailed(f"the song's provider {provider!r} is not one this studio has "
                               f"({', '.join(SONG_PROVIDERS)}): update the studio")
    _check_keys(todo, provider)  # before anything is spent: the song is paid for even if the narration then fails
    for kind in todo:  # likewise what a voice call would refuse, since the song is asked for at the same time
        if kind != "song" and len(join_lines(speech_lines(bundle, kind))[0]) > TTS_MAX_CHARS:
            raise media.StepFailed(f"the {kind} is {len(join_lines(speech_lines(bundle, kind))[0])} characters; "
                                   f"one text-to-speech call takes {TTS_MAX_CHARS}")
    (job / "audio").mkdir(exist_ok=True)

    def make(kind: str) -> str:
        if kind == "song":
            return f"song ({_make_song(job, bundle, provider)})"
        _make_speech(job, kind, speech_lines(bundle, kind), provider)
        return f"{kind} ({provider})"

    # Both at once: the song takes longest, and the voice is made while it is. One that
    # fails fails the step after the others are saved, so a retry makes only what is missing.
    made = ledger.parallel(make, todo, len(todo))
    lengths = ", ".join(f"{kind} {duration(job / media.audio_path(kind)):.1f}s" for kind in media.timed(job))
    return f"made {', '.join(made) or 'nothing (all cached)'}; {lengths}"


def is_done(job: Path) -> bool:
    try:
        bundle = jobs.read(job / "bundle.json", Bundle)
    except (OSError, ValueError):
        return False
    return all(_current(job, bundle, kind) for kind in media.timed(job))


def _current(job: Path, bundle: Bundle, kind: str) -> bool:
    """The audio file is there and was made from the bundle's lines as they now stand."""
    if not (job / media.audio_path(kind)).exists():
        return False
    try:
        made_from = json.loads((job / plan_path(kind)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # step 6 needs what it was made from, so it is made again
    # Who sings was switched since: the one now chosen makes it again. A song Eleven Music made because Lyria
    # would not sing it stands for either.
    if kind == "song" and song_provider(job) not in (made_from.get("provider", "elevenlabs"),
                                                     (made_from.get("instead_of") or {}).get("provider")):
        return False
    wanted = song_lines(bundle) if kind == "song" else speech_lines(bundle, kind)
    return [[i, t] for i, t in made_from.get("source", [])] == [[i, t] for i, t in wanted]


def song_provider(job: Path) -> str:
    """Who makes the audio, the song and the narration: the admin's setting on /admin/studio as the job's last
    claim said, else elevenlabs."""
    song = jobs.studio_settings(job).get("song")
    return (song.get("provider") if isinstance(song, dict) else None) or "elevenlabs"


def _check_keys(todo: list[str], provider: str) -> None:
    needed = []
    if todo and provider == "elevenlabs":
        needed.append("ELEVENLABS_API_KEY")
    if todo and provider == "gemini":
        needed.append("GEMINI_API_KEY")
    missing = [name for name in needed if not os.environ.get(name)]
    if missing:
        raise media.StepFailed(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set")


# --- the song --------------------------------------------------------------------------


def song_lines(bundle: Bundle) -> list[tuple[str, str]]:
    return [(line.id, line.text) for line in bundle.lyrics]


def sections(bundle: Bundle) -> list[dict]:
    """The lyrics in sung order, one entry per section: its name, its lines and their ids."""
    out: list[dict] = []
    for line in bundle.lyrics:
        if not out or out[-1]["section"] != line.section:
            out.append({"section": line.section, "line_ids": [], "lines": []})
        out[-1]["line_ids"].append(line.id)
        out[-1]["lines"].append(line.text)
    return out


def line_ms(text: str) -> float:
    """How long a line takes to sing: its syllables at SUNG_SYLLABLES_PER_S, and LINE_REST_S. An 8-syllable
    line takes 3 s, whatever the tempo."""
    return (checks.syllables(text) / SUNG_SYLLABLES_PER_S + LINE_REST_S) * 1000


def planned_sections(bundle: Bundle) -> list[dict]:
    """Each section in sung order with its lines, and where it is planned to fall: as long as its lines take
    to sing (line_ms)."""
    out, at = [], 0
    for part in sections(bundle):
        length = min(MAX_CHUNK_MS, max(MIN_CHUNK_MS, round(sum(line_ms(text) for text in part["lines"]))))
        out.append({**part, "start_ms": at, "duration_ms": length})
        at += length
    return out


def _placed(planned: list[dict]) -> list[dict]:
    """What the song's plan file keeps of each section, for step 6."""
    return [{k: part[k] for k in ("section", "line_ids", "start_ms", "duration_ms")} for part in planned]


def _make_song(job: Path, bundle: Bundle, provider: str) -> str:
    """Make the song; say who made it."""
    ledger.check_ceiling(job)
    if provider != "gemini":
        _make_eleven_song(job, bundle)
        return "elevenlabs"
    try:
        _make_gemini_song(job, bundle)
        return "gemini"
    except Blocked as e:
        # Lyria refuses some grave topics' lyrics outright: "Why the Holocaust happened" was blocked twice
        # (blockReason OTHER) on Oct 30, where the same style without the lyrics was made. Eleven Music sang them.
        print(f"audio: {e}; Eleven Music makes the song instead", file=sys.stderr)
        ledger.check_ceiling(job)  # a second paid song: outside the try, so the ceiling keeps its own code
        try:
            _make_eleven_song(job, bundle, instead_of={"provider": "gemini", "model_id": gemini_song_model(),
                                                       "reason": str(e)})
        except media.StepFailed as again:
            raise media.StepFailed(f"{e}, and Eleven Music could not make it instead: {again}") from again
        return f"elevenlabs, as {e}"


# Eleven Music: one chunk per section, each exactly as long as asked, and each asked for the song's own style and
# avoid list as the bundle writes them, with only the diction clause and the studio's own things to avoid added
# (with_diction, with_avoid): how the verses and the chorus are sung is the style's to say ("rapped verses, rising
# into a big sing-along chorus"). Until Oct 29 code added "clear rapped verse" to the verses and "sung melodic vocal,
# not rapped" to the rest, after a song told one style for every section rapped throughout.

# Every song asks once for clear diction, the owner's rule since Oct 31: on Lyria, which is given no timings, songs
# came back with lines step 6 could not hear (30 of 47 in one supply-and-demand song, which was sung again). Once,
# and only where the style does not ask already: until round 17 every instruction asked for diction and none for
# music, and the songs came back recited.
DICTION = "clear diction, every word easy to hear"


def with_diction(styles: list[str]) -> list[str]:
    """The song's styles with DICTION added, unless one of them already asks for diction; none stay none."""
    if not styles or any("diction" in style.lower() for style in styles):
        return list(styles)
    return [*styles, DICTION]


# What every song avoids, asked by the studio itself as it asks for diction: the writer listed both in every bundle
# (Nov 1). Songs opened on long instrumental intros (fc8bae6), and no song is explicit, whoever it is for.
AVOID = ("long instrumental intro", "explicit lyrics")


def with_avoid(avoid: list[str], styles: list[str] | None = None) -> list[str]:
    """The song's avoid list with AVOID added where it lacks them, and nothing about diction, which the style asks
    for once (with_diction): asked again and again, a singer sings like someone reading. Nothing about rap either when
    the style raps a section: writers listed "rap" to avoid under styles that sang every verse (Nov 2), and most
    styles now rap their second verse (studio/styles.py)."""
    raps = any(checks.RAPPED.search(style) for style in styles or [])
    kept = [a for a in avoid if "diction" not in a.lower() and not (raps and checks.RAPPED.search(a))]
    have = {" ".join(a.lower().split()) for a in kept}
    return [*kept, *(a for a in AVOID if a not in have)]


def composition_plan(bundle: Bundle) -> tuple[dict, list[dict]]:
    """The music request's plan, one chunk per section, and where each section is planned to fall."""
    planned = planned_sections(bundle)
    chunks = [{
        "text": f"[{part['section'].title()}]\n" + "\n".join(part["lines"]),
        "duration_ms": part["duration_ms"],
        "positive_styles": with_diction(bundle.music.positive_styles),
        "negative_styles": with_avoid(bundle.music.negative_styles, bundle.music.positive_styles),
        "context_adherence": "high",
    } for part in planned]
    return {"chunks": chunks}, _placed(planned)


def _make_eleven_song(job: Path, bundle: Bundle, instead_of: dict | None = None) -> None:
    """instead_of: the provider chosen, which would not sing the song, and why; kept in the plan."""
    plan, placed = composition_plan(bundle)
    body = {"model_id": MUSIC_MODEL, "composition_plan": plan}
    started = time.monotonic()
    status, headers, audio = _eleven("POST", "/v1/music?output_format=mp3_44100_128", body)
    if status == 200:
        _log_eleven(job, "song", MUSIC_MODEL, headers, started)
    if status != 200 or not audio:
        raise media.StepFailed(f"Eleven Music refused the song: {status} {_error(audio)}")
    # The plan first, then the audio: the audio file is what says the song is made.
    jobs.write(job / plan_path("song"), {
        "source": song_lines(bundle), "provider": "elevenlabs", "model_id": MUSIC_MODEL,
        "sections": placed, "sections_pinned": True,
        "song_id": _header(headers, "song-id"), "headers": _kept(headers), "request": body,
        **({"instead_of": instead_of} if instead_of else {}),
    })
    _write_bytes(job / media.audio_path("song"), audio)


# Lyria on the Gemini API: the whole song from one prompt, which it follows only roughly.

def gemini_song_model() -> str:
    return knobs.env("GEMINI_SONG_MODEL").strip() or GEMINI_SONG_MODEL


def song_prompt(bundle: Bundle) -> tuple[str, list[dict]]:
    """Lyria's prompt: the style, what to avoid and the lyrics, section by section (its instructions apart from
    its lyrics, as Google advises), and where each section is planned to fall. No length and no timings: the
    singer takes the time the lines need, and step 6 finds them. Until round 17 it also asked for a length, a
    timed structure, every word exactly as written with no ad-libs or repeats, and clear upfront vocals; every
    instruction asked for diction and none for music, and the songs came back recited."""
    planned = planned_sections(bundle)
    music = bundle.music
    head = []
    if music.positive_styles:  # a free-form style may end on its own full stop
        head.append(f"Style: {', '.join(style.rstrip(' .') for style in with_diction(music.positive_styles))}.")
    head.append(f"Avoid: {', '.join(style.rstrip(' .') for style in with_avoid(music.negative_styles, music.positive_styles))}.")
    head.append("Sing the lines in order, section by section. Backing vocals and echoes of the hook are welcome.")
    lyrics = [f"[{p['section'].title()}]\n" + "\n".join(p["lines"]) for p in planned]
    return "\n".join(head) + "\n\nLyrics:\n" + "\n\n".join(lyrics), _placed(planned)


def _make_gemini_song(job: Path, bundle: Bundle) -> None:
    prompt, placed = song_prompt(bundle)
    model = gemini_song_model()
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO", "TEXT"]}}
    started = time.monotonic()
    status, headers, answer = _gemini(f"/models/{urllib.parse.quote(model, safe='')}:generateContent", body)
    if status != 200:
        raise media.StepFailed(f"Gemini refused the song: {status} {_error(answer)}")
    audio, mime_type, said = read_gemini_song(answer)
    _log(job, "song", model, started, GEMINI_SONG_PRICES.get(model))  # a song came back: one song's price
    audio = as_mp3(audio, mime_type)
    # The plan first, then the audio: the audio file is what says the song is made.
    jobs.write(job / plan_path("song"), {
        "source": song_lines(bundle), "provider": "gemini", "model_id": model,
        "sections": placed, "sections_pinned": False, "mime_type": mime_type, **said,
        "headers": _kept(headers), "request": body,
    })
    _write_bytes(job / media.audio_path("song"), audio)


def read_gemini_song(answer: bytes) -> tuple[bytes, str, dict]:
    """The song out of a generateContent answer: its audio, the audio's type, and what else came
    back (the lyrics or structure Lyria wrote, why it stopped, its version, the usage)."""
    try:
        data = json.loads(answer)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        raise media.StepFailed(f"Gemini sent back something unreadable for the song: {e}") from e
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise Blocked(f"Gemini blocked the song's prompt: {blocked}")
    candidate = (data.get("candidates") or [{}])[0] or {}
    parts = (candidate.get("content") or {}).get("parts") or []
    sounds = [part.get("inlineData") or part.get("inline_data") for part in parts if isinstance(part, dict)]
    sounds = [s for s in sounds if isinstance(s, dict) and _mime(s).lower().startswith("audio/")]
    if not sounds:
        reason = candidate.get("finishReason")
        error = Blocked if reason in BLOCKED_FINISH_REASONS else media.StepFailed
        raise error(f"Gemini sent back no song (finish reason: {reason or 'none given'})")
    try:
        audio = base64.b64decode(sounds[-1]["data"], validate=True)  # the last, as the SDKs' output_audio
    except (KeyError, ValueError, TypeError) as e:
        raise media.StepFailed(f"Gemini's song is not readable audio data: {e}") from e
    if not audio:
        raise media.StepFailed("Gemini sent back an empty song")
    text = "\n".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
    return audio, _mime(sounds[-1]), {
        "text": text, "finish_reason": candidate.get("finishReason"), "model_version": data.get("modelVersion"),
        "response_id": data.get("responseId"), "usage": data.get("usageMetadata"),
    }


def _mime(inline: dict) -> str:
    return str(inline.get("mimeType") or inline.get("mime_type") or "")


def as_mp3(audio: bytes, mime_type: str, kind: str = "song") -> bytes:
    """Gemini's song or narration as an MP3 with a length header: an MP3 as it was sent (Lyria's), anything else
    (a WAV) encoded once.

    Every later step reads an audio file's length from its header (ffprobe), and a
    variable-bitrate MP3 without one reads as the wrong length; the upload calls it
    audio/mpeg. An MP3 is copied, not encoded again: each lossy encode costs some of
    the sound, and the video's AAC is one more (the owner, Nov 2). A copy from a file
    to a file writes the length header all the same. The song's peaks are left as
    sent: the render holds its mix under full scale (media.PEAK_LIMIT), and the MP3
    is played only inside a video.
    """
    with tempfile.TemporaryDirectory(prefix=f"popsmosis-{kind}-") as tmp:
        source, out = Path(tmp) / "gemini", Path(tmp) / f"{kind}.mp3"
        source.write_bytes(audio)
        try:
            kept = (["-codec:a", "copy"] if mime_type.split(";")[0].strip().lower() in MP3_TYPES else
                    ["-codec:a", "libmp3lame", "-b:a", SONG_BITRATE if kind == "song" else SPEECH_BITRATE])
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-vn", *kept, str(out)],
                           capture_output=True, check=True, timeout=300)
            return out.read_bytes()
        except subprocess.CalledProcessError as e:
            detail = e.stderr.decode("utf-8", "replace").strip()[:200]
        except (OSError, subprocess.TimeoutExpired) as e:
            detail = str(e)
    raise media.StepFailed(f"cannot keep Gemini's {kind} ({mime_type or 'no type given'}) as an MP3: {detail}")


# --- the narration -------------------------------------------------------------------------


def speech_lines(bundle: Bundle, kind: str) -> list[tuple[str, str]]:
    """What a speech file reads aloud: (line id, text), in order. The narration is the only one."""
    if kind != "narration":
        raise ValueError(f"no speech called {kind!r}")
    return [(n.id, n.text) for n in bundle.narration]


def voice_id() -> str:
    return knobs.env("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE


def gemini_voice() -> str:
    return knobs.env("GEMINI_TTS_VOICE").strip() or DEFAULT_GEMINI_VOICE


def _make_speech(job: Path, kind: str, lines: list[tuple[str, str]], provider: str = "elevenlabs") -> None:
    text, spans = join_lines(lines)
    if len(text) > TTS_MAX_CHARS:
        raise media.StepFailed(f"the {kind} is {len(text)} characters; one text-to-speech call takes {TTS_MAX_CHARS}")
    ledger.check_ceiling(job)
    if provider == "gemini":
        _make_gemini_speech(job, kind, lines, text, spans)
    else:
        _make_eleven_speech(job, kind, lines, text, spans)


def _make_eleven_speech(job: Path, kind: str, lines: list[tuple[str, str]], text: str, spans: list[dict]) -> None:
    voice = voice_id()
    started = time.monotonic()
    status, headers, answer = _eleven("POST", f"/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
                                      {"text": text, "model_id": TTS_MODEL})
    if status == 200:
        _log_eleven(job, kind, TTS_MODEL, headers, started)
    if status != 200:
        raise media.StepFailed(f"text-to-speech refused the {kind}: {status} {_error(answer)}")
    try:
        data = json.loads(answer)
        audio = base64.b64decode(data["audio_base64"])
        alignment = data["alignment"]
        if not {"characters", "character_start_times_seconds", "character_end_times_seconds"} <= set(alignment):
            raise KeyError("character timings")
    except (ValueError, KeyError, TypeError) as e:
        raise media.StepFailed(f"text-to-speech sent back something unreadable for the {kind}: {e}") from e
    # The timings first, then the audio: the audio file is what says this kind is made.
    jobs.write(job / plan_path(kind), {
        "source": lines, "text": text, "lines": spans, "provider": "elevenlabs", "voice_id": voice,
        "model_id": TTS_MODEL, "alignment": alignment, "headers": _kept(headers),
    })
    _write_bytes(job / media.audio_path(kind), audio)


# Gemini's text-to-speech: the whole narration in one call, as ElevenLabs reads it, but with no timings: step 6
# finds each word in it with the aligner it runs on songs.

def _make_gemini_speech(job: Path, kind: str, lines: list[tuple[str, str]], text: str, spans: list[dict]) -> None:
    voice, model = gemini_voice(), GEMINI_TTS_MODEL
    body = {
        "model": model,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": GEMINI_TTS_STYLE}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
        "store": False,  # nothing to come back to: Google keeps no copy of the interaction
    }
    started = time.monotonic()
    status, headers, answer = _gemini("/interactions", body)
    if status != 200:
        raise media.StepFailed(f"Gemini's text-to-speech refused the {kind}: {status} {_error(answer)}")
    audio, mime_type, said = read_gemini_speech(answer, kind)
    audio = as_mp3(audio, mime_type, kind)
    _log(job, kind, model, started, gemini_speech_cost(said.get("usage"), duration_of(audio)))
    # The plan first, then the audio: the audio file is what says this kind is made.
    jobs.write(job / plan_path(kind), {
        "source": lines, "text": text, "lines": spans, "provider": "gemini", "voice": voice, "model_id": model,
        "mime_type": mime_type, **said, "headers": _kept(headers),
    })
    _write_bytes(job / media.audio_path(kind), audio)


def read_gemini_speech(answer: bytes, kind: str = "narration") -> tuple[bytes, str, dict]:
    """The speech out of an Interactions API answer: its audio (the last audio the model's steps hold), the audio's
    type (a WAV unless asked otherwise), and its id, status and usage."""
    try:
        data = json.loads(answer)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        raise media.StepFailed(f"Gemini sent back something unreadable for the {kind}: {e}") from e
    status = data.get("status")
    if status not in (None, "completed"):
        raise media.StepFailed(f"Gemini's text-to-speech did not finish the {kind} (status: {status})")
    sounds = [item for step in data.get("steps") or [] if isinstance(step, dict) and step.get("type") == "model_output"
              for item in step.get("content") or [] if isinstance(item, dict) and item.get("type") == "audio"]
    if not sounds:
        raise media.StepFailed(f"Gemini sent back no {kind}")
    try:
        audio = base64.b64decode(sounds[-1]["data"], validate=True)
    except (KeyError, ValueError, TypeError) as e:
        raise media.StepFailed(f"Gemini's {kind} is not readable audio data: {e}") from e
    if not audio:
        raise media.StepFailed(f"Gemini sent back an empty {kind}")
    return audio, _mime(sounds[-1]) or "audio/wav", {
        "interaction_id": data.get("id"), "status": status, "usage": data.get("usage"),
    }


def gemini_speech_cost(usage: dict | None, seconds: float | None, today: datetime.date | None = None) -> float | None:
    """What a Gemini narration cost: its text in and its audio out at the prices of the day (GEMINI_TTS_PRICES).
    The audio out is the answer's own count, else its length at 25 tokens a second; None when neither is known."""
    usage = usage if isinstance(usage, dict) else {}

    def count(key: str) -> float | None:
        value = usage.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    tokens_out = count("total_output_tokens")
    if tokens_out is None and seconds is not None:
        tokens_out = seconds * AUDIO_TOKENS_PER_S
    if tokens_out is None:
        return None
    day = today or datetime.date.today()
    _, price_in, price_out = next(price for price in GEMINI_TTS_PRICES if day >= price[0])
    return round(((count("total_input_tokens") or 0) * price_in + tokens_out * price_out) / 1_000_000, 6)


def join_lines(lines: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """The lines read as one text, and each line's [start, end) in it, in characters."""
    text, spans = "", []
    for line_id, line in lines:
        if text:
            text += " "
        spans.append({"id": line_id, "start": len(text), "end": len(text) + len(line)})
        text += line
    return text, spans


# --- what it spent ----------------------------------------------------------------------------


def _log(job: Path, label: str, model: str, started: float, cost_usd: float | None,
         credits: float | None = None) -> None:
    """One call that came back made, as the job's calls.jsonl keeps it (studio/ledger.py)."""
    ledger.log(job, [brain.Call(label, "audio", model, 0, 0, cost_usd, round(time.monotonic() - started, 1),
                                "", credits=credits)])


def _log_eleven(job: Path, label: str, model: str, headers: dict, started: float) -> None:
    credits = eleven_credits(headers)
    rate = eleven_usd_per_1k_credits()
    cost = round(credits * rate / 1000, 6) if credits is not None and rate is not None else None
    _log(job, label, model, started, cost, credits)


def eleven_credits(headers: dict) -> float | None:
    """The credits ElevenLabs charged for a call, from its character-cost header; None when it does not
    say, or says something that is not a count ("nan" would make the bill's JSON one the API refuses)."""
    try:
        credits = float(_header(headers, "character-cost"))
    except ValueError:
        return None
    return credits if 0 <= credits < float("inf") else None


def eleven_usd_per_1k_credits() -> float | None:
    """What 1,000 ElevenLabs credits cost on this account's plan (ELEVENLABS_USD_PER_1K_CREDITS), or None."""
    raw = knobs.env(ELEVENLABS_PRICE_VAR).strip()
    if not raw:
        return None
    try:
        rate = float(raw)
    except ValueError:
        rate = float("nan")
    if not 0 <= rate < float("inf"):  # NaN, negative or infinite
        print(f"audio: {ELEVENLABS_PRICE_VAR}={raw!r} is not a price; the ElevenLabs calls are logged in credits only",
              file=sys.stderr)
        return None
    return rate


# --- helpers ---------------------------------------------------------------------------------


def _eleven(method: str, path: str, body: dict) -> tuple[int, dict, bytes]:
    return _call(method, ELEVENLABS_API + path, {"xi-api-key": _key("ELEVENLABS_API_KEY")}, body)


def _gemini(path: str, body: dict) -> tuple[int, dict, bytes]:
    return _call("POST", GEMINI_API + path, {"x-goog-api-key": _key("GEMINI_API_KEY")}, body)


def _key(name: str) -> str:
    key = os.environ.get(name)
    if not key:
        raise media.StepFailed(f"{name} is not set")
    return key


def _call(method: str, url: str, headers: dict, body: dict) -> tuple[int, dict, bytes]:
    headers = {**headers, "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for wait in (*RETRY_WAITS_S, None):
        try:
            status, answer_headers, answer = send(method, url, headers, data)
        except NoAnswer:
            if wait is None:
                raise
        else:
            if status not in RETRY_STATUSES or wait is None:
                return status, answer_headers, answer
            wait = max(wait, _retry_after(answer_headers, answer))
        sleep(wait)
    raise AssertionError("unreachable")


def _retry_after(headers: dict, body: bytes = b"") -> float:
    """How long the server asked to be left: its Retry-After header, or the retryDelay ("33s") the
    Gemini API puts in a quota error's body instead. At most 300 s."""
    try:
        asked = float(_header(headers, "retry-after") or 0)
    except ValueError:
        asked = 0.0
    try:
        details = json.loads(body).get("error", {}).get("details", [])
        delays = [d["retryDelay"] for d in details if isinstance(d, dict) and isinstance(d.get("retryDelay"), str)]
        asked = max([asked] + [float(delay.rstrip("s")) for delay in delays])
    except (ValueError, AttributeError, TypeError):
        pass
    return min(asked, 300.0)


def _header(headers: dict, name: str) -> str:
    return next((v for k, v in headers.items() if k.lower() == name), "")


def _kept(headers: dict) -> dict:
    """The response headers worth keeping: ids, costs and counts."""
    return {k.lower(): v for k, v in headers.items()
            if any(word in k.lower() for word in ("id", "cost", "count", "credit", "character"))}


def _error(body: bytes) -> str:
    """What a refusal says: ElevenLabs' "detail", or the Gemini API's error status and message."""
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        error = data["error"]
        return " ".join(str(error[k]) for k in ("status", "message") if error.get(k))[:300] or text[:300]
    detail = data.get("detail", text) if isinstance(data, dict) else text
    return json.dumps(detail)[:300] if not isinstance(detail, str) else detail[:300]


def _write_bytes(path: Path, data: bytes) -> None:
    partial = path.with_name(f".{path.name}.partial")
    partial.write_bytes(data)
    os.replace(partial, path)


def duration_of(audio: bytes) -> float | None:
    """The length of audio held in memory, or None when it cannot be read: only the bill needs it."""
    with tempfile.NamedTemporaryFile(prefix="popsmosis-audio-", suffix=".mp3") as f:
        f.write(audio)
        f.flush()
        try:
            return duration(Path(f.name))
        except media.StepFailed:
            return None


def duration(path: Path) -> float:
    """An audio file's length in seconds (ffprobe)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, check=True).stdout
        return float(out.strip())
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        raise media.StepFailed(f"cannot read the length of {path.name}: {e}") from e
