"""How long a whole droplet takes, with every paid call stood in for.

    python -m studio.bench jobs/bench [--scale 10] [--keep]

Makes one droplet the way the routine does: `run` writes, fact-checks and posts the lesson (to a
stand-in API), then the videos are made as `make` makes them (art and locate beside audio and
timing, then the timeline and the render; the upload is left out, as `make` leaves it out). Every
step is the real one. Only what costs money or runs a model is stood in for: Claude (the bundle,
the fact-check and the helper's looks), Muse Image, SAM 3.1, ElevenLabs (the song and the voice),
and Demucs and the MMS aligner. Each stand-in waits as long as its call took on a live run (WAITS,
in seconds), divided by --scale, and answers with something the next step can use: a picture with
each structure painted where SAM then finds it, real MP3s of the planned length, character
timings, words spread evenly over each sung window.

The render, the timeline and the pixel work in art and locate are not stood in for: they run at
full speed on this machine, so their share is larger here than on a live run (where the waits
are --scale times longer). Prints each step's time from runs.jsonl, the lesson's and the videos'
wall clock, and a digest of every file the steps made, so two runs can be checked byte for byte.
No paid call is made, whatever keys the environment holds.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from studio import brain, checks, jobs, media, muse, svg
from studio import run as operator_run
from studio import videos as operator_videos
from studio.render import bench as render_bench
from studio.schemas import Bundle, FactCheck
from studio.steps import art, audio, timing
from studio.tests.test_run import FakeApi

# Seconds each call takes on a live run. Measured on the job of Oct 29: the bundle with its learner test 8.7 min,
# the fact-check 1.4, Demucs 1.6, the alignment 3.1 (its model's load and about ten windows), locate 1 min (a
# SAM call each structure, the scenes at once). The rest are estimates from docs/test-rounds.md.
WAITS = {
    "bundle": 522.0,
    "factcheck": 84.0,
    "paint": 45.0,  # one Muse Image picture (estimate)
    "art check": 12.0,  # the helper's look at it (estimate)
    "sam": 10.0,  # one SAM 3.1 call
    "song": 150.0,  # Eleven Music, a song of about two and a half minutes (estimate)
    "speech": 30.0,  # the narration's text-to-speech (estimate)
    "demucs": 96.0,
    "mms load": 9.0,  # the aligner's model, from a file the disk has cached (100 s the first time a machine reads it)
    "align": 17.7,  # one window of the MMS aligner
}
QUESTION = "q-bench"
SPEECH_S_PER_CHAR = 0.065  # about 15 characters a second, as ElevenLabs reads
# The overview's structures, each painted as a disc where SAM finds it: (colour, x, y, radius) on the art.
OVERVIEW = {
    "Thylakoid": ("#2E8B57", 350, 300, 130), "Cytochrome b6f": ("#E07A2F", 900, 300, 130),
    "Photosystem II": ("#7B3FA0", 1450, 300, 130), "Photosystem I": ("#C0392B", 350, 750, 130),
    "Plastocyanin": ("#1ABC9C", 900, 750, 130), "Ferredoxin": ("#F1C40F", 1450, 750, 130),
}
SPOTS = {("hook", "Thylakoid"): (896, 512, 300), ("synthase", "ATP synthase"): (896, 512, 250),
         **{("overview", name): spot[1:] for name, spot in OVERVIEW.items()}}
NARRATION = [  # (text, scene): about three minutes read aloud, as the narration's 350 to 450 words are
    ("Light lands on the thylakoid, the stack of membranes inside the chloroplast.", "hook"),
    ("Photosystem II catches that light and pulls electrons from water.", "overview"),
    ("The electrons hop to cytochrome b6f, which pumps protons into the lumen.", "overview"),
    ("Plastocyanin carries them on to Photosystem I, which lifts them again.", "overview"),
    ("Ferredoxin hands them to the enzyme that makes NADPH in the stroma.", "overview"),
    ("The protons crowd the lumen and flow back out through ATP synthase.", "synthase"),
    ("As they pass, the synthase turns and makes ATP from ADP and phosphate.", "synthase"),
    ("So the thylakoid turns light into ATP and NADPH for the Calvin cycle.", "overview"),
]


def wait(what: str, scale: float) -> None:
    time.sleep(WAITS[what] / scale)


# --- the droplet ----------------------------------------------------------------------------------


def bench_bundle() -> Bundle:
    """The render benchmark's song (ten sections of seven lines), with an overview of six structures and a
    narration of about three minutes, so the art, locate, timing and render have a live droplet's work."""
    data = render_bench.bench_bundle().model_dump()
    overview = next(s for s in data["scenes"] if s["id"] == "overview")
    aliases = {s["name"]: s["aliases"] for s in overview["structures"]}
    overview["structures"] = [{"name": name, "aliases": aliases.get(name, []), "sam_phrase": f"the {name.lower()}",
                               "color_hex": colour} for name, (colour, *_) in OVERVIEW.items()]
    canon = checks.canonical_names(data["scenes"])
    narration, direction = [], [d for d in data["direction"] if not d["line_id"].startswith("n")]
    for i in range(5 * len(NARRATION)):
        text, scene = NARRATION[i % len(NARRATION)]
        line_id = f"n{i + 1}"
        narration.append({"id": line_id, "text": text, "names": list(checks.said(text, canon).values()),
                          "fact_id": "filler"})
        direction.append({"line_id": line_id, "scene": scene if i else "hook", "camera": {"target": "", "zoom": 1.0},
                          "spotlight": "", "particles": [], "caption": ""})
    data.update(narration=narration, direction=direction)
    return Bundle.model_validate(data)


