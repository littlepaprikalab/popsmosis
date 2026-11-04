"""Step 6: timing. When each line, and each word in it, is heard in each audio file.

    timing/song_words.json        Demucs takes the vocals out of the song, then the MMS
                                  forced aligner finds the exact lyrics in them, one
                                  section at a time (across the whole song at once when
                                  its sections are not pinned: a Gemini song)
    timing/narration_words.json   from the character timings ElevenLabs returned (step 5), or
                                  for a Gemini narration, which has none, the MMS aligner
                                  over the whole narration (no Demucs: there is no music)
    timing/narration_report.json  for an aligned narration, how sure the aligner was of each line
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

Lines the aligner cannot hear: MMS hears no letters in a chorus Lyria sings as a stack of voices (the five songs
of Nov 1: their verses read back nearly word for word, their choruses as blank). With nothing to go on, the
aligner crammed a chorus's lines into a second or two, and the singer sang on for 6 to 15 s over no line, the
hook cut ending with them. So where lines the aligner did not hear (LOW_SCORE) are crammed (SQUEEZED_S) before
GAP_S of singing with no line, they are spread over themselves and that singing, each word in proportion to its
letters (by_the_singing). The report's `spread` and the summary line name them.

Whisper, consulted: MMS is a speech aligner, and it hears no letters in a chorus sung in long notes, nor in a
line the singer sang again or not at all (Nov 4's Silk Road song: every chorus line under LOW_SCORE, a chorus line
sung three times, a bridge line skipped, the karaoke 4 to 7 s off). When MMS is unsure of any line, faster-whisper
(WHISPER_MODEL, on this machine) transcribes the vocals with a time for each word, and the lyrics are matched to what
it heard (by_whisper), word by word and in order, each word to one it sounds like (WORD_MATCH). The lines MMS heard
well (KEEP_SCORE) keep MMS's timing, which is finer, and fence in where the others may be found; a line MMS was less
sure of takes Whisper's timing when Whisper heard enough of its words (FOUND_SHARE), and a line sung again takes the
time it is heard best (of times heard as well, the first). Whisper's words over no singing (it makes up lyrics over
silence) are left out. A line neither of them heard, between two lines Whisper heard sung one after the other with
no room for its words, was not sung: it is marked so, never shown in the karaoke, and named in the summary; the
song goes on without it (a song need not sing every line, the owner, Nov 4). Whisper is the aligner's backup: when
faster-whisper is missing the step installs it (install_whisper()), and a model that will not load is loaded once more; only when that fails too is the song timed
without it, and the step's line says WHISPER UNAVAILABLE, so the operator's record shows it. The report's `by_whisper`
and `skipped` name the lines.

The narration as read: a Gemini narration is held to its lines as the song is to its lyrics. When the
aligner cannot hear more than MISMATCH_SHARE of them, the voice did not read what was written (it
skipped or cut short the text), so the step fails and the narration is deleted, and the retry reads it
again.

The lesson alone (STUDIO_VIDEOS=lesson, studio/media.py's lesson_only()): there is no song,
so only the narration is timed, and Demucs never runs (the aligner only for a Gemini narration).

torch, torchaudio and demucs are imported only when the song is aligned, so the rest
of the studio (and its tests) load without them. Both models run on this machine, so
calls.jsonl logs them with their time and no cost (studio/ledger.py).
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import importlib
import importlib.util
import json
import re
import statistics
import subprocess
import sys
import threading
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
SONG_UNTIMED = "the song could not be timed"  # its aligner or Demucs failed on it (studio/anneal.py)


def speech_report(kind: str) -> str:
    """How sure the aligner was of an aligned narration's lines: timing/narration_report.json."""
    return f"timing/{kind}_report.json"


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
# Spreading unheard lines over the singing (by_the_singing): a frame within SINGING_DB of the loud singing is sung (a
# Lyria song's vocals between sections sit 30 to 38 dB down, at QUIET_DB's edge); GAP_S of singing between two
# lines is singing with no line (a breath, a held note and its tail took up to 2 s between verse lines); no singing for
# PAUSE_S parts the song, so no line is spread across an instrumental break; and an unheard line sung in under
# SQUEEZED_S a word was crammed (on Nov 1 the first crammed line of each run took 0.11 to 0.26 s a word, the unheard
# lines found where they are sung 0.3 s and more).
SINGING_DB = 20.0
GAP_S = 2.0
PAUSE_S = 2.0
SQUEEZED_S = 0.28
# Whisper, consulted (by_whisper): a lyric word and a heard one match when their spellings are WORD_MATCH alike
# (difflib's ratio; Whisper heard the Nov 4 chorus's "Carry silk far, bring paper home" as "Kerry's still far, ring
# paper hone"). A line MMS heard at KEEP_SCORE or better keeps its timing, and Whisper's words for it are looked for
# within ANCHOR_S of it. A line takes Whisper's timing when FOUND_SHARE of its words were heard, with fewer than
# REPEAT_WORDS heard words between two of them beyond the lyric words between them (more: the line sung again, and
# the time it is heard best is kept). A heard word is sung when half its frames are within SINGING_DB of the loud
# singing. A word of a line Whisper found that it misheard is timed by the heard words around the ones it matched (as
# many as the words to time, and MISHEARD_EXTRA more), or else takes PACE_S at most before or after them.
WHISPER_MODEL = "small"  # faster-whisper's; int8 on CPU, 30 to 45 s for a three-minute song (Nov 4)
WORD_MATCH = 0.5
KEEP_SCORE = 0.5
ANCHOR_S = 1.0
FOUND_SHARE = 0.5
REPEAT_WORDS = 3
MISHEARD_EXTRA = 2
PACE_S = 0.3
SKIP_LYRIC = -0.5  # the match's cost of a lyric word Whisper did not hear
SKIP_HEARD = -0.3  # and of a word it heard that is not in the lyrics (an ad-lib, a line sung again)
SKIP_INSIDE = -0.6  # and of one between two words of the same line
SAMPLE_RATE = 16000
CHUNK_S = 30  # the aligner hears a longer window this much at a time (_emission)
CONTEXT_S = 5  # with this much of the audio either side of each piece
DEMUCS_MODEL = "htdemucs"
MMS_MODEL = "mms-fa"  # torchaudio.pipelines.MMS_FA, Meta's forced aligner
# Characters around a word that are not part of it ("word," -> "word").
_EDGES = "\"'.,;:!?()[]{}<>“”‘’«»…—–-*_/\\|"

