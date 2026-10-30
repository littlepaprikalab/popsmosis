import json

import pytest

from studio import __main__ as cli
from studio import brain, ledger, scorecard
from studio.schemas import Bundle, Request
from studio.tests.sample import CHORUS, bundle_data

ABOUT = {"started": "2026-10-29T21:00:00+0000", "commit": "61a18a680288bb44", "dirty": False, "diff": "",
         "prompt": "aaaaaaaaaaaa", "rubric": "bbbbbbbbbbbb", "topics": "cccccccccccc", "brain": "Claude Opus 5.5",
         "model": "claude-opus-5-5", "effort": "high", "judge": "claude-sonnet-5-5 at high", "name": "run-a"}


def topic(id_: str, **changes) -> scorecard.Topic:
    return scorecard.Topic(**{"id": id_, "subject": "biology", "level": "AP", "topic": f"topic {id_}",
                              "course": "AP Biology", **changes})


def scores(value: int = 4) -> dict:
    answer = {key: {"reason": f"{key} reason", "score": value} for key in scorecard.CRITERIA}
    return {**answer, "errors": []}


class FakeAsk:
    """Answers each call by its label, as the studio's steps make them, and keeps what each was asked."""

    def __init__(self, judge=None, passed=True, fail=None, cost=0.0, job=None):
        self.judge, self.passed, self.fail, self.cost, self.job = judge or scores(), passed, fail, cost, job
        self.calls = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.calls.append({"role": role, "label": label, "prompt": prompt, "system": system})
        if label == self.fail:
            raise brain.InvalidOutput("does not fit", "{}")
        if self.cost and self.job:
            ledger.log(self.job, [brain.Call(label, role, "claude-opus-5-5", 10, 10, self.cost, 1.0, "end_turn")])
        if label == "script":  # no script: the bundle is written without one
            raise brain.BrainError("no answer")
        if label == "narration edit":  # handed back as it came
            return {"lines": [{"id": n["id"], "text": n["text"]} for n in json.loads(prompt)["narration"]]}
        if label == "bundle":
            return {**bundle_data(), "why_chain": ["A because B", "C because D", "E because F"],
                    "key_terms": ["ATP synthase"],
                    "key_lines": [{"from_line": "c1-1", "text": "Protons flow back, ATP is made",
                                   "from_narration": "n2"}]}
        if label.endswith("grade"):
            return {"core_idea": self.passed, "links": [True, self.passed, False], "missing": [] if self.passed
                    else ["why the protons flow back"]}
        if label in ("learner", "learner again"):
            return {"big_idea": "light makes ATP", "why": "protons flow", "what_if": "no ATP"}
        if label == "lesson rewrite":
            return {"lesson_markdown": "# A clearer lesson"}
        if label == "judge":
            return self.judge
        raise AssertionError(f"an unexpected call: {label}")


def asks(**kwargs):
    """ask_for: a fresh FakeAsk for each topic's job, all kept for a look."""
    made = {}

    def ask_for(job):
        made[job.name] = FakeAsk(job=job, **kwargs)
        return made[job.name]

    return ask_for, made


def test_the_topic_set_keeps_its_twelve_topics_across_subjects_and_levels():
    topics = scorecard.load_topics()
    ids = [t.id for t in topics]
    assert len(topics) == 12 and len(set(ids)) == 12
    for kept in ("sodium-potassium-pump", "derivative", "french-revolution", "dramatic-irony", "catalyst",
                 "supply-and-demand", "light-reactions"):
        assert kept in ids
    assert len({t.subject for t in topics}) >= 10
    assert {"middle school", "high school", "AP", "college"} <= {t.level for t in topics}
    guided = [t for t in topics if t.study_guide]
    assert len(guided) == 1 and "Unconditioned stimulus" in guided[0].as_request().study_guide
    assert all(isinstance(t.as_request(), Request) for t in topics)


