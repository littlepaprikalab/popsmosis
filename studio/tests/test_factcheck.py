import json

import pytest

from studio.jobs import read, write
from studio.schemas import Bundle, FactCheck, FactCheckReport, Request
from studio.steps import factcheck
from studio.steps.factcheck import check_bundle, cites, lines_of
from studio.tests.sample import CHORUS, bundle_data

SEEDED = "Every electron hand-off pumps a proton"
FIXED = "Only b6f pumps a proton through"


class FakeChecker:
    """Stands in for brain.ask: judges each line with `rule(text)`, records every payload."""

    def __init__(self, rule):
        self.rule = rule
        self.payloads = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        assert role == "brain" and schema is FactCheck and system == factcheck.PROMPT
        payload = json.loads(prompt)
        self.payloads.append(payload)
        checks = []
        for line in payload["lines"]:
            verdict = self.rule(line["text"])
            if verdict is None:
                continue  # the checker skipped this line
            name, source, corrected = verdict
            checks.append({"id": line["id"], "verdict": name, "source": source,
                           "problem": "wrong" if name == "UNSUPPORTED" else "", "corrected": corrected})
        return FactCheck.model_validate({"checks": checks}).model_dump()

    def ids(self, round_no):
        return [line["id"] for line in self.payloads[round_no - 1]["lines"]]


def supported(text):
    return "SUPPORTED", "f1", ""


def seeded_is_wrong(text):
    if text == SEEDED:
        return "UNSUPPORTED", "", FIXED
    return supported(text)


def bundle(change=None):
    data = bundle_data()
    if change:
        change(data)
    return Bundle.model_validate(data)


def test_every_line_is_checked_once_and_chorus_repeats_only_once():
    fake = FakeChecker(supported)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.rounds == 1 and checked == bundle()
    sent = fake.ids(1)
    assert sent.count("lyric:c1-1") == 1 and "lyric:c2-1" not in sent and "lyric:c3-1" not in sent
    assert sent[:2] == ["lyric:v1-1", "lyric:v1-2"]
    assert [k for k in sent if k.startswith("lesson:")] == [f"lesson:{i}" for i in range(1, 7)]
    assert len(report.lines) == len(lines_of(bundle())) and all(line.round == 1 for line in report.lines)


def test_a_fix_is_applied_and_only_the_changed_line_is_rechecked():
    fake = FakeChecker(seeded_is_wrong)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.rounds == 2
    assert fake.ids(2) == ["lyric:v1-2"]
    assert fake.payloads[1]["lines"][0]["previous"] == "Light lands on the thylakoid"
    assert "previous" not in fake.payloads[0]["lines"][1]
    assert checked.lyrics[1].text == FIXED
    [correction] = report.corrections
    assert (correction.round, correction.ids, correction.before, correction.after) == (1, ["v1-2"], SEEDED, FIXED)
    fixed_line = next(line for line in report.lines if line.id == "v1-2")
    assert (fixed_line.text, fixed_line.verdict, fixed_line.round) == (FIXED, "SUPPORTED", 2)


def test_a_chorus_fix_rewrites_every_repeat():
    def chorus_is_wrong(text):
        return ("UNSUPPORTED", "", "Protons return and ATP is made") if text == CHORUS else supported(text)

    report, checked = check_bundle(bundle(), "", FakeChecker(chorus_is_wrong))
    assert report.status == "passed"
    assert [line.text for line in checked.lyrics if line.section == "chorus"] == ["Protons return and ATP is made"] * 3
    assert report.corrections[0].ids == ["c1-1", "c2-1", "c3-1"]


def test_a_lesson_fix_is_written_back_into_the_markdown():
    def pumping_is_wrong(text):
        if text == "Only cytochrome b6f pumps protons.":
            return "UNSUPPORTED", "", "Only cytochrome b6f pumps protons into the lumen."
        return supported(text)

    fake = FakeChecker(pumping_is_wrong)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed"
    assert checked.lesson_markdown == bundle().lesson_markdown.replace(
        "pumps protons.", "pumps protons into the lumen.", 1)
    assert fake.ids(2) == ["lesson:3"]