TURN = media.CPU_TURN  # beside the jobs: the jobs on one machine time their songs, and render, in turn


@contextlib.contextmanager
def cpu_turn(job: Path):
    """The machine's turn at its cores (media.cpu_turn()), shared with the render."""
    with media.cpu_turn(job):
        yield


# align(vocals at 16 kHz, a list of normalised words) -> per word (start s, end s, score),
# in the window's own time. Tests pass their own.
Aligner = Callable[[object, list[str]], list[tuple[float, float, float]]]
# hear(vocals at 16 kHz) -> each word Whisper heard, (start s, end s, word), in song time. Tests pass their own.
Hearer = Callable[[object], list[tuple[float, float, str]]]


def run(job: Path, ask=brain.ask) -> str:
    try:
        return _run(job)
    finally:
        _mms.cache_clear()  # the aligner's 1.2 GB is the step's alone, not the worker's for good
        _whisper.cache_clear()  # and so is Whisper's


def _run(job: Path) -> str:
    bundle = jobs.read(job / "bundle.json", Bundle)
    if not audio_step.is_done(job):
        raise media.StepFailed("the audio step has not made every audio file from this bundle yet")
    with_song = "song" in media.timed(job)
    # Not done until each is written again; the cached vocals are kept.
    media.restart(job, *(media.timing_path(kind) for kind in media.timed(job)), *([REPORT] if with_song else []),
                  *(speech_report(kind) for kind in audio_step.SPEECH))
    parts = []
    for kind in audio_step.SPEECH:
        try:
            timing, report = speech_timing(job, kind)
        except (ValueError, KeyError, IndexError) as e:
            raise media.StepFailed(f"the {kind}'s character timings do not fit its lines: {e}") from e
        _check(timing, [i for i, _ in audio_step.speech_lines(bundle, kind)], kind)
        (job / "timing").mkdir(exist_ok=True)
        if report is not None:
            jobs.write(job / speech_report(kind), report)
            mismatch = speech_mismatch(kind, report)
            if mismatch:
                # Read again on the retry (the audio step makes only what is missing), never timed as it is.
                (job / media.audio_path(kind)).unlink(missing_ok=True)
                raise media.StepFailed(f"{mismatch}; the {kind} is deleted, so the retry reads it again",
                                       code=f"the {kind} did not read its lines")
        jobs.write(job / media.timing_path(kind), timing)
        parts.append(f"{kind} {len(timing.lines)} lines" + (f", aligned ({len(report['unsure'])} unsure)"
                                                            if report is not None else ""))
    if not with_song:
        return "no song (the lesson video alone); " + ", ".join(parts)
    # Whatever goes wrong from here is the song's alone, with the narration timed: the videos can go on without the
    # song and make the lesson video (studio/anneal.py).
    try:
        with cpu_turn(job):
            timing, report = song_timing(job, bundle)
        _check(timing, [line.id for line in bundle.lyrics], "song")
    except media.StepFailed as e:
        e.part = "song"
        raise
    except Exception as e:  # a timing out of order, an aligner that could not fit the words, or a bug in either
        raise media.StepFailed(f"the song could not be aligned: {type(e).__name__}: {e}", code=SONG_UNTIMED,
                               part="song") from e
    jobs.write(job / REPORT, report)  # kept either way: `show` and the summary say what was heard
    mismatch = lyric_mismatch(report)
    if mismatch:
        # Made again on the retry (the audio step makes only what is missing), never timed and published as it is.
        (job / media.audio_path("song")).unlink(missing_ok=True)
        raise media.StepFailed(f"{mismatch}; the song is deleted, so the retry sings it again",
                               code="the song did not sing its lyrics", part="song")
    jobs.write(job / media.timing_path("song"), timing)
    return f"song {len(timing.lines)} lines in {timing.duration:.1f}s, {song_notes(report)}; " + ", ".join(parts)


