import pytest

from studio import brain
from studio.jobs import read, write
from studio.schemas import Bundle, Request
from studio.steps import bundle as bundle_step
from studio.tests.sample import bundle_data


def test_run_writes_the_bundle_and_drops_a_stale_fact_check(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions", course="AP Bio"))
    (tmp_path / "factcheck.json").write_text("{}")
    seen = {}

    def fake_ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        seen.update(role=role, prompt=prompt, schema=schema, system=system, label=label)
        return bundle_data()

    bundle = bundle_step.run(tmp_path, ask=fake_ask)
    assert seen["role"] == "brain" and seen["schema"] is Bundle and seen["label"] == "bundle"
    assert seen["system"] == bundle_step.PROMPT and "Big picture first" in seen["system"]
    assert seen["prompt"] == "Topic: the light reactions\n\nCourse: AP Bio"
    assert read(tmp_path / "bundle.json", Bundle) == bundle
    assert not (tmp_path / "factcheck.json").exists()


def test_the_prompt_carries_the_request_and_study_guide():
    prompt = bundle_step.user_prompt(Request(topic="ATP", request="for my exam", study_guide="ATP is made.",
                                             images=["/notes.png"]))
    assert "The learner's request, in their words: for my exam" in prompt
    assert "1 photo(s) of their notes" in prompt
    assert prompt.endswith("<study_guide>\nATP is made.\n</study_guide>")


def test_the_prompt_carries_the_learners_song_style():
    prompt = bundle_step.user_prompt(Request(topic="ATP", song_style="K-pop with hip-hop verses, 98 bpm"))
    assert prompt == "Topic: ATP\n\nThe song style the learner asked for: K-pop with hip-hop verses, 98 bpm"
    assert "The song style" not in bundle_step.user_prompt(Request(topic="ATP"))


def test_an_answer_that_does_not_validate_is_kept_for_a_look(tmp_path):
    write(tmp_path / "request.json", Request(topic="ATP"))

    def fake_ask(*args, **kwargs):
        raise brain.InvalidOutput("does not fit", '{"course": "x"}')

    with pytest.raises(brain.InvalidOutput):
        bundle_step.run(tmp_path, ask=fake_ask)
    assert (tmp_path / "bundle.invalid.json").read_text() == '{"course": "x"}'
    assert not (tmp_path / "bundle.json").exists()