def test_it_fails_after_three_rounds_when_fixes_keep_failing():
    def never_right(text):
        return ("UNSUPPORTED", "", text + " again") if text.startswith(SEEDED) else supported(text)

    fake = FakeChecker(never_right)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "failed" and report.rounds == 3 and len(fake.payloads) == 3
    assert "lyric v1-2" in report.reason and "wrong" in report.reason
    assert checked.lyrics[1].text == SEEDED + " again again"  # the last fix is never applied unchecked


def test_it_fails_at_once_when_a_wrong_line_has_no_correction():
    def rule(text):
        if text == "Light lands on the thylakoid":
            return "UNSUPPORTED", "", "Light reactions run in the thylakoid"  # fixable, but never applied
        return ("UNSUPPORTED", "", "") if text == SEEDED else supported(text)

    fake = FakeChecker(rule)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "failed" and len(fake.payloads) == 1
    assert report.reason == "lyric v1-2 is unsupported and has no correction: wrong"
    assert checked == bundle() and report.corrections == []


def test_missing_verdicts_and_made_up_sources_are_asked_again():
    asked = {}

    def flaky(text):
        asked[text] = asked.get(text, 0) + 1
        if text == "Light lands on the thylakoid" and asked[text] == 1:
            return None  # skipped in round 1
        if text == "Feel the gradient build" and asked[text] == 1:
            return "SUPPORTED", "f7", ""  # no such fact
        return supported(text)

    fake = FakeChecker(flaky)
    report, _ = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and fake.ids(2) == ["lyric:v1-1", "lyric:b-1"]


def test_lines_that_never_get_a_verdict_fail_the_check():
    report, _ = check_bundle(bundle(), "", FakeChecker(lambda t: None if t == SEEDED else supported(t)))
    assert report.status == "failed" and report.rounds == 3
    assert 'lyric v1-2 "Every electron hand-off pumps a proton": no verdict' in report.reason


def test_cites_needs_a_real_fact_id_or_a_study_guide_quote():
    facts = {"f1": "a", "f2": "b"}
    guide = "The hippocampus  forms new explicit memories."
    assert cites("f1", facts, "") and cites("f1, f2", facts, "")
    assert not cites("f3", facts, "") and not cites("", facts, "") and not cites("f1, f9", facts, "")
    assert cites('"the hippocampus forms new"', facts, guide)
    assert not cites("the amygdala forms new memories", facts, guide)
    assert not cites("memories", facts, guide)  # too short to count as a quote


def test_run_saves_the_fixed_bundle_and_keeps_the_draft(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    write(tmp_path / "bundle.json", bundle())
    report = factcheck.run(tmp_path, ask=FakeChecker(seeded_is_wrong))
    assert report.status == "passed"
    assert read(tmp_path / "factcheck.json", FactCheckReport) == report
    assert read(tmp_path / "bundle.json", Bundle).lyrics[1].text == FIXED
    assert read(tmp_path / "bundle.draft.json", Bundle) == bundle()


def test_run_leaves_the_bundle_alone_when_the_check_fails(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions", study_guide="Only b6f pumps."))
    write(tmp_path / "bundle.json", bundle())
    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text == SEEDED else supported(text))
    report = factcheck.run(tmp_path, ask=fake)
    assert report.status == "failed"
    assert fake.payloads[0]["study_guide"] == "Only b6f pumps."
    assert read(tmp_path / "bundle.json", Bundle) == bundle()
    assert not (tmp_path / "bundle.draft.json").exists()


@pytest.mark.parametrize("rounds", [1, 2])
def test_max_rounds_is_respected(rounds):
    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", text + "!") if text.startswith(SEEDED) else supported(text))
    report, _ = check_bundle(bundle(), "", fake, max_rounds=rounds)
    assert report.status == "failed" and len(fake.payloads) == rounds