def is_done(job: Path) -> bool:
    """Every Timing is there, times the bundle's lines as they now stand, and is newer than its audio."""
    try:
        bundle = jobs.read(job / "bundle.json", Bundle)
        expected = {"song": [line.id for line in bundle.lyrics],
                    "narration": [i for i, _ in audio_step.speech_lines(bundle, "narration")]}
        for kind in media.timed(job):
            path = job / media.timing_path(kind)
            if timing_problems(jobs.read(path, Timing), expected[kind]):
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


# --- the narration: the TTS character timings, or the aligner -------------------------------


def speech_timing(job: Path, kind: str, align: Aligner | None = None, audio=None) -> tuple[Timing, dict | None]:
    """The narration's Timing, and for one aligned here its report (None from character timings). `align` and
    `audio` stand in for MMS and the decoded narration in tests."""
    made = json.loads((job / audio_step.plan_path(kind)).read_text(encoding="utf-8"))
    length = audio_step.duration(job / media.audio_path(kind))
    if "alignment" in made:  # ElevenLabs said when it read each character
        return timing_from_chars(kind, made["text"], made["lines"], made["alignment"], length), None
    texts = {line_id: text for line_id, text in made["source"]}
    line_ids = [line_id for line_id, _ in made["source"]]
    try:
        with cpu_turn(job):
            aligning = None if align else time.monotonic()
            if audio is None:
                audio = _speech_audio(job / media.audio_path(kind))
            aligned = _align_lines(audio, align or mms_aligner(), texts, line_ids, 0.0, length, 0.0, length)
            if aligning is not None:
                _log_local(job, f"{kind} alignment", MMS_MODEL, aligning)
    except (ValueError, RuntimeError) as e:  # too little audio for its words, or an aligner that could not fit them
        raise media.StepFailed(f"the {kind} could not be aligned: {e}") from e
    timing = Timing(audio=media.audio_path(kind), duration=round(length, 3), lines=[line for line, _ in aligned])
    rows = [{"line_id": line.line_id, "text": texts[line.line_id], "seconds": round(line.end - line.start, 3),
             "score": score} for line, score in aligned]
    return timing, {"lines": rows,
                    "unsure": [r["line_id"] for r in rows if r["score"] is not None and r["score"] < LOW_SCORE]}


def speech_mismatch(kind: str, report: dict) -> str | None:
    """Why an aligned narration is not its lines (more than MISMATCH_SHARE of them unheard), or None."""
    scored = [row for row in report.get("lines", []) if row.get("score") is not None]
    if not scored or len(report["unsure"]) <= MISMATCH_SHARE * len(scored):
        return None
    unsure = report["unsure"]
    return (f"the {kind} does not read its lines: the aligner could not hear {len(unsure)} of its {len(scored)} "
            f"({', '.join(unsure[:8])}{', ...' if len(unsure) > 8 else ''})")


def _speech_audio(path: Path):
    """A narration at the aligner's 16 kHz mono, as a (1, samples) tensor."""
    import torch

    return torch.from_numpy(_decode(path, SAMPLE_RATE, 1))


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


