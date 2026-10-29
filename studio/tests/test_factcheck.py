import json

import pytest

from studio.jobs import read, write
from studio.schemas import Bundle, FactCheck, FactCheckReport, LyricRewrite, Request
from studio.steps import factcheck, songwriter
from studio.steps.factcheck import check_bundle, cites, dropped_motions, dropped_tags, lines_of
from studio.tests.sample import CHORUS, bundle_data

SEEDED = "Every electron hand-off pumps a proton"
FIXED = "Only b6f pumps a proton through"
FIRST = "Light lands on the thylakoid"  # the line SEEDED rhymes with: lyric v1-1, in its couplet


def judge(rule, text):
    """rule(line) for each line of a couplet, put together: the couplet fails when a line of it does, corrected
    line by line (no correction when a failing line has none); one line is judged as it is."""
    parts = text.split("\n")
    verdicts = [rule(part) for part in parts]
    if len(parts) == 1:
        return verdicts[0]
    if any(v is None for v in verdicts):
        return None
    wrong = [v for v in verdicts if v[0] == "UNSUPPORTED"]
    if wrong:
        fixed = "\n".join(v[2] if v[0] == "UNSUPPORTED" else part for v, part in zip(verdicts, parts))
        return "UNSUPPORTED", "", fixed if all(v[2] for v in wrong) else ""
    return next((v for v in verdicts if v[0] == "SUPPORTED"), verdicts[0])


class FakeChecker:
    """Stands in for brain.ask: judges each line with `rule(text)` (a couplet line by line), records every
    payload. Asked to rewrite, it answers `song(couplets)`, by default with none, so the checker's fix is used."""

    def __init__(self, rule, song=None):
        self.rule, self.song = rule, song
        self.payloads, self.songs = [], []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if schema is LyricRewrite:
            assert role == "brain" and system == songwriter.PROMPT and label.startswith("lyric rewrite round ")
            self.songs.append(json.loads(prompt))
            written = self.song(self.songs[-1]["couplets"]) if self.song else []
            return LyricRewrite.model_validate({"couplets": written}).model_dump()
        assert role == "brain" and schema is FactCheck and system == factcheck.PROMPT
        payload = json.loads(prompt)
        self.payloads.append(payload)
        checks = []
        for line in payload["lines"]:
            verdict = judge(self.rule, line["text"])
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
    assert sent[:2] == ["lyric:v1-1,v1-2", "lyric:c1-1"]  # the song a couplet at a time
    assert [k for k in sent if k.startswith("lesson:")] == [f"lesson:{i}" for i in range(1, 7)]
    assert len(report.lines) == len(lines_of(bundle())) and all(line.round == 1 for line in report.lines)


def test_a_fix_is_applied_and_only_the_changed_line_is_rechecked():
    fake = FakeChecker(seeded_is_wrong)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.rounds == 2
    assert fake.ids(2) == ["lyric:v1-1,v1-2"]
    assert fake.payloads[1]["lines"][0]["text"] == f"{FIRST}\n{FIXED}"  # its rhyme comes with it
    assert "previous" not in fake.payloads[1]["lines"][0]  # the song's first couplet: nothing comes before it
    assert checked.lyrics[0].text == FIRST and checked.lyrics[1].text == FIXED
    [correction] = report.corrections
    assert (correction.round, correction.ids, correction.before, correction.after, correction.by) == (
        1, ["v1-1", "v1-2"], f"{FIRST}\n{SEEDED}", f"{FIRST}\n{FIXED}", "checker")
    fixed_line = next(line for line in report.lines if line.id == "v1-1,v1-2")
    assert (fixed_line.text, fixed_line.verdict, fixed_line.round) == (f"{FIRST}\n{FIXED}", "SUPPORTED", 2)


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


def test_a_bundle_written_with_an_exam_question_is_checked_without_it():
    # The exam question left the videos on Oct 28; a bundle written before still carries one.
    data = bundle().model_dump()
    data["frq"] = {"prompt": "Explain it.", "predict_question": "Which?", "model_answer_steps": ["One."], "twist": "And?"}
    fake = FakeChecker(supported)
    report, checked = check_bundle(Bundle.model_validate(data), "", fake)
    assert report.status == "passed" and not any(k.startswith("frq:") for k in fake.ids(1))
    assert "frq" not in checked.model_dump()


