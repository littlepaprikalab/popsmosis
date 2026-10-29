from studio.schemas import Bundle
from studio.steps import learner
from studio.tests.sample import bundle_data


def essence_bundle(**changes):
    data = {**bundle_data(), "why_chain": ["A because B", "C because D", "E because F"],
            "key_terms": ["photosystem II"], **changes}
    return Bundle.model_validate(data)


class FakeAsk:
    """Answers each call by its label, and keeps what each was asked."""

    def __init__(self, grades, lesson="# A clearer lesson"):
        self.grades, self.lesson, self.calls = list(grades), lesson, []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.calls.append((role, label, prompt, system))
        if label.endswith("grade"):
            return self.grades.pop(0)
        if label == "lesson rewrite":
            return {"lesson_markdown": self.lesson}
        return {"big_idea": "light makes energy", "why": "it just does", "what_if": "nothing"}


def test_a_lesson_whose_essence_is_learned_is_kept(tmp_path):
    ask = FakeAsk([{"core_idea": True, "links": [True, True, False], "missing": []}])
    bundle = essence_bundle()
    assert learner.run(tmp_path, bundle, ask, "rules") is bundle
    assert [label for _, label, _, _ in ask.calls] == ["learner", "learner grade"]
    # The learner reads only the lesson: never the core idea or the why chain it is graded against.
    learner_prompt = ask.calls[0][2]
    assert bundle.lesson_markdown in learner_prompt and "A because B" not in learner_prompt
    assert "A because B" in ask.calls[1][2]


def test_a_lesson_whose_essence_is_missed_is_rewritten_once_and_tested_again(tmp_path):
    missed = {"core_idea": False, "links": [True, False, False], "missing": ["why C follows from D"]}
    ask = FakeAsk([missed, missed])
    bundle = learner.run(tmp_path, essence_bundle(), ask, "rules")
    assert bundle.lesson_markdown == "# A clearer lesson"
    assert [label for _, label, _, _ in ask.calls] == [
        "learner", "learner grade", "lesson rewrite", "learner again", "learner again grade"]
    rewrite = ask.calls[2]
    assert rewrite[0] == "brain" and rewrite[3] == "rules" and "why C follows from D" in rewrite[2]
    record = (tmp_path / "learner.json").read_text()
    assert '"passed": false' in record and '"rewritten"' in record  # recorded, never a failed droplet


def test_an_older_bundle_or_a_failing_model_leaves_the_lesson_as_it_is(tmp_path):
    older = Bundle.model_validate(bundle_data())
    assert learner.run(tmp_path, older, None, "rules") is older  # no why chain: nothing to grade against

    def broken(*args, **kwargs):
        raise RuntimeError("the helper is down")

    bundle = essence_bundle()
    assert learner.run(tmp_path, bundle, broken, "rules") is bundle
    assert "the helper is down" in (tmp_path / "learner.json").read_text()