def song_timing(job: Path, bundle: Bundle, align: Aligner | None = None, vocals=None,
                hear: Hearer | None = None) -> tuple[Timing, dict]:
    """The song's Timing and its report. `align`, `vocals` and `hear` stand in for MMS, Demucs and Whisper in tests;
    a test that stands in for MMS and not for Whisper has no Whisper."""
    real = align is None  # this machine's aligner, not a test's stand-in: its Whisper too, installed or not
    whisper = hear or (whisper_hearer() if real else None)
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

    levels = vocal_levels(vocals)
    pinned = plan.get("sections_pinned", True)
    skipped = left_out(plan, texts)
    lines, scores = [], {}
    for section in windows(plan):
        start = section["start_ms"] * scale / 1000
        end = min(length, (section["start_ms"] + section["duration_ms"]) * scale / 1000)
        previous = lines[-1].end if lines else 0.0
        if pinned and levels is not None and sung_seconds(levels, start, end) < SUNG_S:
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
    if aligning is not None:
        _log_local(job, "song alignment", MMS_MODEL, aligning)
    found, missed, fitted = [], [], []
    unsure = any(s is not None and s < LOW_SCORE for s in scores.values())
    missing = ""  # why Whisper did not hear a song that needed it, for the report and the step's line
    if unsure and whisper is None and real and levels is not None:
        missing = WHISPER_MISSING
    if unsure and whisper is not None and levels is not None:
        hearing = time.monotonic()
        try:
            heard = whisper(vocals)
        except Exception as e:  # noqa: BLE001 (its model would not load twice, or it failed: timed without it)
            missing = f"Whisper failed: {type(e).__name__}: {e}"
            print(f"timing: {missing}; the song is timed without it", file=sys.stderr)
            heard = None
        if heard is not None:
            if hear is None:
                _log_local(job, "song lyrics heard", f"whisper-{WHISPER_MODEL}", hearing)
            found, missed, fitted = by_whisper(lines, scores, heard, texts, levels, length)
    spread = fitted + [i for i in by_the_singing(lines, scores, levels, placed=set(found)) if i not in fitted]
    timing = Timing(audio=media.audio_path("song"), duration=round(length, 3), lines=lines)
    return timing, {**song_report(timing, texts, scores), "realigned": realigned, "by_whisper": found,
                    "skipped": missed, "spread": spread, **({"whisper_missing": missing[:300]} if missing else {})}


def by_whisper(lines: list[TimedLine], scores: dict[str, float | None], heard: list[tuple[float, float, str]],
               texts: dict[str, str], levels: list[float],
               length: float) -> tuple[list[str], list[str], list[str]]:
    """The lyrics matched to the words Whisper heard (the module's docstring), in place: the ids of the lines it timed,
    of the lines the singer skipped (marked not sung), and of the lines neither placed that no longer fit where MMS
    put them, spread over the singing between the lines placed either side. The lines MMS heard at KEEP_SCORE keep
    their timing."""
    import numpy as np

    sung = np.asarray(levels) >= -SINGING_DB

    def singing(start: float, end: float) -> bool:  # half the word's frames are sung: not made up over silence
        frames = sung[int(start / FRAME_S):max(int(start / FRAME_S) + 1, int(np.ceil(end / FRAME_S)))]
        return bool(frames.size) and frames.mean() >= 0.5

    said = sorted((start, end, spoken(word)) for start, end, word in heard
                  if spoken(word) and end >= start and singing(start, end))
    if not said:
        return [], [], []

    def kept(k: int) -> bool:  # MMS heard it well: its timing stays
        score = scores.get(lines[k].line_id)
        return score is None or score >= KEEP_SCORE

    sung_lines = [k for k, line in enumerate(lines) if line.sung]
    # Each lyric word Whisper may have heard, and where: near MMS's time for a line it keeps, else between those.
    lyric: list[tuple[int, int, str, float, float]] = []  # (line, word, spelled, from, to)
    before = 0.0
    for n, k in enumerate(sung_lines):
        if kept(k):
            for i, w in enumerate(lines[k].words):
                if spoken(w.word):
                    lyric.append((k, i, spoken(w.word), w.start - ANCHOR_S, w.end + ANCHOR_S))
            before = lines[k].end
            continue
        after = next((lines[q].start for q in sung_lines[n + 1:] if kept(q)), length)
        for i, w in enumerate(lines[k].words):
            if spoken(w.word):
                lyric.append((k, i, spoken(w.word), before - ANCHOR_S, after + ANCHOR_S))
    match = _match_words(lyric, said)

    # The words Whisper heard of each line, as one run: a run REPEAT_WORDS heard words more than the lyric words
    # between them from the rest is the line sung again (a held note, or words misheard, between two words is not).
    words_of_line: dict[int, list[tuple[int, int]]] = {}
    for a, b in sorted(match.items()):
        words_of_line.setdefault(lyric[a][0], []).append((lyric[a][1], b))
    for k, got in words_of_line.items():
        runs = [[got[0]]]
        for (i, b), (i0, b0) in zip(got[1:], got):
            if (b - b0) - (i - i0) >= REPEAT_WORDS:
                runs.append([])
            runs[-1].append((i, b))
        words_of_line[k] = max(runs, key=len)  # the first, of runs as long
    sayable = {k: sum(1 for w in lines[k].words if spoken(w.word)) for k in sung_lines}
    found = [k for k in sung_lines if not kept(k) and sayable[k]
             and len(words_of_line.get(k, ())) >= max(1, int(np.ceil(FOUND_SHARE * sayable[k])))]
    for n, k in enumerate(sung_lines):
        if k not in found:
            continue
        # From the last line placed (MMS heard it well, or Whisper found it): a line before it that neither placed
        # may be where MMS crammed it, too late, and is fitted in after.
        lo = next((lines[q].end for q in reversed(sung_lines[:n]) if kept(q) or q in found), 0.0)
        hi = next((lines[q].start for q in sung_lines[n + 1:] if kept(q)), length)
        heard_at = {i: said[b][:2] for i, b in words_of_line[k]}
        # The line's first and last words Whisper misheard: the heard words next to the ones it matched that no line
        # took, as many as there are words to time and MISHEARD_EXTRA more (a mishearing has its own count of
        # words), are where they were sung.
        (first, b0), (last, b1) = words_of_line[k][0], words_of_line[k][-1]
        taken = set(match.values())
        lead = [b for b in range(b0 - 1, max(-1, b0 - 1 - first - MISHEARD_EXTRA), -1) if b not in taken and said[b][0] >= lo]
        lead = lead[:next((n for n, b in enumerate(lead) if b + n + 1 != b0), len(lead))]
        tail = [b for b in range(b1 + 1, min(len(said), b1 + len(lines[k].words) - last + MISHEARD_EXTRA)) if b not in taken
                and said[b][1] <= hi]
        tail = tail[:next((n for n, b in enumerate(tail) if b - n - 1 != b1), len(tail))]
        words = _fill(lines[k].words, heard_at, lo, max(lo, hi), said[lead[-1]][0] if lead else None,
                      said[tail[-1]][1] if tail else None)
        lines[k] = lines[k].model_copy(update={"start": words[0].start, "end": words[-1].end, "words": words})

    # A line neither heard, between two lines Whisper heard sung one after the other, with no room for its words.
    skipped = []
    for n, k in enumerate(sung_lines):
        if k in found or kept(k) or (scores.get(lines[k].line_id) or 0.0) >= LOW_SCORE or not sayable[k]:
            continue
        p = next((q for q in reversed(sung_lines[:n]) if q in words_of_line), None)
        q = next((q for q in sung_lines[n + 1:] if q in words_of_line), None)
        if p is None or q is None:
            continue
        between = [m for m in sung_lines if p < m < q]
        if any(m in found for m in between):
            continue
        last, first = words_of_line[p][-1][1], words_of_line[q][0][1]
        t0, t1 = said[last][1], said[first][0]
        room = float(sung[int(t0 / FRAME_S):max(int(t0 / FRAME_S), int(t1 / FRAME_S))].sum()) * FRAME_S
        if first == last + 1 and room < SQUEEZED_S * sum(sayable[m] for m in between):
            skipped.append(k)
    for k in skipped:
        lines[k] = _unsung(lines[k].line_id, texts, lines[k - 1].end if k else 0.0)
        scores[lines[k].line_id] = None
    # The lines neither placed, each run between two lines placed: left where MMS put them when they fit between,
    # else spread over the singing there (or, with none, at no length where the line placed before them ends).
    fitted, run = [], []
    for k in [*sung_lines, None]:
        if k is not None and not (kept(k) or k in found or k in skipped):
            run.append(k)
            continue
        if run:
            t0 = next((lines[q].end for q in reversed(sung_lines) if q < run[0] and (kept(q) or q in found)), 0.0)
            t1 = lines[k].start if k is not None else length
            if any(lines[m].start < t0 or lines[m].end > t1 for m in run):
                moved = _spread(lines, run, t0, max(t0, t1), sung)
                for m in (m for m in run if lines[m].line_id not in moved):
                    at = round(t0, 3)
                    lines[m] = lines[m].model_copy(update={"start": at, "end": at, "words": [
                        Word(word=w.word, start=at, end=at) for w in lines[m].words]})
                fitted += [lines[m].line_id for m in run]
            run = []
    _in_order(lines, length)
    return [lines[k].line_id for k in found], [lines[k].line_id for k in skipped], fitted


