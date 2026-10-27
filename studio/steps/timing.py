"""Step 6: timing. When each line, and each word in it, is heard in each audio file.

    timing/song_words.json        Demucs takes the vocals out of the song, then the MMS
                                  forced aligner finds the exact lyrics in them, one
                                  section at a time (across the whole song at once when
                                  its sections are not pinned: a Gemini song)
    timing/narration_words.json   from the character timings the TTS returned (step 5)
    timing/frq_words.json
    timing/song_report.json       how sure the aligner was of each line, and chorus drift
    audio/song.vocals.wav         the separated vocals, 16 kHz mono, kept so a re-run skips Demucs
                                  (until the song is made again)

Drift: a chorus line sung several times should take about as long each time. A
repeat more than DRIFT_S away from the median of its repeats is flagged in the
report and in the step's summary line; the timing is still written, since the
renderer can live with it, but a flagged section is the one to regenerate.

torch, torchaudio and demucs are imported only when the song is aligned, so the rest
of the studio (and its tests) load without them. Both models run on this machine, so
calls.jsonl logs them with their time and no cost (studio/ledger.py).
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
import unicodedata
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from studio import brain, jobs, ledger, media
from studio.schemas import TOLERANCE_S, Bundle, TimedLine, Timing, Word, timing_problems
from studio.steps import audio as audio_step

REPORT = "timing/song_report.json"
VOCALS = "audio/song.vocals.wav"
DRIFT_S = 0.5
MARGIN_S = 2.0  # how far past its planned ends each section's window reaches
LOW_SCORE = 0.3  # a line the aligner is this unsure of was probably sung differently
SAMPLE_RATE = 16000
CHUNK_S = 30  # the aligner's front end hears a longer window this much at a time (_emission)
DEMUCS_MODEL = "htdemucs"
MMS_MODEL = "mms-fa"  # torchaudio.pipelines.MMS_FA, Meta's forced aligner
# Characters around a word that are not part of it ("word," -> "word").
_EDGES = "\"'.,;:!?()[]{}<>“”‘’«»…—–-*_/\\|"

# align(vocals at 16 kHz, a list of normalised words) -> per word (start s, end s, score),
# in the window's own time. Tests pass their own.
Aligner = Callable[[object, list[str]], list[tuple[float, float, float]]]


def run(job: Path, ask=brain.ask) -> str:
    bundle = jobs.read(job / "bundle.json", Bundle)
    if not audio_step.is_done(job):
        raise media.StepFailed("the audio step has not made all three audio files from this bundle yet")
    # Not done until all three are written again; the cached vocals are kept.
    media.restart(job, *(media.timing_path(kind) for kind in media.TIMED), REPORT)
    parts = []
    for kind in ("narration", "frq"):
        try:
            timing = speech_timing(job, kind)
        except (ValueError, KeyError, IndexError) as e:
            raise media.StepFailed(f"the {kind}'s character timings do not fit its lines: {e}") from e
        _check(timing, [i for i, _ in audio_step.speech_lines(bundle, kind)], kind)
        (job / "timing").mkdir(exist_ok=True)
        jobs.write(job / media.timing_path(kind), timing)
        parts.append(f"{kind} {len(timing.lines)} lines")
    try:
        timing, report = song_timing(job, bundle)
    except (ValueError, RuntimeError) as e:  # a timing out of order, or an aligner that could not fit the words
        raise media.StepFailed(f"the song could not be aligned: {e}") from e
    _check(timing, [line.id for line in bundle.lyrics], "song")
    jobs.write(job / REPORT, report)
    jobs.write(job / media.timing_path("song"), timing)
    return f"song {len(timing.lines)} lines in {timing.duration:.1f}s, {song_notes(report)}; " + ", ".join(parts)


def is_done(job: Path) -> bool:
    """Every Timing is there, times the bundle's lines as they now stand, and is newer than its audio."""
    try:
        bundle = jobs.read(job / "bundle.json", Bundle)
        expected = {"song": [line.id for line in bundle.lyrics],
                    "narration": [i for i, _ in audio_step.speech_lines(bundle, "narration")],
                    "frq": [i for i, _ in audio_step.speech_lines(bundle, "frq")]}
        for kind, ids in expected.items():
            path = job / media.timing_path(kind)
            if timing_problems(jobs.read(path, Timing), ids):
                return False
            if path.stat().st_mtime < (job / media.audio_path(kind)).stat().st_mtime:
                return False  # the audio was made again after it was timed
    except (OSError, ValueError):
        return False
    return audio_step.is_done(job)


