import pytest

from studio import brain
from studio.jobs import read, write
from studio.styles import style_for
from studio.schemas import Bundle, Request
from studio.steps import bundle as bundle_step
from studio.tests.sample import bundle_data


def test_run_writes_the_bundle_and_drops_a_stale_fact_check(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions", course="AP Bio"))
    (tmp_path / "factcheck.json").write_text("{}")
    seen, labels = {}, []

    def fake_ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        labels.append(label)
        if label == "script":  # no script: the bundle is written without one
            raise brain.BrainError("no answer")
        if label == "narration edit":  # the editor hands the narration back as written
            return {"lines": [{"id": n["id"], "text": n["text"]} for n in bundle_data()["narration"]]}
        seen.update(role=role, prompt=prompt, schema=schema, system=system, label=label)
        return bundle_data()

    bundle = bundle_step.run(tmp_path, ask=fake_ask)
    assert labels == ["script", "bundle", "narration edit"]
    assert seen["role"] == "brain" and seen["schema"] is Bundle and seen["label"] == "bundle"
    assert seen["system"] == bundle_step.PROMPT and "Teach the why, not the parts list" in seen["system"]
    assert seen["prompt"].startswith("Topic: the light reactions\n\nCourse: AP Bio\n\nThe song style for this droplet, "
                                     "from the studio's rotation: ")
    assert style_for(tmp_path.name) in seen["prompt"]
    assert read(tmp_path / "bundle.json", Bundle) == bundle
    assert not (tmp_path / "factcheck.json").exists()


def test_the_prompt_holds_for_topics_outside_science():
    # Six droplets on Oct 29 (literature, history, calculus, economics, chemistry, biology): outside science the
    # brain sent unmarked dots between characters, ended the narration on "in goes a function, out comes a
    # number", and sang the verses' story again in the bridge.
    prompt = " ".join(bundle_step.PROMPT.split())
    assert "never a route made up to keep the dots busy" in prompt and "their scenes hold still" in prompt
    assert 'named in a plain word ("price", "letter", "taxes")' in prompt
    assert "or, for any other idea, its cause and its result, and why it matters" in prompt
    assert "the bridge takes the other case the facts give" in prompt and "another case the same idea explains" in prompt


def test_the_facts_say_the_misconception_is_a_common_one():
    # The lesson names the common mistake; with no fact saying it is common, the fact-check rewrote "Many students
    # think..." in two of six droplets on Oct 29 (Newton's third law, p-values).
    prompt = " ".join(bundle_step.PROMPT.split())
    assert "and that the mistake is a common one" in prompt


def test_a_grave_topic_sets_its_own_slow_style_over_the_rotation():
    # On Oct 29 the Holocaust droplet kept the rotation's 112 bpm and its four-on-the-floor beat, since the rotation's
    # tempo was the tempo.
    prompt = bundle_step.user_prompt(Request(topic="Why the Holocaust happened"), "dance-pop with a four-on-the-floor "
                                     "beat, 112 bpm")
    assert "a grave topic (a genocide, slavery) the grave topic's own slow style" in prompt
    rules = " ".join(bundle_step.PROMPT.split())
    assert "A grave topic sets its own style**, whatever the rotation says" in rules
    assert "80 to 90 bpm" in rules and "No dance beat" in rules and "never a full or epic orchestra" in rules
    # The first Holocaust droplet carried "trains" to occupied Poland; the slave trade's carried "people" south.
    assert "The people such a topic is about are never particles" in rules


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


def test_the_learners_own_song_style_wins_over_the_rotation():
    prompt = bundle_step.user_prompt(Request(topic="atp", song_style="K-pop"), "indie-pop singalong, 110 bpm")
    assert "The song style the learner asked for: K-pop" in prompt and "rotation" not in prompt
