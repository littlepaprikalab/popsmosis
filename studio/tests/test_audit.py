import json

import pytest

from studio import jobs
from studio import run as operator_run
from studio.jobs import read, write
from studio.schemas import AuditReport, Bundle, FactAudit, FactCheck, Request
from studio.steps import audit, factcheck
from studio.steps import bundle as bundle_step
from studio.steps.factcheck import check_bundle
from studio.tests.sample import FACTS, bundle_data
from studio.tests.test_run import FakeApi

# The sample bundle's seeded error, moved from a lyric into the fact the lyric was written from: the fact-check
# holds lines to the facts, so a lyric that says what a wrong fact says passes it.
WRONG = "Every electron hand-off in the thylakoid membrane pumps a proton into the thylakoid lumen."
RIGHT = FACTS[1]["text"]  # "Only cytochrome b6f pumps protons into the thylakoid lumen; the other carriers..."
SEEDED = "Every electron hand-off pumps a proton"  # lyric v1-2, written from f2
FIXED = "Only b6f pumps a proton through"
GUIDE = "In the light reactions, only cytochrome b6f pumps protons into the lumen. ATP synthase makes ATP."


def seeded_data():
    data = bundle_data()
    data["facts"][1]["text"] = WRONG
    return data


def knows_b6f(text):
    """An auditor that knows only cytochrome b6f pumps: every other fact is correct."""
    if "Every electron hand-off" in text:
        return {"verdict": "WRONG", "problem": "Only cytochrome b6f pumps protons; the other carriers do not.",
                "corrected": RIGHT}
    return {"verdict": "CORRECT"}


def judge(payload, rule):
    """The auditor's answer: rule(text) for each fact, or None to leave it out."""
    judged = []
    for fact in payload["facts"]:
        verdict = rule(fact["text"])
        if verdict is not None:
            judged.append({"id": fact["id"], "basis": "course knowledge", "quote": "", "problem": "", "corrected": "",
                           **verdict})
    return FactAudit.model_validate({"facts": judged}).model_dump()


def check_by_the_facts(payload):
    """A fact-checker that holds each line to the facts it is sent and nothing else: the seeded lyric is supported
    while a fact says every hand-off pumps, and corrected once none does."""
    says_every = [f["id"] for f in payload["facts"] if "every electron hand-off" in f["text"].lower()]
    checks = []
    for line in payload["lines"]:
        if "every electron hand-off" in line["text"].lower() and not says_every:
            checks.append({"id": line["id"], "verdict": "UNSUPPORTED", "source": "",
                           "problem": "only cytochrome b6f pumps", "corrected": FIXED})
        else:
            checks.append({"id": line["id"], "verdict": "SUPPORTED", "source": says_every[0] if says_every and
                           "every" in line["text"].lower() else "f1", "problem": "", "corrected": ""})
    return FactCheck.model_validate({"checks": checks}).model_dump()


class FakeAuditor:
    """Stands in for brain.ask in the audit: judges each fact with `rule`, and keeps every payload and label."""

    def __init__(self, rule=knows_b6f):
        self.rule, self.payloads, self.labels = rule, [], []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        assert role == "brain" and schema is FactAudit and system == audit.PROMPT
        self.payloads.append(json.loads(prompt))
        self.labels.append(label)
        return judge(self.payloads[-1], self.rule)

    def sent(self, call):
        return [(f["id"], f["text"]) for f in self.payloads[call - 1]["facts"]]


class Studio:
    """brain.ask for a whole run: the seeded bundle, an auditor that knows only cytochrome b6f pumps, and a
    fact-checker that holds each line to the facts it is sent."""

    def __init__(self, rule=knows_b6f):
        self.auditor = FakeAuditor(rule)
        self.labels, self.checks = [], []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.labels.append(label)
        if schema is Bundle:
            return seeded_data()
        if schema is FactAudit:
            return self.auditor(role, prompt, schema, system=system, label=label)
        assert schema is FactCheck and system == factcheck.PROMPT
        self.checks.append(json.loads(prompt))
        return check_by_the_facts(self.checks[-1])