def test_it_fails_after_three_rounds_when_fixes_keep_failing():
    def never_right(text):
        return ("UNSUPPORTED", "", text + " again") if text.startswith(SEEDED) else supported(text)

    fake = FakeChecker(never_right)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "failed" and report.rounds == 3 and len(fake.payloads) == 3
    assert "lyric v1-1, v1-2" in report.reason and "wrong" in report.reason
    assert checked.lyrics[1].text == SEEDED + " again again"  # the last fix is never applied unchecked


def test_it_fails_at_once_when_a_wrong_line_has_no_correction():
    def rule(text):
        if text == "Feel the gradient build":
            return "UNSUPPORTED", "", "Feel the proton gradient build"  # fixable, but never applied
        return ("UNSUPPORTED", "", "") if text.startswith("The light reactions happen") else supported(text)

    fake = FakeChecker(rule)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "failed" and len(fake.payloads) == 1
    assert report.reason == "narration n1 is unsupported and has no correction: wrong"
    assert checked == bundle() and report.corrections == []
    assert fake.songs == []  # no songwriter is paid for a check that fails anyway


def test_a_wrong_couplet_with_no_correction_from_the_checker_or_the_songwriter_fails():
    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text == SEEDED else supported(text))
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "failed" and len(fake.payloads) == 1 and len(fake.songs) == 1
    assert report.reason == "lyric v1-1, v1-2 is unsupported and has no correction: wrong"
    assert checked == bundle()


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
    assert report.status == "passed" and fake.ids(2) == ["lyric:v1-1,v1-2", "lyric:b-1"]


def test_lines_that_never_get_a_verdict_fail_the_check():
    report, _ = check_bundle(bundle(), "", FakeChecker(lambda t: None if t == SEEDED else supported(t)))
    assert report.status == "failed" and report.rounds == 3
    assert f'lyric v1-1, v1-2 "{FIRST} / {SEEDED}": no verdict' in report.reason


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


def test_checks_after_a_correction_join_the_bundle_steps_checks(tmp_path):
    from studio.schemas import CheckReport, Problem

    write(tmp_path / "request.json", Request(topic="the light reactions"))
    write(tmp_path / "bundle.json", bundle())
    earlier = Problem(line_id="c1-1", rule="spotlight", detail="from the bundle step", repaired=True)
    write(tmp_path / "checks.json", CheckReport(problems=[earlier]))

    def names_the_thylakoid(text):  # the correction says a name the line did not list
        return ("UNSUPPORTED", "", "Only b6f by the thylakoid pumps") if text == SEEDED else supported(text)

    factcheck.run(tmp_path, ask=FakeChecker(names_the_thylakoid))
    problems = read(tmp_path / "checks.json", CheckReport).problems
    assert problems[0] == earlier
    assert [(p.line_id, p.rule) for p in problems[1:]] == [("v1-2", "unlisted-name")]
    assert read(tmp_path / "bundle.json", Bundle).lyrics[1].names == ["thylakoid"]


def test_a_bundle_from_before_names_can_still_be_fact_checked(tmp_path):
    data = bundle_data()
    for item in [*data["lyrics"], *data["narration"]]:
        del item["names"]
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    (tmp_path / "bundle.json").write_text(json.dumps(data))
    assert factcheck.run(tmp_path, ask=FakeChecker(supported)).status == "passed"