def _alike(word: str, heard: str) -> float:
    """How alike two spellings are (difflib's ratio); a word of one or two letters only as itself ("far" is not "a")."""
    if min(len(word), len(heard)) <= 2:
        return float(word == heard)
    return SequenceMatcher(None, word, heard, autojunk=False).ratio()


def _match_words(lyric: list[tuple[int, int, str, float, float]], said: list[tuple[float, float, str]]) -> dict[int, int]:
    """The lyric words matched in order to the words heard (index to index): each pair WORD_MATCH alike, and the
    heard word's middle where the lyric word may be; the best total, a lyric word not heard costing SKIP_LYRIC and a
    heard word not in the lyrics SKIP_HEARD, or SKIP_INSIDE between two words of one line, so that a line sung again
    is matched to one time it is sung rather than to words of each. Of matches as good, the earlier heard words are
    kept."""
    n, m = len(lyric), len(said)
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]  # 0: matched, 1: the lyric word not heard, 2: the heard word extra
    for j in range(1, m + 1):
        best[0][j], move[0][j] = best[0][j - 1] + SKIP_HEARD, 2
    for i in range(1, n + 1):
        best[i][0], move[i][0] = best[i - 1][0] + SKIP_LYRIC, 1
        _, _, word, lo, hi = lyric[i - 1]
        # A heard word between lyric words i - 1 and i (0-based i - 1 and i) of one line.
        extra = SKIP_INSIDE if i < n and lyric[i][0] == lyric[i - 1][0] else SKIP_HEARD
        for j in range(1, m + 1):
            # A heard word left over wins a tie, so that, read back from the end, the earlier words are matched.
            got, how = best[i][j - 1] + extra, 2
            start, end, heard = said[j - 1]
            if lo <= (start + end) / 2 <= hi:
                alike = _alike(word, heard)
                if alike >= WORD_MATCH and best[i - 1][j - 1] + 2 * alike - 1 > got + 1e-9:  # summed in another order
                    got, how = best[i - 1][j - 1] + 2 * alike - 1, 0
            if best[i - 1][j] + SKIP_LYRIC > got + 1e-9:
                got, how = best[i - 1][j] + SKIP_LYRIC, 1
            best[i][j], move[i][j] = got, how
    match, i, j = {}, n, m
    while i or j:
        how = move[i][j]
        if how == 0:
            match[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    return match


def _fill(words: list[Word], heard: dict[int, tuple[float, float]], lo: float, hi: float,
          begin: float | None = None, finish: float | None = None) -> list[Word]:
    """A line's words timed: those in `heard` (by index) where Whisper heard them, the others shared by their letters
    between them, or before the first heard from `begin` and after the last to `finish` (where the words it misheard
    were), else over up to PACE_S a word; all within lo and hi."""
    n = len(words)
    times: list[tuple[float, float] | None] = [None] * n
    at = lo
    for i in sorted(heard):
        start = min(max(heard[i][0], at), hi)
        end = min(max(heard[i][1], start), hi)
        times[i], at = (start, end), end
    known = sorted(heard)

    def share(ks: list[int], t0: float, t1: float) -> None:
        letters = [len(spoken(words[k].word)) for k in ks]
        total, done = sum(letters), 0
        t1 = max(t0, t1)
        for k, count in zip(ks, letters):
            a = t0 + (t1 - t0) * done / total if total else t0
            done += count
            times[k] = (a, t0 + (t1 - t0) * done / total if total else t0)

    if known[0]:
        first = times[known[0]][0]
        share(list(range(known[0])), max(lo, first - PACE_S * known[0] if begin is None else begin), first)
    for a, b in zip(known, known[1:]):
        if b > a + 1:
            share(list(range(a + 1, b)), times[a][1], times[b][0])
    if known[-1] < n - 1:
        last = times[known[-1]][1]
        share(list(range(known[-1] + 1, n)), last,
              min(hi, last + PACE_S * (n - 1 - known[-1]) if finish is None else finish))
    return [Word(word=w.word, start=round(t[0], 3), end=round(t[1], 3)) for w, t in zip(words, times)]


def _in_order(lines: list[TimedLine], length: float) -> None:
    """Each word, in place, starting no earlier than the one before it ends; a line not sung where the line before it
    ends."""
    at = 0.0
    for k, line in enumerate(lines):
        if not line.sung:
            if line.start < at:
                lines[k] = line.model_copy(update={"start": round(at, 3), "end": round(at, 3), "words": [
                    Word(word=w.word, start=round(at, 3), end=round(at, 3)) for w in line.words]})
            continue
        words = []
        for w in line.words:
            start = min(max(w.start, at), length)
            end = min(max(w.end, start), length)
            words.append(Word(word=w.word, start=round(start, 3), end=round(end, 3)))
            at = end
        if words != line.words:
            lines[k] = line.model_copy(update={"start": words[0].start, "end": words[-1].end, "words": words})


@functools.cache
def _whisper():
    """faster-whisper's WHISPER_MODEL on this machine's CPU, loaded once for the step."""
    from faster_whisper import WhisperModel

    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def whisper_hearer() -> Hearer | None:
    """Whisper as the step hears with it. With faster-whisper missing it is installed here first (install_whisper());
    None only when that fails too, and the song's report says so (WHISPER_MISSING)."""
    if not install_whisper():
        return None

    def hear(vocals) -> list[tuple[float, float, str]]:
        import numpy as np

        audio = np.asarray(vocals, dtype=np.float32).reshape(-1)
        # Temperature 0 alone: the same song is heard the same way each time (the fallback's sampling at higher
        # temperatures heard the Nov 4 song's second chorus differently run to run).
        segments, _ = whisper_model().transcribe(audio, language="en", word_timestamps=True, vad_filter=False,
                                                 condition_on_previous_text=False, temperature=0.0)
        return [(w.start, w.end, w.word.strip()) for segment in segments for w in (segment.words or [])]

    return hear


# Whisper is the aligner's backup, not an extra: without it a line MMS cannot hear is only spread over the singing,
# and a song with many such lines is sung again or let go of. The session hook installs faster-whisper and its model
# (.claude/hooks/session-start.sh), but on Nov 4 its install had failed since Nov 2 (a download cut short failed the
# whole requirements file), so faster-whisper was missing from every session. The step mends that itself, once a
# process: it installs faster-whisper from requirements.txt's own line, and loads a model that would not load once more
# after a pause. What it cannot mend, the song's report and the step's line say (song_notes()).
WHISPER_MISSING = "faster-whisper is not installed here and could not be installed"
INSTALL_TIMEOUT_S = 600
WHISPER_RETRY_S = 10.0
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
_installing = threading.Lock()
_installed: bool | None = None  # whether faster-whisper imports here, once install_whisper() has looked


def install_whisper() -> bool:
    """Whether faster-whisper imports on this machine, installing it first when it does not (once a process)."""
    global _installed
    with _installing:
        if _installed is None:
            _installed = _importable("faster_whisper") or _pip_install(requirement("faster-whisper"))
        return _installed


def requirement(name: str) -> str:
    """The package's line in studio/requirements.txt, its version range with it ("faster-whisper>=1.2.1,<2")."""
    wanted = re.sub(r"[-_.]+", "-", name).lower()
    try:
        lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return name
    for line in lines:
        spec = line.split("#", 1)[0].strip()
        found = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec)
        if found and re.sub(r"[-_.]+", "-", found.group(0)).lower() == wanted:
            return spec
    return name


