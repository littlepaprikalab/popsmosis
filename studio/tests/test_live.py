"""Live checks against the real brain. They spend Anthropic credit, so they run only when asked for, with
STUDIO_LIVE=1 and the studio's Anthropic key (POPSMOSIS_ANTHROPIC_API_KEY, or ANTHROPIC_API_KEY on a laptop):
a key in the environment alone never runs them.

    STUDIO_LIVE=1 POPSMOSIS_ANTHROPIC_API_KEY=... python -m pytest studio/tests/test_live.py -s
"""

import os

import pytest

from studio import brain
from studio.steps.factcheck import Line, ask_checker

pytestmark = [
    pytest.mark.skipif(os.environ.get("STUDIO_LIVE") != "1", reason="needs STUDIO_LIVE=1 (spends Anthropic credit)"),
    pytest.mark.skipif(not brain.claude_key(), reason="needs POPSMOSIS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"),
]

PHOTOSYNTHESIS = {
    "f1": "In the light reactions, photosystem II uses light energy to split water, releasing oxygen, "
    "protons and electrons.",
    "f2": "Electrons pass from photosystem II through plastoquinone, cytochrome b6f, plastocyanin and "
    "photosystem I to NADP+ reductase, which makes NADPH. Of these carriers, only cytochrome b6f pumps "
    "protons into the thylakoid lumen; the others pass electrons on without pumping.",
    "f3": "Protons flow from the lumen back into the stroma through ATP synthase, which uses that flow to "
    "make ATP.",
}


def test_the_fact_check_flags_every_hand_off_pumping_a_proton(monkeypatch):
    lines = [
        Line("lyric", "v1-1", "Photosystem II splits water for electrons"),
        Line("lyric", "v1-2", "Every electron hand-off pumps a proton"),
        Line("lyric", "v1-3", "ATP synthase turns the proton flow to ATP"),
    ]
    checks = ask_checker(lines, lines, PHOTOSYNTHESIS, "", brain.ask, 1)
    for key, check in checks.items():
        print(f"{key}: {check.verdict} {check.source} {check.problem} {check.corrected}")
    call = brain.calls[-1]
    print(f"{call.model}: {call.input_tokens} in, {call.output_tokens} out, ${call.cost_usd:.4f}, {call.seconds}s")

    seeded = checks["lyric:v1-2"]
    assert seeded.verdict == "UNSUPPORTED"
    assert seeded.corrected and seeded.corrected != "Every electron hand-off pumps a proton"
    assert checks["lyric:v1-1"].verdict == "SUPPORTED"
    assert checks["lyric:v1-3"].verdict == "SUPPORTED"


def test_claude_sonnet_draws_a_scene_the_checks_accept(monkeypatch, tmp_path):
    """One scene of the sample bundle, drawn live by Claude Sonnet 5.5, as when an admin has Claude draw the art."""
    from studio.steps import art
    from studio.tests import sample_media

    bundle = sample_media.bundle()
    scene = next(s for s in bundle.scenes if s.id == "overview")
    (tmp_path / "art").mkdir()
    before = len(brain.calls)
    drawing = art.draw(tmp_path, bundle, scene, brain.ask, resume=False)
    for call in brain.calls[before:]:
        print(f"{call.label}: {call.model} {call.input_tokens} in, {call.output_tokens} out, "
              f"${call.cost_usd or 0:.4f}, {call.seconds}s")
    print("problems on the kept drawing:", drawing.problems)
    assert brain.calls[before].role == "artist"
    art.save(tmp_path, scene, drawing)
    print("art:", tmp_path / "art" / "overview.png")
    assert drawing.enough(scene)