def test_tags_are_checked_once_each_with_what_a_tag_is_judged_by():
    fake = FakeChecker(supported)
    report, checked = check_bundle(bundle(), "", fake)
    tags = [(line["id"], line["text"]) for line in fake.payloads[0]["lines"] if line["id"].startswith("tag:")]
    # The tags both videos carry are sent once, under the song's ids.
    assert tags == [("tag:song-1", "#photosynthesis"), ("tag:song-2", "#lightreactions"), ("tag:song-3", "#apbio"),
                    ("tag:song-4", "#studysong"), ("tag:song-5", "#pop"), ("tag:lesson-4", "#explained")]
    assert fake.payloads[0]["about"] == {"course": "AP Biology", "topic": "the light reactions",
                                         "audience": "high school", "song_style": "upbeat pop, 112 bpm"}
    assert report.status == "passed" and checked.tags == bundle().tags and dropped_tags(report) == []
    assert [line.id for line in report.lines if line.kind == "tag"] == [
        "song-1", "song-2", "song-3", "song-4", "song-5", "lesson-1", "lesson-2", "lesson-3", "lesson-4"]


def test_a_tag_that_fails_leaves_both_videos_and_never_fails_the_check():
    def rule(text):
        if text == "#lightreactions":
            return "UNSUPPORTED", "", ""  # dropped: a tag is never rewritten
        if text == "#apbio":
            return None  # never gets a verdict
        if text.startswith("#"):
            return "FILLER", "", ""
        return supported(text)

    fake = FakeChecker(rule)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.reason == "" and report.corrections == []
    assert checked.tags.song == ["photosynthesis", "studysong", "pop"]
    assert checked.tags.lesson == ["photosynthesis", "explained"]
    # The tag with no verdict is asked again each round, with what it is judged by; then it is dropped.
    assert len(fake.payloads) == 3 and fake.ids(2) == ["tag:song-3"] and fake.ids(3) == ["tag:song-3"]
    assert fake.payloads[1]["about"]["course"] == "AP Biology"
    assert [(t.text, t.verdict, t.problem) for t in dropped_tags(report)] == [
        ("#lightreactions", "UNSUPPORTED", "wrong"), ("#apbio", None, "")]
    assert FactCheckReport.model_validate_json(report.model_dump_json()) == report


ELECTRONS = "e⁻ travels from Thylakoid to Cytochrome b6f"
PROTONS = "H⁺ travels from Cytochrome b6f to Thylakoid"