def _importable(module: str) -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec(module) is not None


def _pip_install(spec: str) -> bool:
    """pip install the one requirement, with no cache (a download cut short is cached as it came), tried twice."""
    for attempt in (1, 2):
        try:
            done = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir", spec],
                                  capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
            if done.returncode == 0 and _importable("faster_whisper"):
                print(f"timing: {spec} was not installed here, so the step installed it", file=sys.stderr)
                return True
            why = (done.stderr or done.stdout).strip().splitlines()[-1:] or [f"pip exited {done.returncode}"]
        except (OSError, subprocess.SubprocessError) as e:
            why = [f"{type(e).__name__}: {e}"]
        print(f"timing: could not install {spec} (try {attempt} of 2): {why[0]}", file=sys.stderr)
        if attempt == 1:
            time.sleep(WHISPER_RETRY_S)
    return False


def whisper_model():
    """_whisper(), loaded once more after a pause when it would not load (a model download cut short, a rate limit)."""
    try:
        return _whisper()
    except Exception as e:  # noqa: BLE001 (raised again below if the second load fails too)
        print(f"timing: Whisper's model would not load, so it is loaded once more: {type(e).__name__}: {e}",
              file=sys.stderr)
        _whisper.cache_clear()
        time.sleep(WHISPER_RETRY_S)
        return _whisper()