def test_the_rubric_and_the_judges_schema_name_the_same_seven_criteria():
    for key in scorecard.CRITERIA:
        assert f"## {key}\n" in scorecard.RUBRIC
    assert set(scorecard.CRITERIA) < set(scorecard.Judgement.model_fields)
    schema = brain.strict_schema(scorecard.Judgement)  # the schema both providers accept
    # The schema itself holds a score to 1 to 5, so a constrained answer cannot score 0.
    assert schema["properties"]["hook"]["properties"]["score"]["enum"] == [1, 2, 3, 4, 5]
    for wrong in (0, 6):
        with pytest.raises(ValueError):
            scorecard.Judgement.model_validate(scores(wrong))


def test_the_judge_is_the_same_claude_model_whatever_the_brain(monkeypatch):
    seen = []

    def fake_claude(role, prompt, schema, images, system, label):
        seen.append(role)
        return ['{"text": "ok"}']

    monkeypatch.setattr(brain, "_ask_claude", fake_claude)
    monkeypatch.setattr(brain, "_ask_meta", lambda *a: pytest.fail("the judge never runs on Meta"))
    with brain.chosen("muse-spark"):
        brain.ask("judge", "grade this")
    assert seen == ["judge"]
    request = brain.claude_request("judge", "grade this", scorecard.Judgement, (), "the rubric")
    assert request["model"] == brain.CLAUDE["judge"]["model"] != brain.CHOICES["claude"]["model"]
    assert request["output_config"]["effort"] == brain.CLAUDE["judge"]["effort"]
    assert "fallbacks" not in request


def test_a_run_makes_and_scores_each_topic_and_saves_the_tables(tmp_path):
    ask_for, made = asks()
    card = scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=ask_for, about=ABOUT, workers=2)
    # The bundle step alone (with its learner test), then the judge: no fact-check, art or audio.
    assert [c["label"] for c in made["one"].calls] == ["script", "bundle", "learner", "learner grade",
                                                       "narration edit", "judge"]
    judged = made["one"].calls[-1]
    assert judged["role"] == "judge" and judged["system"] == scorecard.RUBRIC
    assert judged["prompt"].startswith("<droplet>") and CHORUS in judged["prompt"]
    assert '"Protons flow back, ATP is made" (from lyric c1-1' in judged["prompt"]
    for name in ("request.json", "bundle.json", "checks.json", "learner.json", "judge.json"):
        assert (tmp_path / "one" / name).exists()

    row = card["topics"][0]
    assert row["made"] and not row["failed"] and row["title_line"] == CHORUS
    assert row["learner"]["first"] == {"core_idea": True, "links": 2, "of": 3, "passed": True, "missing": []}
    assert row["rubric"]["hook"] == {"reason": "hook reason", "score": 4}
    assert row["checks"]["left"] > 0  # the sample sings its chorus without a title line in the intro, and more
    assert card["summary"]["rubric"]["frame"] == {"mean": 4, "lowest": 4, "low": 0}
    assert card["summary"]["learner"]["first_passed"] == 2

    saved = json.loads((tmp_path / "scorecard.json").read_text())
    assert saved["about"]["commit"] == ABOUT["commit"] and len(saved["topics"]) == 2
    table = (tmp_path / "scorecard.md").read_text()
    assert "| Topic | Level | Checks | Learner | Hook | Frame |" in table
    assert "| one | AP |" in table and "pass 2/3" in table and "- **Hook 4.** hook reason" in table


def test_a_missed_lesson_shows_its_rewrite(tmp_path):
    ask_for, made = asks(passed=False)
    card = scorecard.run([topic("one")], tmp_path, ask_for=ask_for, about=ABOUT)
    assert "lesson rewrite" in [c["label"] for c in made["one"].calls]
    assert scorecard.learner_cell(card["topics"][0]) == "miss 1/3 (rewrite: miss 1/3)"
    assert "missed: why the protons flow back" in (tmp_path / "scorecard.md").read_text()


