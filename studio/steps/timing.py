"""Step 6: timing. When each line, and each word in it, is heard in each audio file.

    timing/song_words.json        Demucs takes the vocals out of the song, then the MMS
                                  forced aligner finds the exact lyrics in them, one
                                  section at a time (across the whole song at once when
                                  its sections are not pinned: a Gemini song)
    timing/narration_words.json   from the character timings the TTS returned (step 5)
    timing/song_report.json       how sure the aligner was of each line, the lines it was unsure
                                  of that it heard again alone between their neighbours, and
                                  chorus drift
    audio/song.vocals.wav         the separated vocals, 16 kHz mono, kept so a re-run skips Demucs
                                  (until the song is made again)

Drift: a chorus line sung several times should take about as long each time. A
repeat more than DRIFT_S away from the median of its repeats is flagged in the
report and in the step's summary line; the timing is still written, since the
renderer can live with it, but a flagged section is the one to regenerate.

Not sung: Eleven Music sometimes makes a chunk with no singing in it (an intro left
instrumental). A pinned section whose own chunk holds less than SUNG_S of singing has
its lines marked not sung, timed at no length where the chunk ends, so the videos
never show them and the aligner does not take them out of the next section's audio.
The report and the summary line name them.

The lyrics as sung: the song goes public with the vetted lyrics under it, so the singer
must have sung them. A line the aligner is unsure of (LOW_SCORE) was probably sung
differently; a few are normal (a gang-vocal chorus, Lyria's ad-libs: 4 to 9 of about 30
lines in the test rounds). When more than MISMATCH_SHARE of the lines are, the song is not
the lyrics, whatever it does sing: the step fails, and the song is deleted so the retry
makes it again rather than timing the same song. Lines marked not sung are left out of the
count: the videos never show them.

torch, torchaudio and demucs are imported only when the song is aligned, so the rest
of the studio (and its tests) load without them. Both models run on this machine, so
calls.jsonl logs them with their time and no cost (studio/ledger.py).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import statistics
import subprocess
import time
import unicodedata
import wave
from concurrent.futures import ThreadPoolExecutor
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
# A section is sung when its chunk holds at least SUNG_S of vocals within QUIET_DB of the song's loud singing
# (its LOUD_PERCENTILE level, every FRAME_S). The unsung intro of Oct 30 sat 53 to 71 dB below it, the singing
# within 10 dB; a pickup into the next section, or a breath, is shorter than SUNG_S.
SUNG_S = 0.5
QUIET_DB = 30.0
LOUD_PERCENTILE = 95
FRAME_S = 0.05
MISMATCH_SHARE = 0.5  # more than this share of the lines sung differently: the song is not its lyrics
SAMPLE_RATE = 16000
CHUNK_S = 30  # the aligner hears a longer window this much at a time (_emission)
CONTEXT_S = 5  # with this much of the audio either side of each piece
DEMUCS_MODEL = "htdemucs"
MMS_MODEL = "mms-fa"  # torchaudio.pipelines.MMS_FA, Meta's forced aligner
# Characters around a word that are not part of it ("word," -> "word").
_EDGES = "\"'.,;:!?()[]{}<>“”‘’«»…—–-*_/\\|"

TURN = ".timing.lock"  # beside the jobs: the jobs on one machine time their songs in turn


@contextlib.contextmanager
def cpu_turn(job: Path):
    """Wait for this machine's other jobs to finish timing a song. Demucs and the aligner each use
    every core; two at once (a test round's topics side by side) took 8 times as long each, their
    threads fighting for the cores, where taking turns costs only the wait. A routine runs on a
    machine of its own, so it never waits."""
    with open(job.parent / TURN, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# align(vocals at 16 kHz, a list of normalised words) -> per word (start s, end s, score),
# in the window's own time. Tests pass their own.
Aligner = Callable[[object, list[str]], list[tuple[float, float, float]]]


def run(job: Path, ask=brain.ask) -> str:
    bundle = jobs.read(job / "bundle.json", Bundle)
    if not audio_step.is_done(job):
        raise media.StepFailed("the audio step has not made both audio files from this bundle yet")
    # Not done until both are written again; the cached vocals are kept.
    media.restart(job, *(media.timing_path(kind) for kind in media.TIMED), REPORT)
    parts = []
    for kind in audio_step.SPEECH:
        try:
            timing = speech_timing(job, kind)
        except (ValueError, KeyError, IndexError) as e:
            raise media.StepFailed(f"the {kind}'s character timings do not fit its lines: {e}") from e
        _check(timing, [i for i, _ in audio_step.speech_lines(bundle, kind)], kind)
        (job / "timing").mkdir(exist_ok=True)
        jobs.write(job / media.timing_path(kind), timing)
        parts.append(f"{kind} {len(timing.lines)} lines")
    try:
        with cpu_turn(job):
            timing, report = song_timing(job, bundle)
    except (ValueError, RuntimeError) as e:  # a timing out of order, or an aligner that could not fit the words
        raise media.StepFailed(f"the song could not be aligned: {e}") from e
    _check(timing, [line.id for line in bundle.lyrics], "song")
    jobs.write(job / REPORT, report)  # kept either way: `show` and the summary say what was heard
    mismatch = lyric_mismatch(report)
    if mismatch:
        # Made again on the retry (the audio step makes only what is missing), never timed and published as it is.
        (job / media.audio_path("song")).unlink(missing_ok=True)
        raise media.StepFailed(f"{mismatch}; the song is deleted, so the retry sings it again",
                               code="the song did not sing its lyrics")
    jobs.write(job / media.timing_path("song"), timing)
    return f"song {len(timing.lines)} lines in {timing.duration:.1f}s, {song_notes(report)}; " + ", ".join(parts)


def is_done(job: Path) -> bool:
    """Every Timing is there, times the bundle's lines as they now stand, and is newer than its audio."""
    try:
        bundle = jobs.read(job / "bundle.json", Bundle)
        expected = {"song": [line.id for line in bundle.lyrics],
                    "narration": [i for i, _ in audio_step.speech_lines(bundle, "narration")]}
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


# --- the narration: the TTS character timings ---------------------------------------------


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
    # The aligner loads while Demucs runs. The first read of its 1.2 GB on a fresh machine took 100 s here, waiting on
    # the disk while Demucs works the cores; once the file is cached its load is 9 s, and beside Demucs saves a few.
    if align is None:
        _import_models()
    with ThreadPoolExecutor(1, thread_name_prefix="mms") as loading:
        aligner = None if align else loading.submit(mms_aligner)
        if vocals is None:
            started, cached = time.monotonic(), vocals_current(job)
            vocals = separate_vocals(job)
            if not cached:
                _log_local(job, "song vocals", DEMUCS_MODEL, started)
        aligning = None if align else time.monotonic()
        align = align or aligner.result()
    texts = {line.id: line.text for line in bundle.lyrics}
    planned_ms = sum(s["duration_ms"] for s in plan["sections"])
    scale = length * 1000 / planned_ms if planned_ms else 1.0  # the song can run a little long or short

    levels = vocal_levels(vocals) if plan.get("sections_pinned", True) else None
    skipped = left_out(plan, texts)
    lines, scores = [], {}
    for section in windows(plan):
        start = section["start_ms"] * scale / 1000
        end = min(length, (section["start_ms"] + section["duration_ms"]) * scale / 1000)
        previous = lines[-1].end if lines else 0.0
        if levels is not None and sung_seconds(levels, start, end) < SUNG_S:
            at = round(max(end, previous), 3)
            for line_id in section["line_ids"]:
                lines.append(_unsung(line_id, texts, at))
                scores[line_id] = None
            continue
        t0 = max(0.0, start - MARGIN_S)
        t1 = min(length, end + MARGIN_S)
        sung = [line_id for line_id in section["line_ids"] if line_id not in skipped]
        timed = iter(_align_lines(vocals, align, texts, sung, t0, t1, previous, length) if sung else [])
        for line_id in section["line_ids"]:
            if line_id in skipped:  # at no length where the line before it ends, and never shown
                lines.append(_unsung(line_id, texts, lines[-1].end if lines else 0.0))
                scores[line_id] = None
                continue
            line, score = next(timed)
            lines.append(line)
            scores[line.line_id] = score
    realigned = []
    # A line the aligner was unsure of, among many, may have been pulled out of place by a
    # repeat or an ad-lib the lyrics do not have (seven words crammed into the last second
    # before the next line). Heard alone between its neighbours, it finds where it is sung.
    for k, line in enumerate(lines):
        if scores[line.line_id] is None or scores[line.line_id] >= LOW_SCORE:
            continue
        t0 = lines[k - 1].end if k else 0.0
        t1 = lines[k + 1].start if k + 1 < len(lines) else length
        try:
            (again, score), = _align_lines(vocals, align, texts, [line.line_id], t0, t1, t0, t1)
        except (RuntimeError, ValueError):  # too little room between its neighbours for its words
            continue
        if score is not None and score > scores[line.line_id]:
            lines[k], scores[line.line_id] = again, score
            realigned.append(line.line_id)
    timing = Timing(audio=media.audio_path("song"), duration=round(length, 3), lines=lines)
    if aligning is not None:
        _log_local(job, "song alignment", MMS_MODEL, aligning)
    return timing, {**song_report(timing, texts, scores), "realigned": realigned}


def _unsung(line_id: str, texts: dict[str, str], at: float) -> TimedLine:
    """A line not sung, timed at no length at `at`: the timeline never shows it."""
    at = round(at, 3)
    return TimedLine(line_id=line_id, start=at, end=at, sung=False,
                     words=[Word(word=w, start=at, end=at) for w, _, _ in words_of(texts[line_id])])


# Lyria's own text of the song: each line it sang, after its start ("[12.5:] Deep underground...") or a bare "[:]",
# under section marks ("[[B1]]").
LYRIA_LINE = re.compile(r"^\[[\d.]*:\]\s*")
LYRIA_SECTION = re.compile(r"^\[\[.*\]\]$|^\[[^\]:]*\]$")
TRUST_TEXT = 0.6  # a text that matches fewer of the lyrics than this is not the song's, and is not used


def left_out(plan: dict, texts: dict[str, str]) -> set[str]:
    """The lines Lyria's own text of the song (its answer's text, kept in the plan) leaves out: round 17's kidney
    song stopped after its last chorus, and the aligner, asked for the outro too, crammed seven lines into the
    song's last seven seconds, flashed in the band and never sung. Lines it changed a little are still sung.
    Nothing for Eleven Music, which keeps to its sections, or for a text that does not match the lyrics."""
    if plan.get("sections_pinned", True) or not (plan.get("text") or "").strip():
        return set()
    order = [line_id for section in plan["sections"] for line_id in section["line_ids"]]

    def plain(text: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", "", unicodedata.normalize("NFKC", text).lower()).split())

    sung = [plain(LYRIA_LINE.sub("", line.strip())) for line in plan["text"].splitlines()
            if line.strip() and not LYRIA_SECTION.match(line.strip())]
    matcher = SequenceMatcher(a=[plain(texts[line_id]) for line_id in order], b=sung, autojunk=False)
    if sum(block.size for block in matcher.get_matching_blocks()) < TRUST_TEXT * len(order):
        return set()
    return {order[k] for op, a0, a1, _, _ in matcher.get_opcodes() if op == "delete" for k in range(a0, a1)}


def _align_lines(vocals, align: Aligner, texts: dict[str, str], line_ids: list[str], t0: float, t1: float,
                 previous: float, end_by: float) -> list[tuple[TimedLine, float | None]]:
    """The lines aligned together in the vocals between t0 and t1 (song time), each with the
    aligner's mean score for its words; no word starts before `previous` or ends after `end_by`."""
    window = vocals[..., int(t0 * SAMPLE_RATE):int(t1 * SAMPLE_RATE)]
    words = [(line_id, word) for line_id in line_ids for word, _, _ in words_of(texts[line_id])]
    sayable = [i for i, (_, word) in enumerate(words) if spoken(word)]
    found = align(window, [spoken(words[i][1]) for i in sayable]) if sayable else []
    timed: list[tuple[float, float, float] | None] = [None] * len(words)
    for i, (start, end, score) in zip(sayable, found):
        timed[i] = (start + t0, end + t0, score)
    out = []
    for line_id in line_ids:
        line_words, line_scores = [], []
        for i, (owner, word) in enumerate(words):
            if owner != line_id:
                continue
            # A word the aligner cannot spell ("+") sits at the end of the one before it.
            start, end, score = timed[i] if timed[i] else (previous, previous, None)
            start = max(start, previous)
            end = min(max(end, start), end_by)
            start = min(start, end)
            line_words.append(Word(word=word, start=round(start, 3), end=round(end, 3)))
            if score is not None:
                line_scores.append(score)
            previous = end
        if not line_words:
            raise media.StepFailed(f"{line_id} has no words to time")
        out.append((TimedLine(line_id=line_id, start=line_words[0].start, end=line_words[-1].end, words=line_words),
                    round(sum(line_scores) / len(line_scores), 3) if line_scores else None))
    return out


def _import_models() -> None:
    """Import Demucs's and the aligner's packages in this thread, before the aligner loads in another: torchaudio
    imported by both threads at once deadlocked on its module locks. Without them installed, the steps say so
    when they import them."""
    try:
        import demucs.apply  # noqa: F401
        import demucs.pretrained  # noqa: F401
        import torchaudio.functional  # noqa: F401
        import torchaudio.pipelines  # noqa: F401
    except ImportError:
        pass


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


def vocal_levels(vocals) -> list[float] | None:
    """The separated vocals' level every FRAME_S, in dB below the song's loud singing; None when nothing in
    them is sung at all, so no section can be told from the rest."""
    import numpy as np

    mono = np.asarray(vocals, dtype=np.float32).reshape(-1)
    frame = int(FRAME_S * SAMPLE_RATE)
    frames = len(mono) // frame
    if not frames:
        return None
    rms = np.sqrt((mono[:frames * frame].reshape(frames, frame) ** 2).mean(axis=1))
    loud = float(np.percentile(rms, LOUD_PERCENTILE))
    if loud < 1e-4:  # -80 dBFS: silence
        return None
    return (20 * np.log10(np.maximum(rms, 1e-12) / loud)).tolist()


def sung_seconds(levels: list[float], t0: float, t1: float) -> float:
    """How long the vocals between t0 and t1 (song time) are within QUIET_DB of the song's loud singing."""
    frames = levels[max(0, int(t0 / FRAME_S)):max(0, int(t1 / FRAME_S))]
    return sum(level >= -QUIET_DB for level in frames) * FRAME_S


def song_report(timing: Timing, texts: dict[str, str], scores: dict[str, float | None]) -> dict:
    """Each line's length and aligner score, the chorus repeats that drift more than DRIFT_S, and the lines
    not sung (their rows say so, and they are left out of the drift)."""
    rows = [{"line_id": line.line_id, "text": texts[line.line_id], "seconds": round(line.end - line.start, 3),
             "score": scores.get(line.line_id), **({} if line.sung else {"sung": False})} for line in timing.lines]
    by_text: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("sung", True):
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
        "unsung": [r["line_id"] for r in rows if not r.get("sung", True)],
    }


def lyric_mismatch(report: dict) -> str | None:
    """Why the song is not its lyrics (more than MISMATCH_SHARE of its sung lines sung differently), or None."""
    scored = [row for row in report.get("lines", []) if row.get("score") is not None and row.get("sung", True)]
    unsure = [row["line_id"] for row in scored if row["score"] < LOW_SCORE]
    if not scored or len(unsure) <= MISMATCH_SHARE * len(scored):
        return None
    return (f"the song does not sing its lyrics: the aligner could not hear {len(unsure)} of its {len(scored)} lines "
            f"({', '.join(unsure[:8])}{', ...' if len(unsure) > 8 else ''})")


def song_notes(report: dict) -> str:
    """The report in a few words, for the summary line and `python -m studio show`."""
    worst = max((abs(d["drift"]) for d in report["drift"]), default=0.0)
    notes = [f"chorus drift at most {worst:.2f}s" if report["drift"] else "no repeated lines"]
    if report.get("unsung"):  # a report from before the check has none
        notes.append(f"NOT SUNG: {', '.join(report['unsung'])}")
    if report["drifting"]:
        notes.append(f"DRIFT over {DRIFT_S}s: {', '.join(report['drifting'])}")
    if report.get("realigned"):  # a report from before the repair pass has none
        notes.append(f"re-aligned alone: {', '.join(report['realigned'])}")
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
    made again (other lines, another singer) has its vocals separated again."""
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
    """MMS's emission for a window, heard CHUNK_S at a time with CONTEXT_S of audio either side.

    MMS learned from utterances of seconds, not minutes, and one pass over a whole song
    (a Gemini one) blurs what it hears: the first live Lyria song had 15 of its 28 lines
    unsure heard in one pass, and 3 heard in pieces. Each piece is a pass of its own (the
    memory, about 17 MB a second of audio on the front end, stays that of one piece) and
    keeps only the frames of its middle CHUNK_S; its frames fall on the grid one pass
    would give, so the pieces join into an emission with one pass's frames. A model of
    another shape (torchaudio changed) gets one pass.
    """
    import torch

    shape = _front_end(model)
    if shape is None or window.size(1) <= (CHUNK_S + 2 * CONTEXT_S) * SAMPLE_RATE:
        return model(window)[0]
    field, stride = shape
    frames = (window.size(1) - field) // stride + 1
    step, context = int(CHUNK_S * SAMPLE_RATE) // stride, int(CONTEXT_S * SAMPLE_RATE) // stride
    pieces = []
    for f0 in range(0, frames, step):
        f1 = min(frames, f0 + step)
        a, b = max(0, f0 - context), min(frames, f1 + context)
        heard = model(window[:, a * stride:(b - 1) * stride + field])[0]  # frames a to b of the window
        pieces.append(heard[:, f0 - a:f1 - a])
    return torch.cat(pieces, dim=1)


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