def by_the_singing(lines: list[TimedLine], scores: dict[str, float | None], levels: list[float] | None,
                   placed: set[str] = frozenset()) -> list[str]:
    """Spread the lines the aligner did not hear over the singing they leave with no line (the module's docstring),
    in place; the ids of the sung lines it moved. The lines it heard, and each pause of PAUSE_S, stay where they are
    and part the song into stretches. In a stretch where two lines have GAP_S of singing between them, the lines from
    the first one crammed (under SQUEEZED_S a word) to the last such gap are spread over the stretch up to that gap's
    end: the aligner crams what it cannot hear right after what it last heard, and finds its place again by the next
    line it hears (the five songs of Nov 1). Singing with no line and no crammed line before it is left: Lyria is
    asked for echoes of the hook, and a line it did not hear but placed at a pace that sings stays where it is.
    The lines in `placed` (found by Whisper) count as heard."""
    import numpy as np

    if levels is None:
        return []
    sung = np.asarray(levels) >= -SINGING_DB

    def heard(line: TimedLine) -> bool:
        return (scores.get(line.line_id) or 0.0) >= LOW_SCORE or line.line_id in placed

    def crammed(line: TimedLine) -> bool:  # sung (it has a score), in under SQUEEZED_S a word
        return scores.get(line.line_id) is not None and line.end - line.start < SQUEEZED_S * len(line.words)

    quiet = np.diff(np.concatenate([[0], (~sung).astype(np.int8), [0]]))
    pauses = [(a + b) / 2 * FRAME_S for a, b in zip(np.flatnonzero(quiet == 1), np.flatnonzero(quiet == -1))
              if (b - a) * FRAME_S >= PAUSE_S]
    end = len(sung) * FRAME_S
    fixed = sorted([(line.start, line.end) for line in lines if heard(line)] + [(t, t) for t in pauses] + [(end, end)])
    moved, reach = [], 0.0
    for (_, done), (t1, _) in zip([(0.0, 0.0), *fixed], fixed):
        reach = max(reach, done)
        group = [k for k, line in enumerate(lines) if not heard(line) and reach <= (line.start + line.end) / 2 < t1]
        if not group:
            continue
        t0 = max(reach, lines[group[0] - 1].end if group[0] else 0.0)
        t1 = min(t1, lines[group[-1] + 1].start if group[-1] + 1 < len(lines) else t1)
        marks = [t0, *(t for k in group for t in (lines[k].start, lines[k].end)), t1]
        gaps = [i for i, (a, b) in enumerate(zip(marks[::2], marks[1::2]))  # gap i is the one before group[i]
                if sung[int(a / FRAME_S):int(b / FRAME_S)].sum() * FRAME_S >= GAP_S]
        last = gaps[-1] if gaps else 0
        first = next((i for i in range(last) if crammed(lines[group[i]])), None)
        if first is not None:  # from the first line crammed to the last gap's end
            moved += _spread(lines, group[first:last], lines[group[first - 1]].end if first else t0,
                             marks[2 * last + 1], sung)
    return moved


