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
        Line("lyric", "v1-2", "Each electron hand-off pumps a proton"),
        Line("lyric", "v1-3", "ATP synthase turns the proton flow to ATP"),
    ]
    checks = ask_checker(lines, lines, PHOTOSYNTHESIS, "", brain.ask, 1)
    for key, check in checks.items():
        print(f"{key}: {check.verdict} {check.source} {check.problem} {check.corrected}")
    call = brain.calls[-1]
    print(f"{call.model}: {call.input_tokens} in, {call.output_tokens} out, ${call.cost_usd:.4f}, {call.seconds}s")

    seeded = checks["lyric:v1-2"]
    assert seeded.verdict == "UNSUPPORTED"
    assert seeded.corrected and seeded.corrected != "Each electron hand-off pumps a proton"
    assert checks["lyric:v1-1"].verdict == "SUPPORTED"
    assert checks["lyric:v1-3"].verdict == "SUPPORTED"


def test_the_song_is_held_to_what_a_student_would_believe_and_a_wrong_couplet_rewritten_to_sing():
    """A couplet that compresses and adds a true detail the facts leave out must pass; the seeded couplet must
    fail, and the songwriter's rewrite of it keep its shape."""
    from studio.schemas import Bundle
    from studio.steps import songwriter
    from studio.tests.sample import bundle_data

    data = bundle_data()
    data["facts"] = [{"id": i, "text": text, "source_quote": ""} for i, text in PHOTOSYNTHESIS.items()]
    bundle = Bundle.model_validate(data)
    fine = Line("lyric", "v1-1,v1-2", "Green chlorophyll grabs the morning light\nSplit the water, oxygen takes flight",
                ("v1-1", "v1-2"))
    wrong = Line("lyric", "v1-3,v1-4", "Down the chain the electrons hop\nEvery hand-off pumps a proton, non-stop",
                 ("v1-3", "v1-4"))
    context = {"course": "AP Biology", "topic": "the light reactions", "audience": "high school",
               "song_style": "upbeat pop, 112 bpm"}
    checks = ask_checker([fine, wrong], [fine, wrong], PHOTOSYNTHESIS, "", brain.ask, 1, context)
    for key, check in checks.items():
        print(f"{key}: {check.verdict} {check.source} {check.problem} {check.corrected!r}")
    assert checks[fine.key].verdict == "SUPPORTED"
    assert checks[wrong.key].verdict == "UNSUPPORTED"

    written = songwriter.rewrite(bundle, [(wrong, checks[wrong.key])], brain.ask, 1)
    print("songwriter:", written)
    for call in brain.calls[-2:]:
        print(f"{call.label}: {call.model} {call.input_tokens} in, {call.output_tokens} out, "
              f"${call.cost_usd or 0:.4f}, {call.seconds}s")
    assert "every hand-off" not in written.get(wrong.id, "").lower()


def test_the_audit_corrects_a_fact_saying_every_hand_off_pumps_a_proton():
    """The fact-check case moved into the planner's own facts, where the fact-check cannot see it: the audit must
    correct it in its first call, pass the correction in its second, and leave the right facts alone."""
    from studio.schemas import Bundle
    from studio.steps import audit
    from studio.tests.sample import bundle_data

    data = bundle_data()
    data["facts"] = [{"id": i, "text": text, "source_quote": ""} for i, text in PHOTOSYNTHESIS.items()]
    data["facts"][1]["text"] = (
        "Electrons pass from photosystem II through plastoquinone, cytochrome b6f, plastocyanin and photosystem I "
        "to NADP+ reductase, which makes NADPH, and every electron hand-off along the way pumps a proton into the "
        "thylakoid lumen.")
    before = len(brain.calls)
    report, audited = audit.audit_bundle(Bundle.model_validate(data), "", brain.ask)
    for fact in report.facts:
        print(f"{fact.id}: {fact.verdict} ({fact.basis}) {fact.problem}")
    for correction in report.corrections:
        print(f"{correction.id} corrected: {correction.after}")
    for call in brain.calls[before:]:
        print(f"{call.label}: {call.model} {call.input_tokens} in, {call.output_tokens} out, "
              f"${call.cost_usd or 0:.4f}, {call.seconds}s")

    assert report.status == "passed" and [c.id for c in report.corrections] == ["f2"]
    assert "every electron hand-off" not in audited.facts[1].text.lower()
    assert [f.verdict for f in report.facts if f.id != "f2"] == ["CORRECT", "CORRECT"]


def test_the_brain_draws_a_scene_the_checks_accept(monkeypatch, tmp_path):
    """One scene of the sample bundle, drawn live by Claude Opus 5.5, as when an admin has Claude draw the art."""
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
    art.save(tmp_path, scene, drawing)
    print("art:", tmp_path / "art" / "overview.png")
    assert drawing.enough(scene)