def test_a_topic_that_fails_is_scored_as_failed_and_the_others_go_on(tmp_path):
    made = {}

    def ask_for(job):
        made[job.name] = FakeAsk(fail="bundle" if job.name == "bad" else None)
        return made[job.name]

    card = scorecard.run([topic("bad"), topic("good")], tmp_path, ask_for=ask_for, about=ABOUT)
    bad, good = card["topics"]
    assert bad["failed"]["step"] == "bundle" and "InvalidOutput" in bad["failed"]["error"] and not bad["rubric"]
    assert good["rubric"] and card["summary"]["failed"] == {"bad": "bundle"}
    assert (tmp_path / "bad" / "bundle.invalid.json").exists()  # kept for a look, as the bundle step does
    assert "Failed at the bundle" in (tmp_path / "scorecard.md").read_text()


def test_a_resumed_run_makes_only_what_is_missing(tmp_path):
    ask_for, made = asks(judge=scores(3))
    scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=ask_for, about=ABOUT)
    (tmp_path / "two" / "judge.json").unlink()
    ask_for, made = asks(judge=scores(5))
    card = scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=ask_for, about={**ABOUT, "commit": "other"})
    assert made["one"].calls == [] and [c["label"] for c in made["two"].calls] == ["judge"]
    assert [r["rubric"]["hook"]["score"] for r in card["topics"]] == [3, 5]
    assert card["about"]["commit"] == ABOUT["commit"]  # a resumed run keeps what it started with


def test_no_topic_starts_once_the_run_has_spent_its_budget(tmp_path):
    ask_for, made = asks(cost=0.5)
    card = scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=ask_for, about=ABOUT, workers=1, budget=1.0)
    assert "two" not in made
    assert card["topics"][1]["failed"]["error"].startswith("not run")
    assert card["topics"][0]["cost"]["bundle"] == 2.5 and card["topics"][0]["cost"]["judge"] == 0.5


def test_compare_shows_two_runs_side_by_side_and_warns_when_the_judge_changed(tmp_path):
    ask_for, _ = asks(judge=scores(3))
    a = scorecard.run([topic("one"), topic("two")], tmp_path / "a", ask_for=ask_for, about=ABOUT)
    ask_for, _ = asks(judge=scores(4), passed=False)
    b = scorecard.run([topic("one")], tmp_path / "b", ask_for=ask_for, about={**ABOUT, "name": "run-b"})
    text = scorecard.compare(a, b)
    assert "# Scorecards: run-a vs run-b" in text and "on the 1 topics both ran" in text
    assert "| Hook (mean) | 3.00 | 4.00 | +1.00 |" in text
    assert "| Learner passed on the first read | 1 | 0 | -1 |" in text
    assert "| one | " in text and "| 3 → 4 |" in text and "pass 2/3 → miss 1/3 (rewrite: miss 1/3)" in text
    assert "Not comparable" not in text
    later = scorecard.compare(a, {**b, "about": {**b["about"], "judge": "claude-fable-5-1 at high"}})
    assert "Not comparable: the judge changed" in later


def test_the_command_spends_nothing_without_the_keys(monkeypatch, capsys):
    monkeypatch.setattr(scorecard, "run", lambda *a, **k: pytest.fail("nothing runs without a key"))
    assert cli.main(["scorecard", "run", "--topics", "derivative"]) == 2
    assert "needs" in capsys.readouterr().err
    assert cli.main(["scorecard", "run", "--topics", "no-such-topic"]) == 2
    assert cli.main(["scorecard", "run", "--resume", "run-a", "--topics", "derivative"]) == 2


def test_the_judge_reads_the_whole_droplet():
    bundle = Bundle.model_validate({**bundle_data(), "why_chain": ["A because B"], "key_terms": ["ATP synthase"]})
    text = scorecard.droplet_text(bundle, Request(topic="x", request="for my exam", study_guide="ATP is made."))
    for part in (bundle.core_idea, "1. A because B", bundle.lesson_markdown.strip(), "[chorus]", "c1-1: " + CHORUS,
                 "n2: ATP synthase uses", "for my exam", "## Study guide the learner gave\nATP is made.",
                 "## Key lines (on screen, in order, each from the line it starts on)\n(none)"):
        assert part in text


