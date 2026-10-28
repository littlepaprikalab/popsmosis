"""A media job whose hook is drawn from the schematic library, for tests and for looking at frames.

    python -m studio.tests.sample_schematic jobs/sample-3d [--stills OUT_DIR]

The sample job (sample_media.py: the light reactions, with tones for audio), with its hook
scene drawn as the ATP synthase schematic and the song opening on it. Its fact-check
supports one way for each of the machine's motions. That way is arbitrary here, not a claim
about ATP synthase: the tests check that what is drawn follows the verdicts, not which way
is right. The art step draws the schematic scene and the timeline step builds the
timelines, so they are what those steps make; the other scenes keep sample_media's art.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from studio import checks, jobs, media
from studio.schematic3d import library
from studio.schemas import Bundle, CheckedLine, FactCheckReport, Locations
from studio.steps import art, timeline
from studio.tests import sample_media
from studio.tests.sample import bundle_data

RECIPE = "atp_synthase"
# The ways the sample's fact-check supports: arbitrary (see above).
SUPPORTED = {f"{RECIPE}.rotor.down-counterclockwise", f"{RECIPE}.rotor.up-clockwise", f"{RECIPE}.protons.forward"}

HOOK_LYRICS = {  # id: (text, names)
    "v1-1": ("Protons turn the rotor ring", ["rotor ring"]),
    "v1-2": ("The rod spins inside the knob", ["rod", "knob"]),
}


def bundle() -> Bundle:
    data = bundle_data()
    hook = next(s for s in data["scenes"] if s["id"] == "hook")
    hook.update(schematic=RECIPE, image_prompt="ATP synthase turning in the thylakoid membrane.", structures=[])
    for line in data["lyrics"]:
        if line["id"] in HOOK_LYRICS:
            line["text"], line["names"] = HOOK_LYRICS[line["id"]]
            line["fact_id"] = "f3"
    data["frq"]["model_answer_steps"][1] = "The flow of protons turns the rotor ring."  # the exam question reviews the hook
    narration = data["narration"][0]
    narration.update(text="The rotor ring of ATP synthase turns as protons flow through it.", names=["rotor ring"],
                     fact_id="f3")
    for d in data["direction"]:
        if d["line_id"] in ("v1-1", "n1"):
            d["scene"] = "hook"
        if d["line_id"] == "v1-2":  # a close-up on the turning ring, revealed by the line before
            d.update(scene="hook", camera={"target": "rotor ring", "zoom": 2.2}, spotlight="rod")
    bundle, _ = checks.review(Bundle.model_validate(data))
    return bundle


def factcheck(the_bundle: Bundle) -> FactCheckReport:
    from studio.steps.factcheck import lines_of

    lines = []
    for line in lines_of(the_bundle):
        if line.kind == "motion":
            verdict, source = ("SUPPORTED", "f3") if line.id in SUPPORTED else ("UNSUPPORTED", "")
        else:
            verdict, source = ("SUPPORTED", "f1")
        lines.append(CheckedLine(kind=line.kind, id=line.id, text=line.text, verdict=verdict, source=source, round=1))
    return FactCheckReport(status="passed", reason="", rounds=1, lines=lines, corrections=[])


def write_job(job: Path) -> Path:
    sample_media.write_job(job, art=True, audio=True)
    the_bundle = bundle()
    jobs.write(job / "bundle.json", the_bundle)
    jobs.write(job / "factcheck.json", factcheck(the_bundle))
    for kind, timing in sample_media.timings(the_bundle).items():
        jobs.write(job / media.timing_path(kind), timing)
    # The hook's own art and masks from sample_media are an ellipse; the art step's schematic replaces them.
    hook = next(s for s in the_bundle.scenes if s.id == "hook")
    located = jobs.read(job / media.LOCATIONS, Locations)
    scenes = [art.draw_schematic(job, the_bundle, hook) if s.scene == "hook" else s for s in located.scenes]
    jobs.write(job / media.LOCATIONS, Locations(scenes=scenes))
    timeline.run(job)
    return job


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m studio.tests.sample_schematic", description=__doc__.split("\n\n")[1])
    parser.add_argument("job", type=Path)
    parser.add_argument("--stills", type=Path, help="also save a still every 2 s of the song here")
    args = parser.parse_args()
    library.load(RECIPE)
    write_job(args.job)
    if args.stills:
        from studio.render.renderer import Renderer
        from studio.schemas import Timeline

        song = jobs.read(args.job / media.timeline_path("song"), Timeline)
        renderer = Renderer(args.job, song)
        args.stills.mkdir(parents=True, exist_ok=True)
        t = 0.0
        while t < song.duration:
            renderer.image(t).save(str(args.stills / f"song_{t:05.1f}s.png"), __import__("skia").kPNG)
            t += 2.0
    print(args.job)