def _check(timing: Timing, line_ids: list[str], kind: str) -> None:
    problems = timing_problems(timing, line_ids)
    if problems:
        raise media.StepFailed(f"the {kind} timing does not match the bundle: {'; '.join(problems)}")


# --- words -------------------------------------------------------------------------------


def words_of(text: str) -> list[tuple[str, int, int]]:
    """Each word of a line with its [start, end) in characters, without the punctuation around it."""
    found = []
    for match in re.finditer(r"\S+", text):
        token, start = match.group(), match.start()
        left = len(token) - len(token.lstrip(_EDGES))
        word = token.strip(_EDGES)
        if word:
            found.append((word, start + left, start + left + len(word)))
    return found


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (_ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + "hundred" + (_number(n % 100) if n % 100 else "")
    return "".join(_ONES[int(d)] for d in str(n))


# Letters as they are said, for words sung letter by letter ("ATP", "b6f"), spelled the
# way the aligner's romanised alphabet hears them.
_LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", (
    "ei bi si di i ef dji eitch ai djei kei el em en ou pi kiu ar es ti iu vi dabliu eks wai zed").split()))


def _said_letter_by_letter(word: str) -> bool:
    """An acronym (ATP, NADPH) or letters mixed with digits (b6f, PS2)."""
    letters = re.sub(r"[^A-Za-z]", "", word)
    if not letters:
        return False
    return (word.isupper() and 2 <= len(letters) <= 6) or (bool(re.search(r"\d", word)) and len(letters) <= 3)


def spoken(word: str) -> str:
    """A word as the aligner's alphabet can spell it: lower case a-z and ', numbers in words,
    acronyms as their letters' names."""
    if word == "I":
        return "ai"
    if _said_letter_by_letter(word):
        return "".join(_LETTERS.get(c, "") if c.isalpha() else spoken(c) if c.isdigit() else ""
                       for c in re.findall(r"[A-Za-z]|\d+", unicodedata.normalize("NFKD", word.lower())))
    plain = unicodedata.normalize("NFKD", word.lower())
    plain = re.sub(r"\d+", lambda m: _number(int(m.group())), plain)
    return re.sub(r"[^a-z']", "", plain)


# --- narration and the exam question: the TTS character timings ---------------------------


def speech_timing(job: Path, kind: str) -> Timing:
    made = json.loads((job / audio_step.plan_path(kind)).read_text(encoding="utf-8"))
    return timing_from_chars(kind, made["text"], made["lines"], made["alignment"],
                             audio_step.duration(job / media.audio_path(kind)))


def timing_from_chars(kind: str, text: str, spans: list[dict], alignment: dict, length: float) -> Timing:
    """Words from character timings: a word runs from its first character's start to its last one's end."""
    starts, ends = alignment["character_start_times_seconds"], alignment["character_end_times_seconds"]
    index = _char_map(text, "".join(alignment["characters"]))
    lines, previous = [], 0.0
    for span in spans:
        words = []
        for word, a, b in words_of(text[span["start"]:span["end"]]):
            first, last = index[span["start"] + a], index[span["start"] + b - 1]
            start = max(previous, starts[first] if first is not None else previous)
            end = max(start, ends[last] if last is not None else start)
            words.append(Word(word=word, start=round(start, 3), end=round(end, 3)))
            previous = end
        if not words:
            raise media.StepFailed(f"{span['id']} has no words to time")
        lines.append(TimedLine(line_id=span["id"], start=words[0].start, end=words[-1].end, words=words))
    end = max(length, lines[-1].end if lines else 0)
    return Timing(audio=media.audio_path(kind), duration=round(end, 3), lines=lines)