def moving(data):
    """Electrons on a verse line and on every chorus line, protons on another, and an unmarked flow."""
    for d in data["direction"]:
        if d["line_id"] in ("v1-2", "c1-1", "c2-1", "c3-1"):
            d.update(particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻")
        elif d["line_id"] == "v2-1":
            d.update(particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺")
        elif d["line_id"] == "b-1":
            d.update(particles=["Thylakoid", "Cytochrome b6f"], carries="")


def test_each_marked_motion_is_checked_once_and_unmarked_ones_are_not_sent():
    fake = FakeChecker(supported)
    report, checked = check_bundle(bundle(moving), "", fake)
    motions = [(line["id"], line["text"]) for line in fake.payloads[0]["lines"] if line["id"].startswith("motion:")]
    assert motions == [("motion:v1-2", ELECTRONS), ("motion:v2-1", PROTONS)]  # after the tags, the chorus once
    assert report.status == "passed" and checked == bundle(moving) and dropped_motions(report) == []


def test_a_motion_that_fails_is_left_out_of_the_video_and_never_fails_the_check():
    def rule(text):
        if text == PROTONS:
            return "UNSUPPORTED", "", ""  # left out: a motion is never rewritten
        return supported(text)

    report, checked = check_bundle(bundle(moving), "", FakeChecker(rule))
    assert report.status == "passed" and report.corrections == []
    shown = {d.line_id: (d.particles, d.carries) for d in checked.direction if d.particles}
    assert shown == {line: (["Thylakoid", "Cytochrome b6f"], "e⁻") for line in ("v1-2", "c1-1", "c2-1", "c3-1")} | {
        "b-1": (["Thylakoid", "Cytochrome b6f"], "")}  # an unmarked flow is not the check's
    assert [(m.text, m.verdict) for m in dropped_motions(report)] == [(PROTONS, "UNSUPPORTED")]


def test_a_motion_with_no_verdict_is_asked_again_then_left_out():
    fake = FakeChecker(lambda text: None if text == ELECTRONS else supported(text))
    report, checked = check_bundle(bundle(moving), "", fake)
    assert fake.ids(2) == ["motion:v1-2"] and fake.ids(3) == ["motion:v1-2"]
    assert report.status == "passed"
    assert {d.line_id for d in checked.direction if d.particles} == {"v2-1", "b-1"}


def test_what_builds_up_is_checked_too_and_left_out_when_it_fails():
    def building(data):
        moving(data)
        data["builds_up"] = [{"carries": "H⁺", "region": "Thylakoid"}, {"carries": "e⁻", "region": "Cytochrome b6f"}]

    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text == "e⁻ builds up in Cytochrome b6f"
                       else supported(text))
    report, checked = check_bundle(bundle(building), "", fake)
    sent = [(line["id"], line["text"]) for line in fake.payloads[0]["lines"] if line["id"].startswith("motion:builds")]
    assert sent == [("motion:builds-1", "H⁺ builds up in Thylakoid"), ("motion:builds-2", "e⁻ builds up in Cytochrome b6f")]
    assert report.status == "passed" and [(b.carries, b.region) for b in checked.builds_up] == [("H⁺", "Thylakoid")]
    assert [m.text for m in dropped_motions(report)] == ["e⁻ builds up in Cytochrome b6f"]


def test_what_turns_is_checked_with_what_drives_it():
    def spinning(data):
        moving(data)
        data["spins"] = [{"structure": "Cytochrome b6f"}, {"structure": "Thylakoid"}]
        for d in data["direction"]:
            if d["line_id"] == "b-1":  # unmarked: an analogy's water, say
                d.update(particles=["Cytochrome b6f", "Thylakoid"])

    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text.startswith("Thylakoid spins") else supported(text))
    report, checked = check_bundle(bundle(spinning), "", fake)
    sent = [line["text"] for line in fake.payloads[0]["lines"] if line["id"].startswith("motion:spins")]
    assert sent == ["Cytochrome b6f spins as e⁻ flows through it",
                    "Thylakoid spins as H⁺ flows through it"]
    assert [s.structure for s in checked.spins] == ["Cytochrome b6f"]


def test_what_turns_an_unmarked_part_is_the_stop_just_before_it():
    def dam(data):
        for d in data["direction"]:
            if d["line_id"] == "v1-2":  # pump -> reservoir -> turbine, as an analogy's water runs
                d.update(particles=["Thylakoid", "Cytochrome b6f", "Thylakoid"], carries="")
        data["spins"] = [{"structure": "Thylakoid"}]

    fake = FakeChecker(supported)
    check_bundle(bundle(dam), "", fake)
    assert [line["text"] for line in fake.payloads[0]["lines"] if line["id"].startswith("motion:spins")] == [
        "Thylakoid spins as particles flow into it from Cytochrome b6f"]


def test_a_blocked_flow_is_checked_as_stopped_there():
    def stopping(data):
        moving(data)
        data["blocks"] = [{"structure": "Cytochrome b6f"}]

    fake = FakeChecker(supported)
    check_bundle(bundle(stopping), "", fake)
    motions = [line["text"] for line in fake.payloads[0]["lines"] if line["id"].startswith("motion:")]
    assert "e⁻ flow from Thylakoid is stopped at Cytochrome b6f" in motions
    assert "H⁺ travels from Cytochrome b6f to Thylakoid" in motions  # not blocked: it ends elsewhere


def test_a_tag_never_rides_along_with_a_corrected_line():
    fake = FakeChecker(seeded_is_wrong)
    report, _ = check_bundle(bundle(), "", fake)
    # The re-check is only the corrected couplet, with what a lyric is judged by: the tags have their verdicts.
    assert fake.ids(2) == ["lyric:v1-1,v1-2"] and fake.payloads[1]["about"]["audience"] == "high school"

    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "Only cytochrome b6f pumps protons into the lumen.")
                       if text == "Only cytochrome b6f pumps protons." else supported(text))
    check_bundle(bundle(), "", fake)
    assert fake.ids(2) == ["lesson:3"] and "about" not in fake.payloads[1]  # a lesson line rests on the facts alone