def test_the_command_names_the_run_for_its_commit_and_resumes_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scorecard, "RUNS", tmp_path)
    monkeypatch.setenv(brain.CLAUDE_KEY_VARS[0], "a fake key")
    fake = FakeAsk()
    monkeypatch.setattr(brain, "ask", fake)  # under the logging every topic's calls go through
    assert cli.main(["scorecard", "run", "--topics", "derivative,p-value"]) == 0
    [folder] = tmp_path.iterdir()
    card = json.loads((folder / "scorecard.json").read_text())
    assert folder.name.split("-")[2] == card["about"]["commit"][:7]
    assert card["about"]["topic_ids"] == ["derivative", "p-value"] and len(card["topics"]) == 2
    assert "| derivative | AP |" in capsys.readouterr().out
    (folder / "p-value" / "judge.json").unlink()
    fake.calls.clear()
    assert cli.main(["scorecard", "run", "--resume", folder.name]) == 0
    assert [c["label"] for c in fake.calls] == ["judge"]
    assert len(json.loads((folder / "scorecard.json").read_text())["topics"]) == 2


def test_a_judge_answer_that_does_not_fit_is_kept_for_a_look(tmp_path):
    made = {}

    def ask_for(job):
        made[job.name] = FakeAsk(fail="judge")
        return made[job.name]

    card = scorecard.run([topic("one")], tmp_path, ask_for=ask_for, about=ABOUT)
    assert card["topics"][0]["failed"]["step"] == "judge" and card["topics"][0]["made"]
    assert (tmp_path / "one" / "judge.invalid.json").read_text() == "{}"


def test_the_judges_errors_come_with_their_corrections(tmp_path):
    error = {"claim": 'lyric c1-4 "ATP and NADPH from the synthase"', "problem": "ATP synthase makes only ATP.",
             "correction": "NADPH is made at photosystem I's end of the chain."}
    ask_for, _ = asks(judge={**scores(4), "errors": [error]})
    card = scorecard.run([topic("one")], tmp_path, ask_for=ask_for, about=ABOUT)
    assert card["topics"][0]["errors"] == [error] and card["summary"]["errors"] == 1
    assert ("- Error: lyric c1-4 \"ATP and NADPH from the synthase\": ATP synthase makes only ATP. Correction: NADPH"
            in (tmp_path / "scorecard.md").read_text())
    assert "Never a claim that is fine" in json.dumps(brain.strict_schema(scorecard.Judgement))


def test_a_changed_rubric_judges_a_run_again_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(scorecard, "RUNS", tmp_path)
    monkeypatch.setenv(brain.CLAUDE_KEY_VARS[0], "a fake key")
    fake = FakeAsk(judge=scores(3))
    monkeypatch.setattr(brain, "ask", fake)
    assert cli.main(["scorecard", "run", "--topics", "derivative,p-value"]) == 0
    [folder] = tmp_path.iterdir()
    monkeypatch.setattr(scorecard, "RUBRIC", scorecard.RUBRIC + "\nA rule more.")
    assert cli.main(["scorecard", "run", "--resume", folder.name]) == 2  # the rubric changed
    assert cli.main(["scorecard", "run", "--rejudge"]) == 2  # which run?
    fake.calls.clear()
    fake.judge = scores(5)
    assert cli.main(["scorecard", "run", "--resume", folder.name, "--rejudge"]) == 0
    assert [c["label"] for c in fake.calls] == ["judge", "judge"]  # the bundles are kept
    card = json.loads((folder / "scorecard.json").read_text())
    assert card["about"]["rubric"] == scorecard.digest(scorecard.RUBRIC) and card["about"]["rejudged"]["commit"]
    assert [r["rubric"]["hook"]["score"] for r in card["topics"]] == [5, 5]
    assert json.loads((folder / "derivative" / "judge.previous.json").read_text())["hook"]["score"] == 3
    assert "judged again on" in (folder / "scorecard.md").read_text()