def _char_map(text: str, heard: str) -> list[int | None]:
    """For each character of the text, its index in the timed characters (None if it has none)."""
    if text == heard:
        return list(range(len(text)))
    index: list[int | None] = [None] * len(text)
    for block in SequenceMatcher(None, text, heard, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            index[block.a + k] = block.b + k
    # A character with no timing borrows the nearest one before it.
    last = None
    for i, j in enumerate(index):
        if j is None:
            index[i] = last
        last = index[i]
    return index


# --- the song: Demucs, then the MMS aligner, section by section ------------------------------


def song_timing(job: Path, bundle: Bundle, align: Aligner | None = None, vocals=None) -> tuple[Timing, dict]:
    """The song's Timing and its report. `align` and `vocals` stand in for MMS and Demucs in tests."""
    plan = json.loads((job / audio_step.plan_path("song")).read_text(encoding="utf-8"))
    length = audio_step.duration(job / media.audio_path("song"))
    if vocals is None:
        started, cached = time.monotonic(), vocals_current(job)
        vocals = separate_vocals(job)
        if not cached:
            _log_local(job, "song vocals", DEMUCS_MODEL, started)
    aligning = None if align else time.monotonic()
    align = align or mms_aligner()
    texts = {line.id: line.text for line in bundle.lyrics}
    planned_ms = sum(s["duration_ms"] for s in plan["sections"])
    scale = length * 1000 / planned_ms if planned_ms else 1.0  # the song can run a little long or short

    lines, scores, previous = [], {}, 0.0
    for section in windows(plan):
        t0 = max(0.0, section["start_ms"] * scale / 1000 - MARGIN_S)
        t1 = min(length, (section["start_ms"] + section["duration_ms"]) * scale / 1000 + MARGIN_S)
        window = vocals[..., int(t0 * SAMPLE_RATE):int(t1 * SAMPLE_RATE)]
        words = [(line_id, word) for line_id in section["line_ids"] for word, _, _ in words_of(texts[line_id])]
        sayable = [i for i, (_, word) in enumerate(words) if spoken(word)]
        found = align(window, [spoken(words[i][1]) for i in sayable]) if sayable else []
        timed: list[tuple[float, float, float] | None] = [None] * len(words)
        for i, (start, end, score) in zip(sayable, found):
            timed[i] = (start + t0, end + t0, score)
        for line_id in section["line_ids"]:
            line_words, line_scores = [], []
            for i, (owner, word) in enumerate(words):
                if owner != line_id:
                    continue
                # A word the aligner cannot spell ("+") sits at the end of the one before it.
                start, end, score = timed[i] if timed[i] else (previous, previous, None)
                start = max(start, previous)
                end = min(max(end, start), length)
                start = min(start, end)
                line_words.append(Word(word=word, start=round(start, 3), end=round(end, 3)))
                if score is not None:
                    line_scores.append(score)
                previous = end
            if not line_words:
                raise media.StepFailed(f"{line_id} has no words to time")
            lines.append(TimedLine(line_id=line_id, start=line_words[0].start, end=line_words[-1].end,
                                   words=line_words))
            scores[line_id] = round(sum(line_scores) / len(line_scores), 3) if line_scores else None
    timing = Timing(audio=media.audio_path("song"), duration=round(length, 3), lines=lines)
    if aligning is not None:
        _log_local(job, "song alignment", MMS_MODEL, aligning)
    return timing, song_report(timing, texts, scores)


def _log_local(job: Path, label: str, model: str, started: float) -> None:
    """A model run on this machine: its time, and no bill."""
    ledger.log(job, [brain.Call(label, "local", model, 0, 0, 0.0, round(time.monotonic() - started, 1), "")])


def windows(plan: dict) -> list[dict]:
    """Where to look for each section's lines: where it was planned, when the provider keeps
    to the plan (Eleven Music makes each section a chunk of its planned length), else the
    whole song at once (Lyria, which may add an intro or sing a section longer or shorter)."""
    if plan.get("sections_pinned", True):
        return plan["sections"]
    planned_ms = sum(s["duration_ms"] for s in plan["sections"])
    return [{"line_ids": [i for s in plan["sections"] for i in s["line_ids"]], "start_ms": 0, "duration_ms": planned_ms}]


def song_report(timing: Timing, texts: dict[str, str], scores: dict[str, float | None]) -> dict:
    """Each line's length and aligner score, and the chorus repeats that drift more than DRIFT_S."""
    rows = [{"line_id": line.line_id, "text": texts[line.line_id], "seconds": round(line.end - line.start, 3),
             "score": scores.get(line.line_id)} for line in timing.lines]
    by_text: dict[str, list[dict]] = {}
    for row in rows:
        by_text.setdefault(" ".join(spoken(w) for w, _, _ in words_of(row["text"])), []).append(row)
    drift = []
    for repeats in by_text.values():
        if len(repeats) < 2:
            continue
        median = statistics.median(r["seconds"] for r in repeats)
        for r in repeats:
            drift.append({"line_id": r["line_id"], "seconds": r["seconds"], "median": round(median, 3),
                          "drift": round(r["seconds"] - median, 3)})
    return {
        "lines": rows,
        "drift": drift,
        "drifting": [d["line_id"] for d in drift if abs(d["drift"]) > DRIFT_S + TOLERANCE_S / 10],
        "unsure": [r["line_id"] for r in rows if r["score"] is not None and r["score"] < LOW_SCORE],
    }


def song_notes(report: dict) -> str:
    """The report in a few words, for the summary line and `python -m studio show`."""
    worst = max((abs(d["drift"]) for d in report["drift"]), default=0.0)
    notes = [f"chorus drift at most {worst:.2f}s" if report["drift"] else "no repeated lines"]
    if report["drifting"]:
        notes.append(f"DRIFT over {DRIFT_S}s: {', '.join(report['drifting'])}")
    if report["unsure"]:
        notes.append(f"aligner unsure of {', '.join(report['unsure'])}")
    return "; ".join(notes)


def separate_vocals(job: Path):
    """The song's vocals at 16 kHz mono, as a (1, samples) tensor; Demucs runs once, then the wav is reused."""
    import numpy as np
    import torch

    path = job / VOCALS
    if vocals_current(job):
        with wave.open(str(path), "rb") as f:
            pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        return torch.from_numpy(pcm.astype(np.float32) / 32768).unsqueeze(0)

    import torchaudio.functional as F
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model(DEMUCS_MODEL)  # what `demucs --two-stems=vocals` uses
    model.eval()
    mix = torch.from_numpy(_decode(job / media.audio_path("song"), model.samplerate, model.audio_channels))
    ref = mix.mean(0)
    mix = (mix - ref.mean()) / (ref.std() + 1e-8)
    with torch.inference_mode():
        sources = apply_model(model, mix[None], device="cpu", split=True, overlap=0.25, progress=False)[0]
    vocals = sources[model.sources.index("vocals")] * ref.std() + ref.mean()
    vocals = F.resample(vocals.mean(0, keepdim=True), model.samplerate, SAMPLE_RATE)
    pcm = (vocals.clamp(-1, 1) * 32767).to(torch.int16).numpy()
    partial = path.with_name(f".{path.name}.partial")
    with wave.open(str(partial), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm.tobytes())
    partial.replace(path)
    return vocals


def vocals_current(job: Path) -> bool:
    """The kept vocals were separated from the song as it now is: they are newer than it. A song
    made again (other lines, another SONG_PROVIDER) has its vocals separated again."""
    try:
        return (job / VOCALS).stat().st_mtime >= (job / media.audio_path("song")).stat().st_mtime
    except OSError:
        return False


def _decode(path: Path, rate: int, channels: int):
    """An audio file as float32 samples, (channels, samples), through ffmpeg."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(channels),
                          "-ar", str(rate), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels).T.copy()


def mms_aligner() -> Aligner:
    """MMS forced alignment (torchaudio.pipelines.MMS_FA), with a <star> at each end of the
    window to soak up the neighbouring sections' lines that the margins let in."""
    import torch
    from torchaudio.pipelines import MMS_FA

    model = MMS_FA.get_model(with_star=True)
    model.eval()
    tokenizer, aligner = MMS_FA.get_tokenizer(), MMS_FA.get_aligner()

    def align(window, words: list[str]) -> list[tuple[float, float, float]]:
        with torch.inference_mode():
            emission = _emission(model, window)
        spans = aligner(emission[0], tokenizer(["*", *words, "*"]))[1:-1]
        seconds = window.size(1) / emission.size(1) / SAMPLE_RATE
        return [(s[0].start * seconds, s[-1].end * seconds, sum(t.score * len(t) for t in s) / sum(len(t) for t in s))
                for s in spans]

    return align


def _emission(model, window):
    """MMS's emission for a window, the same as `model(window)[0]` in a fraction of the memory.

    The memory goes on the convolutional front end, about 17 MB a second of audio, which
    a whole song (a Gemini one) would push past 4 GB. Each of its frames hears only a
    few hundred samples and is normalised on its own, so it runs CHUNK_S at a time and
    gives exactly the frames one pass would; the transformer after it still hears the
    whole window. A model of another shape (torchaudio changed) gets one pass.
    """
    import torch

    shape = _front_end(model)
    if shape is None or window.size(1) <= CHUNK_S * SAMPLE_RATE:
        return model(window)[0]
    field, stride = shape
    inner = model.model
    x = torch.nn.functional.layer_norm(window, window.shape) if model.normalize_waveform else window
    frames = (x.size(1) - field) // stride + 1
    step = CHUNK_S * SAMPLE_RATE // stride
    pieces = [inner.feature_extractor(x[:, stride * f0:stride * (min(frames, f0 + step) - 1) + field], None)[0]
              for f0 in range(0, frames, step)]
    out = inner.encoder(torch.cat(pieces, dim=1), None)
    if inner.aux is not None:
        out = inner.aux(out)
    if model.apply_log_softmax:
        out = torch.nn.functional.log_softmax(out, dim=-1)
    if model.append_star:
        out = torch.cat((out, torch.zeros((1, out.size(1), 1), dtype=out.dtype, device=out.device)), dim=-1)
    return out


def _front_end(model) -> tuple[int, int] | None:
    """How many samples one frame of the front end hears, and how far apart frames are: (400, 320)
    for MMS. None unless the model is torchaudio's wrapped Wav2Vec2 with frame-local norms only."""
    import torch

    try:
        inner, layers = model.model, model.model.feature_extractor.conv_layers
        if not all(hasattr(model, a) for a in ("normalize_waveform", "apply_log_softmax", "append_star")):
            return None
        if not all(hasattr(inner, a) for a in ("encoder", "aux")):
            return None
        field, stride = 1, 1
        for layer in layers:
            if layer.layer_norm is not None and not isinstance(layer.layer_norm, torch.nn.LayerNorm):
                return None  # a GroupNorm spans the whole window: pieces would differ
            if layer.conv.padding not in ((0,), 0, "valid") or layer.conv.dilation != (1,):
                return None
            field += (layer.conv.kernel_size[0] - 1) * stride
            stride *= layer.conv.stride[0]
        return field, stride
    except (AttributeError, TypeError, IndexError):
        return None