class Brain:
    """Stands in for brain.ask: the bench bundle, a fact-check that supports every line, and the helper's look at
    a picture that finds nothing wrong."""

    def __init__(self, scale: float):
        self.scale = scale

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if schema is Bundle:
            wait("bundle", self.scale)
            return bench_bundle().model_dump(mode="json")
        if schema is FactCheck:
            wait("factcheck", self.scale)
            return {"checks": [{"id": line["id"], "verdict": "SUPPORTED", "source": "f1", "problem": "",
                                "corrected": ""} for line in json.loads(prompt)["lines"]]}
        if schema is art.ArtCheck:
            wait("art check", self.scale)
            return {"text": [], "misplaced": [], "wrong": []}
        raise AssertionError(f"the bench has no stand-in for {label!r}")


class Api(FakeApi):
    """The stand-in API, with the videos' claim and reports."""

    def claim_videos(self, answer_id, attempt=None):
        self._call("claim_videos", answer_id, attempt)
        return {"answerId": answer_id, "attempt": 1}

    def report_video_progress(self, answer_id, attempt, progress):
        self._call("report_video_progress", answer_id, attempt)


def painting(scene_id: str) -> bytes:
    """Muse Image's picture of a scene: each structure a disc in its colour, where SAM finds it."""
    ys, xs = np.ogrid[:media.ART_HEIGHT, :media.ART_WIDTH]
    pixels = np.full((media.ART_HEIGHT, media.ART_WIDTH, 3), 255, np.uint8)
    for structure in next(s for s in bench_bundle().scenes if s.id == scene_id).structures:
        x, y, r = SPOTS[(scene_id, structure.name)]
        pixels[(xs - x) ** 2 + (ys - y) ** 2 <= r * r] = tuple(bytes.fromhex(structure.color_hex[1:]))
    return svg.png(pixels)