def test_a_rejudge_makes_no_bundle_and_records_each_go(tmp_path, monkeypatch):
    monkeypatch.setattr(scorecard, "RUNS", tmp_path)
    monkeypatch.setenv(brain.CLAUDE_KEY_VARS[0], "a fake key")
    fake = FakeAsk(fail="bundle")
    monkeypatch.setattr(brain, "ask", fake)
    assert cli.main(["scorecard", "run", "--topics", "derivative,p-value"]) == 1  # both bundles fail
    [folder] = tmp_path.iterdir()
    (folder / "p-value" / "failed.json").unlink()
    fake.fail, fake.calls = None, []
    assert cli.main(["scorecard", "run", "--resume", folder.name, "--rejudge"]) == 1
    assert fake.calls == []  # a re-judge spends cents: it never makes the bundles that failed
    card = json.loads((folder / "scorecard.json").read_text())
    assert [r["failed"]["step"] for r in card["topics"]] == ["bundle", "bundle"]
    assert len(card["about"]["resumed"]) == 1 and card["about"]["rejudged"] == card["about"]["resumed"][0]
    assert cli.main(["scorecard", "run", "--resume", folder.name]) == 0  # a plain resume makes them
    assert len(json.loads((folder / "scorecard.json").read_text())["about"]["resumed"]) == 2
    assert "resumed on" in (folder / "scorecard.md").read_text()


def test_a_bundle_made_again_is_judged_again(tmp_path):
    ask_for, _ = asks(judge=scores(2))
    scorecard.run([topic("one")], tmp_path, ask_for=ask_for, about=ABOUT)
    (tmp_path / "one" / "bundle.json").unlink()
    ask_for, made = asks(judge=scores(5))
    card = scorecard.run([topic("one")], tmp_path, ask_for=ask_for, about=ABOUT)
    assert made["one"].calls[-1]["label"] == "judge" and card["topics"][0]["rubric"]["hook"]["score"] == 5


def test_two_runs_never_share_a_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(scorecard, "RUNS", tmp_path)
    monkeypatch.setenv(brain.CLAUDE_KEY_VARS[0], "a fake key")
    monkeypatch.setattr(brain, "ask", FakeAsk())
    monkeypatch.setattr(scorecard, "run_name", lambda about: "same-minute")
    assert cli.main(["scorecard", "run", "--topics", "derivative"]) == 0
    assert cli.main(["scorecard", "run", "--topics", "p-value"]) == 2
    assert json.loads((tmp_path / "same-minute" / "scorecard.json").read_text())["about"]["topic_ids"] == ["derivative"]


def test_an_older_judgement_or_bundle_is_a_failed_step_not_a_lost_scorecard(tmp_path):
    ask_for, _ = asks()
    scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=ask_for, about=ABOUT)
    old = json.loads((tmp_path / "one" / "judge.json").read_text())
    (tmp_path / "one" / "judge.json").write_text(json.dumps({**old, "errors": ["an error as one line"]}))
    (tmp_path / "two" / "bundle.json").write_text('{"topic": "cut short"')
    card = scorecard.run([topic("one"), topic("two")], tmp_path, ask_for=asks()[0], about=ABOUT)
    one, two = card["topics"]
    assert "--rejudge" in one["failed"]["error"] and not one["rubric"]
    assert two["failed"]["step"] == "bundle" and not two["made"]
    assert (tmp_path / "scorecard.md").exists()


def test_compare_or_resume_of_a_run_not_there_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scorecard, "RUNS", tmp_path)
    monkeypatch.setenv(brain.CLAUDE_KEY_VARS[0], "a fake key")
    assert cli.main(["scorecard", "compare", "nope-a", "nope-b"]) == 2
    assert cli.main(["scorecard", "run", "--resume", "nope"]) == 2
    assert "no run nope" in capsys.readouterr().err