def test_a_bundle_without_tags_sends_none_and_no_about():
    fake = FakeChecker(supported)
    report, checked = check_bundle(bundle(lambda d: d.pop("tags")), "", fake)
    assert report.status == "passed" and set(fake.payloads[0]["about"]) == {"course", "topic", "audience", "song_style"}
    assert not [i for i in fake.ids(1) if i.startswith("tag:")]


def test_run_posts_only_the_tags_that_passed_and_keeps_the_draft(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    write(tmp_path / "bundle.json", bundle())
    report = factcheck.run(tmp_path, ask=FakeChecker(lambda t: ("UNSUPPORTED", "", "") if t == "#pop" else supported(t)))
    assert report.status == "passed"
    assert read(tmp_path / "bundle.json", Bundle).tags.song == ["photosynthesis", "lightreactions", "apbio", "studysong"]
    assert read(tmp_path / "bundle.draft.json", Bundle) == bundle()


def test_a_report_from_before_tags_still_reads():
    old = {"status": "passed", "reason": "", "rounds": 1, "corrections": [],
           "lines": [{"kind": "lyric", "id": "v1-1", "text": "x", "verdict": "SUPPORTED", "source": "f1", "round": 1}]}
    assert FactCheckReport.model_validate(old).lines[0].problem == ""



KEY = "Every carrier pumps H⁺"


def with_key_lines(data):
    data["key_lines"] = [{"from_line": "v1-1", "text": "How does light make ATP?"}, {"from_line": "v2-1", "text": KEY}]


def test_key_lines_and_the_core_idea_are_checked_and_a_wrong_one_corrected():
    def rule(text):
        return ("UNSUPPORTED", "", "Only b6f pumps H⁺") if text == KEY else supported(text)

    fake = FakeChecker(rule)
    report, checked = check_bundle(bundle(with_key_lines), "", fake)
    assert {"keyline:core_idea", "keyline:v1-1", "keyline:v2-1"} <= set(fake.ids(1))
    assert report.status == "passed"
    assert [k.text for k in checked.key_lines] == ["How does light make ATP?", "Only b6f pumps H⁺"]
    assert [(c.kind, c.ids) for c in report.corrections] == [("keyline", ["v2-1"])]


def test_a_wrong_key_line_with_no_correction_leaves_the_screen_and_never_fails_the_check():
    def rule(text):
        return ("UNSUPPORTED", "", "") if text == KEY else supported(text)

    report, checked = check_bundle(bundle(with_key_lines), "", FakeChecker(rule))
    assert report.status == "passed"
    assert [k.text for k in checked.key_lines] == ["How does light make ATP?"]


def test_a_key_line_that_never_passes_leaves_the_screen_and_the_report_keeps_it():
    def rule(text):
        return ("UNSUPPORTED", "", text + "!") if text.startswith(KEY) else supported(text)

    report, checked = check_bundle(bundle(with_key_lines), "", FakeChecker(rule))
    assert report.status == "passed"
    assert [k.text for k in checked.key_lines] == ["How does light make ATP?"]
    assert any(c.kind == "keyline" and c.verdict == "UNSUPPORTED" for c in report.lines)


def test_a_second_flow_is_its_own_motion_and_one_that_fails_leaves_only_itself():
    OXYGEN = "O₂ travels from Cytochrome b6f to Thylakoid"

    def change(data):
        moving(data)
        next(d for d in data["direction"] if d["line_id"] == "v2-1")["also"] = [
            {"particles": ["Cytochrome b6f", "Thylakoid"], "carries": "O₂"}]

    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text == OXYGEN else supported(text))
    report, checked = check_bundle(bundle(change), "", fake)
    assert ("motion:v2-1+1", OXYGEN) in [(line["id"], line["text"]) for line in fake.payloads[0]["lines"]]
    line = next(d for d in checked.direction if d.line_id == "v2-1")
    assert report.status == "passed" and line.also == [] and (line.particles, line.carries) == (
        ["Cytochrome b6f", "Thylakoid"], "H⁺")