def _spread(lines: list[TimedLine], ks: list[int], t0: float, t1: float, sung) -> list[str]:
    """Lines `ks` spread, in place, over the singing between t0 and t1, each word over as many of its frames as its
    share of the letters; a line not sung, or a word the aligner cannot spell, at no length. The ids of those sung."""
    import numpy as np

    f0 = int(np.ceil(t0 / FRAME_S))
    frames = np.flatnonzero(sung[f0:int(t1 / FRAME_S)]) + f0
    words = [(k, word) for k in ks for word in lines[k].words]
    letters = np.array([len(spoken(word.word)) if lines[k].sung else 0 for k, word in words], dtype=float)
    if not len(frames) or not letters.sum():
        return []
    cuts = np.concatenate([[0.0], np.cumsum(letters)]) * len(frames) / letters.sum()
    placed: dict[int, list[Word]] = {k: [] for k in ks}
    at = t0
    for (k, word), a, b, n in zip(words, cuts, cuts[1:], letters):
        start = stop = at
        if n:  # from its first frame to its last
            first = min(int(a), len(frames) - 1)
            start = max(at, frames[first] * FRAME_S)
            stop = max(start, (frames[min(max(int(np.ceil(b)) - 1, first), len(frames) - 1)] + 1) * FRAME_S)
        placed[k].append(Word(word=word.word, start=round(start, 3), end=round(stop, 3)))
        at = stop
    for k in ks:
        lines[k] = lines[k].model_copy(update={"start": placed[k][0].start, "end": placed[k][-1].end,
                                               "words": placed[k]})
    return [lines[k].line_id for k in ks if lines[k].sung]


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
    """Why the song is not its lyrics, or None: more than MISMATCH_SHARE of its sung lines were sung differently
    (neither MMS nor Whisper heard them). A line the singer skipped does not fail the song (the owner, Nov 4: a song
    need not be that strict; that day's Calvin cycle threw four songs away for one skipped chorus line each): it is
    marked not sung, the karaoke never shows it, and the summary names it (SKIPPED by the singer)."""
    scored = [row for row in report.get("lines", []) if row.get("score") is not None and row.get("sung", True)]
    heard = set(report.get("by_whisper") or ())
    unsure = [row["line_id"] for row in scored if row["score"] < LOW_SCORE and row["line_id"] not in heard]
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
    if report.get("by_whisper"):  # nor one from before Whisper was consulted
        notes.append(f"timed by Whisper: {', '.join(report['by_whisper'])}")
    if report.get("whisper_missing"):  # the backup was needed and not there: the operator's record says so
        notes.append(f"WHISPER UNAVAILABLE ({report['whisper_missing']}): the lines the aligner could not hear "
                     f"were not heard again")
    if report.get("skipped"):
        notes.append(f"SKIPPED by the singer: {', '.join(report['skipped'])}")
    if report.get("spread"):  # nor one from before lines were spread over the singing
        notes.append(f"spread over the singing: {', '.join(report['spread'])}")
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


@functools.cache
def _mms():
    """MMS's model, tokenizer and aligner, loaded once for the step: a Gemini narration and the song share them (each
    loaded its own, 9 s once the file is cached). run() lets them go when it is done."""
    from torchaudio.pipelines import MMS_FA

    model = MMS_FA.get_model(with_star=True)
    model.eval()
    return model, MMS_FA.get_tokenizer(), MMS_FA.get_aligner()


def mms_aligner() -> Aligner:
    """MMS forced alignment (torchaudio.pipelines.MMS_FA), with a <star> at each end of the
    window to soak up the neighbouring sections' lines that the margins let in.

    Each window is heard afresh, and that is the point: a line the aligner was unsure of, heard again alone between
    its neighbours, sounds different to it from the same stretch in a longer hearing. Reading every window from one
    hearing of the whole song (Oct 31) was a third faster, and re-timed none of the 20 lines that hearing them alone
    had re-timed better: unsure lines went from 22 to 27, and 60 words moved over a tenth of a second."""
    import torch

    model, tokenizer, aligner = _mms()

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
