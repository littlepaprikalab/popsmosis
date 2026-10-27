"""Step 5: audio. The song, then the narration and the exam question read aloud.

    audio/song.mp3                the song: Eleven Music, one composition-plan chunk per
                                  section, or Lyria on the Gemini API, from one prompt
    audio/song.plan.json          what was asked for, which lines each section sings, who made it
    audio/narration.mp3           text-to-speech, the narration sentences in order
    audio/narration.chars.json    what was read, where each line sits in it, and the
    audio/frq.mp3                 character timings the TTS returned (step 6 turns them
    audio/frq.chars.json          into words without an aligner)

SONG_PROVIDER picks who makes the song: elevenlabs (the default) or gemini. Eleven Music
makes each section a chunk of the length asked for, so step 6 looks for each section's
lines where it was planned. Lyria takes the whole song as one prompt and keeps to the
planned times only roughly, so its plan says the sections are not pinned and step 6 looks
for its lines across the whole song. The narration and the exam question are always
ElevenLabs text-to-speech, whose character timings step 6 turns into words.

Every call costs credits, so a file that is already there is never asked for again,
unless the bundle's lines have changed since it was made, or for the song, SONG_PROVIDER
since (its .json says what it was made from and by whom). To redo one by hand, delete its
.mp3. A busy or failing server (429, 5xx) is retried three times; a timeout is not, since
the server may still be making it. Needs ELEVENLABS_API_KEY, and GEMINI_API_KEY for a
Gemini song; ELEVENLABS_VOICE_ID picks another voice and GEMINI_SONG_MODEL another Lyria model.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from studio import brain, jobs, media
from studio.api import USER_AGENT
from studio.schemas import Bundle

ELEVENLABS_API = "https://api.elevenlabs.io"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
SONG_PROVIDERS = ("elevenlabs", "gemini")
MUSIC_MODEL = "music_v2_5"
GEMINI_SONG_MODEL = "lyria-3.5"  # Lyria's full-length songs, sung from lyrics given in the prompt
SONG_BITRATE = "192k"  # Lyria's song is kept as a constant-bitrate MP3 at this rate
TTS_MODEL = "eleven_multilingual_v2"
TTS_MAX_CHARS = 10000  # that model's limit per request; 3 minutes of narration is about 3,000
DEFAULT_VOICE = "XrExE9yKIg1WjnnlVkGX"  # Matilda: a clear, steady American educator
BEATS_PER_LINE = 8  # one sung line is two bars of 4/4 (PLAN section 5, step 5): 4.3 s at 112 bpm
MIN_CHUNK_MS, MAX_CHUNK_MS = 3000, 120000  # what Eleven Music accepts per chunk
TIMEOUT_S = 600  # a song takes a minute or two to come back
SPEECH = ("narration", "frq")

# send(method, url, headers, body) -> (status, response headers, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class NoAnswer(media.StepFailed):
    """The request never reached the server, or the connection dropped: safe to send again."""


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
    todo = [kind for kind in media.TIMED if not _current(job, bundle, kind)]
    provider = song_provider()
    if "song" in todo and provider not in SONG_PROVIDERS:
        raise media.StepFailed(f"SONG_PROVIDER must be {' or '.join(SONG_PROVIDERS)}, not {provider!r}")
    _check_keys(todo, provider)  # before anything is spent: the song is paid for even if the narration then fails
    made = []
    for kind in todo:
        (job / "audio").mkdir(exist_ok=True)
        if kind == "song":
            _make_song(job, bundle, provider)
            made.append(f"song ({provider})")
        else:
            _make_speech(job, kind, speech_lines(bundle, kind))
            made.append(kind)
    lengths = ", ".join(f"{kind} {duration(job / media.audio_path(kind)):.1f}s" for kind in media.TIMED)
    return f"made {', '.join(made) or 'nothing (all cached)'}; {lengths}"


def is_done(job: Path) -> bool:
    try:
        bundle = jobs.read(job / "bundle.json", Bundle)
    except (OSError, ValueError):
        return False
    return all(_current(job, bundle, kind) for kind in media.TIMED)


def _current(job: Path, bundle: Bundle, kind: str) -> bool:
    """The audio file is there and was made from the bundle's lines as they now stand."""
    if not (job / media.audio_path(kind)).exists():
        return False
    try:
        made_from = json.loads((job / plan_path(kind)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # step 6 needs what it was made from, so it is made again
    if kind == "song" and made_from.get("provider", "elevenlabs") != song_provider():
        return False  # SONG_PROVIDER was switched since: the one now chosen makes it again
    wanted = song_lines(bundle) if kind == "song" else speech_lines(bundle, kind)
    return [[i, t] for i, t in made_from.get("source", [])] == [[i, t] for i, t in wanted]


def song_provider() -> str:
    """Who makes the song: SONG_PROVIDER, elevenlabs when it is not set."""
    return os.environ.get("SONG_PROVIDER", "").strip().lower() or "elevenlabs"


def _check_keys(todo: list[str], provider: str) -> None:
    needed = []
    if any(kind != "song" or provider == "elevenlabs" for kind in todo):
        needed.append("ELEVENLABS_API_KEY")
    if "song" in todo and provider == "gemini":
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


def line_ms(bundle: Bundle) -> float:
    """How long one line takes to sing at the tempo the song's styles name."""
    return BEATS_PER_LINE * 60_000 / bundle.music.bpm


def planned_sections(bundle: Bundle) -> list[dict]:
    """Each section in sung order with its lines, and where it is planned to fall: two bars a
    line at the tempo the styles name."""
    out, at = [], 0
    for part in sections(bundle):
        length = min(MAX_CHUNK_MS, max(MIN_CHUNK_MS, round(line_ms(bundle) * len(part["lines"]))))
        out.append({**part, "start_ms": at, "duration_ms": length})
        at += length
    return out


def _placed(planned: list[dict]) -> list[dict]:
    """What the song's plan file keeps of each section, for step 6."""
    return [{k: part[k] for k in ("section", "line_ids", "start_ms", "duration_ms")} for part in planned]


def _make_song(job: Path, bundle: Bundle, provider: str) -> None:
    if provider == "gemini":
        _make_gemini_song(job, bundle)
    else:
        _make_eleven_song(job, bundle)


# Eleven Music: one chunk per section, each exactly as long as asked.

def composition_plan(bundle: Bundle) -> tuple[dict, list[dict]]:
    """The music request's plan, one chunk per section, and where each section is planned to fall."""
    planned = planned_sections(bundle)
    chunks = [{
        "text": f"[{part['section'].title()}]\n" + "\n".join(part["lines"]),
        "duration_ms": part["duration_ms"],
        "positive_styles": list(bundle.music.positive_styles),
        "negative_styles": list(bundle.music.negative_styles),
        "context_adherence": "high",
    } for part in planned]
    return {"chunks": chunks}, _placed(planned)


def _make_eleven_song(job: Path, bundle: Bundle) -> None:
    plan, placed = composition_plan(bundle)
    body = {"model_id": MUSIC_MODEL, "composition_plan": plan}
    status, headers, audio = _eleven("POST", "/v1/music?output_format=mp3_44100_128", body)
    if status != 200 or not audio:
        raise media.StepFailed(f"Eleven Music refused the song: {status} {_error(audio)}")
    # The plan first, then the audio: the audio file is what says the song is made.
    jobs.write(job / plan_path("song"), {
        "source": song_lines(bundle), "provider": "elevenlabs", "model_id": MUSIC_MODEL,
        "sections": placed, "sections_pinned": True,
        "song_id": _header(headers, "song-id"), "headers": _kept(headers), "request": body,
    })
    _write_bytes(job / media.audio_path("song"), audio)


# Lyria on the Gemini API: the whole song from one prompt, which it follows only roughly.

def gemini_song_model() -> str:
    return os.environ.get("GEMINI_SONG_MODEL", "").strip() or GEMINI_SONG_MODEL


def song_prompt(bundle: Bundle) -> tuple[str, list[dict]]:
    """Lyria's prompt: the style, the length, the planned structure and the lyrics to sing word
    for word (its instructions apart from its lyrics, as Google advises), and where each section
    is planned to fall."""
    planned = planned_sections(bundle)
    end_ms = planned[-1]["start_ms"] + planned[-1]["duration_ms"] if planned else 0
    music = bundle.music
    head = []
    if music.positive_styles:
        head.append(f"Style: {', '.join(music.positive_styles)}.")
    if music.negative_styles:
        head.append(f"Avoid: {', '.join(music.negative_styles)}.")
    head += [
        f"Length: about {_clock(end_ms)}. The singing starts within the first two seconds, with no long "
        "intro, and the song ends soon after the last line.",
        "Sing the lyrics below exactly as written, in order, section by section: no added lines, ad-libs or "
        "extra repeats, and no line left out. Clear, upfront vocals, so every word is easy to hear.",
    ]
    structure = [f"[{_clock(p['start_ms'])} - {_clock(p['start_ms'] + p['duration_ms'])}] {p['section'].title()}"
                 for p in planned]
    lyrics = [f"[{p['section'].title()}]\n" + "\n".join(p["lines"]) for p in planned]
    prompt = "\n".join(head) + "\n\nStructure:\n" + "\n".join(structure) + "\n\nLyrics:\n" + "\n\n".join(lyrics)
    return prompt, _placed(planned)


def _clock(ms: int) -> str:
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _make_gemini_song(job: Path, bundle: Bundle) -> None:
    prompt, placed = song_prompt(bundle)
    model = gemini_song_model()
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO", "TEXT"]}}
    status, headers, answer = _gemini(f"/models/{urllib.parse.quote(model, safe='')}:generateContent", body)
    if status != 200:
        raise media.StepFailed(f"Gemini refused the song: {status} {_error(answer)}")
    audio, mime_type, said = read_gemini_song(answer)
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
        raise media.StepFailed(f"Gemini blocked the song's prompt: {blocked}")
    candidate = (data.get("candidates") or [{}])[0] or {}
    parts = (candidate.get("content") or {}).get("parts") or []
    sounds = [part.get("inlineData") or part.get("inline_data") for part in parts if isinstance(part, dict)]
    sounds = [s for s in sounds if isinstance(s, dict) and _mime(s).lower().startswith("audio/")]
    if not sounds:
        raise media.StepFailed(f"Gemini sent back no song (finish reason: {candidate.get('finishReason') or 'none given'})")
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


def as_mp3(audio: bytes, mime_type: str) -> bytes:
    """Lyria's song as a constant-bitrate MP3, whatever it sent (an MP3 of any kind, a WAV).

    Every later step reads audio/song.mp3's length from its header (ffprobe), and a
    variable-bitrate MP3 without one reads as the wrong length; the upload calls it
    audio/mpeg. So the song is always encoded again, once, from a file to a file, so
    ffmpeg can seek the input and write the output's length header.
    """
    with tempfile.TemporaryDirectory(prefix="popsmosis-song-") as tmp:
        source, out = Path(tmp) / "lyria", Path(tmp) / "song.mp3"
        source.write_bytes(audio)
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-vn", "-codec:a", "libmp3lame",
                            "-b:a", SONG_BITRATE, str(out)], capture_output=True, check=True, timeout=300)
            return out.read_bytes()
        except subprocess.CalledProcessError as e:
            detail = e.stderr.decode("utf-8", "replace").strip()[:200]
        except (OSError, subprocess.TimeoutExpired) as e:
            detail = str(e)
    raise media.StepFailed(f"cannot keep Gemini's song ({mime_type or 'no type given'}) as an MP3: {detail}")