@pytest.fixture
def job(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions", course="AP Biology"))
    write(tmp_path / "bundle.json", Bundle.model_validate(seeded_data()))
    return tmp_path


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    return tmp_path


# --- the seeded error -------------------------------------------------------------


def test_without_the_audit_a_wrong_fact_passes_the_fact_check_with_the_lines_written_from_it():
    report, checked = check_bundle(Bundle.model_validate(seeded_data()), "", Studio())
    assert report.status == "passed" and report.corrections == []
    assert checked.lyrics[1].text == SEEDED and checked.facts[1].text == WRONG


def test_the_audit_catches_the_wrong_fact_and_the_fact_check_then_corrects_the_lines_written_from_it(job):
    auditor = FakeAuditor()
    report = audit.run(job, ask=auditor)
    assert report.status == "passed" and report.calls == 2
    assert auditor.labels == ["fact audit", "fact audit again"]
    # The second call judges the correction on its own: only the corrected fact, as corrected.
    assert auditor.sent(2) == [("f2", RIGHT)]
    [correction] = report.corrections
    assert (correction.id, correction.before, correction.after) == ("f2", WRONG, RIGHT)
    assert correction.cited_by == ["v1-2", "v2-1"] and correction.basis == "course knowledge"
    assert [(f.id, f.verdict, f.call) for f in report.facts] == [("f1", "CORRECT", 1), ("f2", "CORRECT", 2),
                                                                ("f3", "CORRECT", 1)]
    assert read(job / "audit.json", AuditReport) == report
    assert checks_facts(job) == [FACTS[0]["text"], RIGHT, FACTS[2]["text"]]
    assert json.loads((job / "bundle.draft.json").read_text())["facts"][1]["text"] == WRONG  # the writer's

    checker = Studio()
    checked = factcheck.run(job, ask=checker)
    assert checked.status == "passed" and checked.rounds == 2
    sent = checker.checks[0]
    # The corrected fact comes with what it said before, and the lines written from it say so.
    assert sent["facts"][1] == {"id": "f2", "text": RIGHT, "was": WRONG}
    assert "was" not in sent["facts"][0] and "was" not in sent["facts"][2]
    marked = {line["id"]: line.get("written_from") for line in sent["lines"] if "written_from" in line}
    assert marked == {"lyric:v1-2": "f2", "lyric:v2-1": "f2"}
    [fix] = checked.corrections
    assert (fix.ids, fix.before, fix.after) == (["v1-2"], SEEDED, FIXED)
    bundle = Bundle.model_validate_json((job / "bundle.json").read_text())
    assert bundle.lyrics[1].text == FIXED and bundle.facts[1].text == RIGHT
    draft = json.loads((job / "bundle.draft.json").read_text())  # still the writer's, from before the audit
    assert draft["facts"][1]["text"] == WRONG and draft["lyrics"][1]["text"] == SEEDED


def test_a_whole_run_posts_the_corrected_fact_and_the_lines_rechecked_against_it(jobs_root):
    api, ask = FakeApi(), Studio()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.labels == ["bundle", "fact audit", "fact audit again", "factcheck round 1", "factcheck round 2"]
    posted = api.posted["bundle"]
    assert posted["facts"][1]["text"] == RIGHT and posted["lyrics"][1]["text"] == FIXED
    assert SEEDED not in json.dumps(posted) and WRONG not in json.dumps(posted)
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    audit_line = next(s for s in record["steps"] if s["step"] == "audit")
    # The question's text file is a study guide, but it says nothing of these facts: course knowledge judged them.
    assert audit_line["detail"] == "3 fact(s) judged by course knowledge, 1 corrected (f2), in 2 call(s)"
    assert "Fact audit passed: 3 fact(s) judged by course knowledge, 1 corrected (f2)" in operator_run.summary("q1")


def checks_facts(job):
    return [f["text"] for f in json.loads((job / "bundle.json").read_text())["facts"]]


# --- what fails a droplet -----------------------------------------------------------


def test_a_correction_judged_wrong_again_fails_the_step_and_changes_nothing(job):
    def never_right(text):
        if "Every electron hand-off" in text or text == "Most carriers pump.":
            return {"verdict": "WRONG", "problem": "still wrong", "corrected": "Most carriers pump."}
        return {"verdict": "CORRECT"}

    before = (job / "bundle.json").read_text()
    report = audit.run(job, ask=FakeAuditor(never_right))
    assert report.status == "failed" and report.calls == 2
    assert report.reason == "fact f2 is still wrong after its correction: still wrong"  # corrected only once
    assert (job / "bundle.json").read_text() == before and not (job / "bundle.draft.json").exists()
    assert read(job / "audit.json", AuditReport).corrections[0].after == "Most carriers pump."
    assert not audit.is_done(job)


def test_a_wrong_fact_without_a_correction_fails_after_one_call(job):
    auditor = FakeAuditor(lambda text: {"verdict": "WRONG", "problem": "not so"} if "hand-off" in text
                          else {"verdict": "CORRECT"})
    report = audit.run(job, ask=auditor)
    assert report.status == "failed" and auditor.labels == ["fact audit"]
    assert report.reason == "fact f2 is wrong and the audit gave no correction: not so"


def test_a_run_whose_audit_fails_never_reaches_the_fact_check_and_retries_the_audit(jobs_root):
    first = Studio(lambda text: {"verdict": "WRONG", "problem": "not so"} if "hand-off" in text
                   else {"verdict": "CORRECT"})
    result, step, reason = operator_run.run("q1", api=FakeApi(), ask=first)
    assert (result, step) == ("FAILED", "audit") and reason.startswith("fact f2 is wrong")
    assert first.labels == ["bundle", "fact audit"]
    second = Studio()
    assert operator_run.run("q1", api=FakeApi(), ask=second)[0] == "DONE"
    assert second.labels[0] == "fact audit"  # the bundle was kept


def test_an_unsure_fact_is_kept_and_recorded_never_failing_the_droplet(job):
    def unsure_of_f3(text):
        if text == FACTS[2]["text"]:
            return {"verdict": "UNSURE", "problem": "Whether this course says the protons return to the stroma."}
        return {"verdict": "CORRECT"}

    data = bundle_data()  # f2 right this time
    write(job / "bundle.json", Bundle.model_validate(data))
    auditor = FakeAuditor(unsure_of_f3)
    report = audit.run(job, ask=auditor)
    assert report.status == "passed" and report.calls == 1 and report.corrections == []
    [doubt] = audit.unsure(report)
    assert (doubt.id, doubt.text, doubt.call) == ("f3", FACTS[2]["text"], 1) and doubt.problem.startswith("Whether")
    assert checks_facts(job)[2] == FACTS[2]["text"] and not (job / "bundle.draft.json").exists()
    assert audit.describe(report) == "3 fact(s) judged by course knowledge, 1 unsure (f3), in 1 call(s)"
    # Nothing was corrected, so the fact-check is sent exactly what it was before the audit.
    checker = Studio()
    factcheck.run(job, ask=checker)
    assert not any("was" in f for f in checker.checks[0]["facts"])
    assert not any("written_from" in line for line in checker.checks[0]["lines"])


class SkipsF1:
    """An auditor's rule that leaves fact f1 out of its first answer, then judges it `then` (None: out again)."""

    def __init__(self, then):
        self.then, self.skipped = then, False

    def __call__(self, text):
        if text != FACTS[0]["text"]:
            return knows_b6f(text)
        if not self.skipped:
            self.skipped = True
            return None
        return self.then


def test_a_fact_left_without_a_verdict_is_asked_again_once(job):
    auditor = FakeAuditor(SkipsF1({"verdict": "CORRECT"}))
    report = audit.run(job, ask=auditor)
    assert report.status == "passed" and report.calls == 2
    assert auditor.sent(2) == [("f1", FACTS[0]["text"]), ("f2", RIGHT)]  # the one skipped, and the correction


@pytest.mark.parametrize("then, reason", [
    (None, "no verdict on fact f1 after 2 call(s)"),
    ({"verdict": "WRONG", "problem": "no", "corrected": "x"},
     "fact f1 is wrong, and was first judged in the last call, so no call is left to judge a correction: no"),
])
def test_every_fact_must_end_judged_correct_or_unsure(job, then, reason):
    report = audit.run(job, ask=FakeAuditor(SkipsF1(then)))
    assert (report.status, report.reason, report.calls) == ("failed", reason, 2)


# --- the study guide ----------------------------------------------------------------


def test_with_a_study_guide_a_fact_is_judged_by_it_and_its_quote_must_be_in_it(job):
    write(job / "request.json", Request(topic="the light reactions", study_guide=GUIDE))
    data = bundle_data()
    data["facts"][1]["source_quote"] = "only cytochrome b6f pumps protons into the lumen"
    write(job / "bundle.json", Bundle.model_validate(data))

    def by_the_guide(text):
        if text == RIGHT:
            return {"verdict": "CORRECT", "basis": "study guide", "quote": "“only cytochrome b6f pumps protons”"}
        if text == FACTS[2]["text"]:  # a quote the guide does not have: judged by what the auditor knows
            return {"verdict": "CORRECT", "basis": "study guide", "quote": "ATP synthase spins like a turbine"}
        return {"verdict": "CORRECT"}

    auditor = FakeAuditor(by_the_guide)
    report = audit.run(job, ask=auditor)
    sent = auditor.payloads[0]
    assert sent["study_guide"] == GUIDE
    assert (sent["course"], sent["topic"], sent["audience"]) == ("AP Biology", "the light reactions", "high school")
    assert sent["facts"][1]["source_quote"] == "only cytochrome b6f pumps protons into the lumen"
    assert "source_quote" not in sent["facts"][0]  # "" is not sent
    assert report.judged_against == "study guide"
    assert [(f.basis, f.quote) for f in report.facts] == [
        ("course knowledge", ""), ("study guide", "“only cytochrome b6f pumps protons”"), ("course knowledge", "")]
    assert audit.describe(report) == "3 fact(s) judged, 1 by the study guide, in 1 call(s)"


def test_without_a_study_guide_every_fact_is_judged_by_course_knowledge_and_the_report_says_so(job):
    auditor = FakeAuditor(lambda text: {"verdict": "CORRECT", "basis": "study guide",
                                        "quote": "only cytochrome b6f pumps protons"})
    report = audit.run(job, ask=auditor)
    assert "study_guide" not in auditor.payloads[0]
    assert report.judged_against == "course knowledge" and report.audience == "high school"
    assert {f.basis for f in report.facts} == {"course knowledge"}


# --- recovery ---------------------------------------------------------------------


def test_the_audit_is_done_only_when_it_passed_the_facts_the_bundle_has_now(job):
    assert not audit.is_done(job)  # no audit.json
    audit.run(job, ask=FakeAuditor())
    assert audit.is_done(job) and operator_run.is_done(job, "audit")
    data = json.loads((job / "bundle.json").read_text())
    data["facts"][0]["text"] = "The light reactions happen in the stroma."  # changed by hand: audit again
    (job / "bundle.json").write_text(json.dumps(data))
    assert not audit.is_done(job)
    (job / "audit.json").write_text('{"status": "pass')  # cut short
    assert not audit.is_done(job)


def test_an_audit_makes_an_earlier_fact_check_stale_and_a_new_bundle_an_earlier_audit(job):
    (job / "factcheck.json").write_text("{}")
    audit.run(job, ask=FakeAuditor())
    assert not (job / "factcheck.json").exists() and (job / "audit.json").exists()
    bundle_step.run(job, ask=lambda *args, **kwargs: bundle_data())
    assert not (job / "audit.json").exists() and not (job / "bundle.draft.json").exists()


@pytest.mark.parametrize("killed_at", ["audit.json", "bundle.json"])
def test_an_audit_stopped_while_it_writes_is_done_again_and_keeps_its_corrections(job, monkeypatch, killed_at):
    real = audit.write

    def stopped(path, data):
        if path.name == killed_at:
            raise KeyboardInterrupt  # killed as it writes this file, after any written before it
        real(path, data)

    monkeypatch.setattr(audit, "write", stopped)
    with pytest.raises(KeyboardInterrupt):
        audit.run(job, ask=FakeAuditor())
    monkeypatch.setattr(audit, "write", real)
    assert not audit.is_done(job)  # no audit.json, or one that passed other facts than bundle.json has
    report = audit.run(job, ask=FakeAuditor())
    assert [c.id for c in report.corrections] == ["f2"] and audit.is_done(job)
    assert factcheck.audit_corrections(job, Bundle.model_validate_json((job / "bundle.json").read_text())) == {
        "f2": WRONG}


def test_a_run_killed_in_the_audit_audits_again(jobs_root):
    first = Studio(lambda text: {"verdict": "WRONG", "problem": "not so"} if "hand-off" in text
                   else {"verdict": "CORRECT"})
    operator_run.run("q1", api=FakeApi(), ask=first)
    job = jobs_root / "q1"
    (job / "run.current.json").write_text(json.dumps({"started": "t", "step": "audit", "steps": []}))
    ask = Studio()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    assert ask.labels[:2] == ["fact audit", "fact audit again"]
    runs = operator_run.read_jsonl(job / "runs.jsonl")
    assert [(r["result"], r["step"]) for r in runs] == [("FAILED", "audit"), ("FAILED", "audit"), ("DONE", "post")]


def test_a_job_fact_checked_before_the_audit_existed_is_audited_and_checked_again_unless_posted(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = OSError("down")  # a bug, as far as run knows: the post fails, the rest stays
    operator_run.run("q1", api=api, ask=Studio())
    job = jobs_root / "q1"
    (job / "audit.json").unlink()  # as a studio before the audit left it: a passed fact-check, no audit
    assert operator_run.is_done(job, "factcheck") and not operator_run.is_done(job, "audit")
    ask = Studio()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    assert ask.labels[0] == "fact audit" and "factcheck round 1" in ask.labels

    (job / "audit.json").unlink()  # a posted lesson is never made again
    ask = Studio()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[2] == "the lesson was already posted"
    assert ask.labels == []


def test_the_planner_record_hears_what_the_audit_corrected(jobs_root):
    from studio import inkmory

    operator_run.run("q1", api=FakeApi(), ask=Studio())
    facts = inkmory.what_happened(jobs_root / "q1")
    assert facts["audit"]["corrected"] == [{"id": "f2", "before": WRONG, "after": RIGHT,
                                           "problem": "Only cytochrome b6f pumps protons; the other carriers do not."}]
    assert facts["audit"]["unsure"] == [] and facts["audit"]["status"] == "passed"