def tone(seconds: float) -> bytes:
    """An MP3 of a quiet tone this long, so ffprobe, the timing and the render have real audio."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tone.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=330:duration={seconds:.3f}", "-q:a", "9", str(out)], check=True)
        return out.read_bytes()


def stand_in(scale: float) -> None:
    """Put the stand-ins in place of every paid API and local model, for this process."""
    def paint(job, label, prompt, reference=None):
        wait("paint", scale)
        return painting(label.split()[1])

    def segment(job, label, art_path, *, phrase=None, box=None):
        with muse._sam_slots:  # as many at once as the real call lets through
            wait("sam", scale)
        scene, name = label.split()[1], label.split(" ", 2)[2].removesuffix(" box").split(" try ")[0]
        x, y, r = SPOTS[(scene, name)]
        ys, xs = np.ogrid[:media.ART_HEIGHT, :media.ART_WIDTH]
        return [muse.Match((xs - x) ** 2 + (ys - y) ** 2 <= r * r, 0.9)]

    def send(method, url, headers, body):
        request = json.loads(body)
        if "/v1/music" in url:
            wait("song", scale)
            seconds = sum(chunk["duration_ms"] for chunk in request["composition_plan"]["chunks"]) / 1000
            return 200, {"song-id": "bench", "Content-Type": "audio/mpeg"}, tone(seconds)
        wait("speech", scale)
        text = request["text"]
        answer = {"audio_base64": base64.b64encode(tone(len(text) * SPEECH_S_PER_CHAR)).decode(),
                  "alignment": {"characters": list(text),
                                "character_start_times_seconds": [i * SPEECH_S_PER_CHAR for i in range(len(text))],
                                "character_end_times_seconds": [(i + 1) * SPEECH_S_PER_CHAR for i in range(len(text))]}}
        return 200, {"character-cost": str(len(text))}, json.dumps(answer).encode()

    def separate_vocals(job):
        wait("demucs", scale)
        return np.zeros((1, round(audio.duration(job / media.audio_path("song")) * timing.SAMPLE_RATE)), np.float32)

    def mms_aligner():
        wait("mms load", scale)

        def align(window, words):
            wait("align", scale)
            step = window.shape[-1] / timing.SAMPLE_RATE / max(1, len(words))
            return [(i * step, (i + 0.8) * step, 0.9) for i in range(len(words))]
        return align

    muse.paint, muse.segment, audio.send = paint, segment, send
    timing.separate_vocals, timing.mms_aligner = separate_vocals, mms_aligner
    timing._import_models = lambda: None  # the stand-ins need neither Demucs's packages nor the aligner's
    operator_videos.unavailable = lambda job=None, studio=None: None  # no keys here, and none needed


# --- the run ----------------------------------------------------------------------------------------


def digests(job: Path) -> dict[str, str]:
    """A digest of every file the media steps made, to tell two runs' output apart byte for byte."""
    made = [p for pattern in ("art/*.png", "masks/**/*.png", "audio/*.mp3", "timing/*.json", "timeline_*.json",
                              "out/*", media.LOCATIONS) for p in job.glob(pattern)]
    return {str(p.relative_to(job)): hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in sorted(made)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio.bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help=f"a folder for the bench's job, <root>/{QUESTION} (made afresh)")
    parser.add_argument("--scale", type=float, default=10.0, help="each stand-in waits its live time divided by this")
    parser.add_argument("--keep", action="store_true", help="keep the job folder afterwards")
    args = parser.parse_args(argv)
    # Nothing here may reach a paid API, nor the planner's record on Inkmory.
    for name in (*brain.CLAUDE_KEY_VARS, brain.META_KEY_VAR, "GEMINI_API_KEY", "INKMORY_PLANNER_API_KEY"):
        os.environ.pop(name, None)
    os.environ["ELEVENLABS_API_KEY"] = "bench"  # the audio step checks one is set; the stand-in answers
    shutil.rmtree(args.root / QUESTION, ignore_errors=True)  # its own job only: the folder may hold others
    args.root.mkdir(parents=True, exist_ok=True)
    jobs.ROOT = args.root
    stand_in(args.scale)
    api = Api(question={"id": QUESTION, "content": "the light reactions", "status": "PENDING", "answer": None,
                        "attachments": []}, studio={"art": {"claude": "muse"}})
    ask = Brain(args.scale)

    clock = time.monotonic()
    result = operator_run.run(QUESTION, api=api, ask=ask)
    lesson_s = time.monotonic() - clock
    if result[0] != operator_run.DONE:
        print(f"lesson: {result}", file=sys.stderr)
        return 1
    job = jobs.ROOT / QUESTION
    clock = time.monotonic()
    result = operator_videos.local(job, ask=ask)
    videos_s = time.monotonic() - clock
    if result[0] != operator_run.DONE:
        print(f"videos: {result}", file=sys.stderr)
        return 1

    print(f"\nscale {args.scale:g}: each stand-in waits its live time / {args.scale:g}; the code runs at full speed\n")
    for record in operator_run.read_jsonl(job / "runs.jsonl"):
        for s in record["steps"]:
            print(f"  {s['step']:<10} {s['seconds']:7.1f} s   {s.get('detail', '')[:110]}")
    print(f"\n  lesson    {lesson_s:7.1f} s  (posted)")
    print(f"  videos    {videos_s:7.1f} s")
    print(f"  total     {lesson_s + videos_s:7.1f} s\n")
    for path, digest in digests(job).items():
        print(f"  {digest}  {path}")
    if not args.keep:
        shutil.rmtree(job)
    return 0


if __name__ == "__main__":
    sys.exit(main())