# --- narration and the exam question ------------------------------------------------------


def speech_lines(bundle: Bundle, kind: str) -> list[tuple[str, str]]:
    if kind == "narration":
        return [(n.id, n.text) for n in bundle.narration]
    return media.frq_lines(bundle)


def voice_id() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE


def _make_speech(job: Path, kind: str, lines: list[tuple[str, str]]) -> None:
    text, spans = join_lines(lines)
    if len(text) > TTS_MAX_CHARS:
        raise media.StepFailed(f"the {kind} is {len(text)} characters; one text-to-speech call takes {TTS_MAX_CHARS}")
    voice = voice_id()
    status, headers, answer = _eleven("POST", f"/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
                                      {"text": text, "model_id": TTS_MODEL})
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
        "source": lines, "text": text, "lines": spans, "voice_id": voice, "model_id": TTS_MODEL,
        "alignment": alignment, "headers": _kept(headers),
    })
    _write_bytes(job / media.audio_path(kind), audio)


def join_lines(lines: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """The lines read as one text, and each line's [start, end) in it, in characters."""
    text, spans = "", []
    for line_id, line in lines:
        if text:
            text += " "
        spans.append({"id": line_id, "start": len(text), "end": len(text) + len(line)})
        text += line
    return text, spans


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


def duration(path: Path) -> float:
    """An audio file's length in seconds (ffprobe)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, check=True).stdout
        return float(out.strip())
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        raise media.StepFailed(f"cannot read the length of {path.name}: {e}") from e
