import json

from studio import brain
from studio.jobs import write
from studio.schemas import Bundle, Request
from studio.steps import bundle as bundle_step
from studio.steps import script
from studio.tests.sample import bundle_data


def script_data(**changes) -> dict:
    """A script the sample bundle keeps to: its facts, its scenes, and its narration reworded."""
    data = bundle_data()
    out = {
        "audience": "high school", "question": "Where does a leaf's ATP come from?",
        "core_idea": "Light pumps protons, and their flow back makes ATP.", "why_chain": [],  # no learner test
        "key_terms": ["ATP synthase"], "facts": data["facts"], "misconception": "A common mistake is to think so.",
        "scenes": [{"id": "hook", "picture": "A leaf."}, {"id": "overview", "picture": "A thylakoid membrane."},
                   {"id": "synthase", "picture": "ATP synthase up close."}],
        "narration": [{"scene": "overview", "lines": ["The light reactions happen in   the thylakoid membrane."]},
                      {"scene": "synthase", "lines": ["So ATP synthase uses the returning protons to make ATP.", " "]}],
    }
    out.update(changes)
    return out


def asking(answers):
    """Stands in for brain.ask: answers each call by its label."""
    asked = []

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        asked.append((label, prompt, system))
        answer = answers[label]
        if isinstance(answer, Exception):
            raise answer
        return answer(prompt) if callable(answer) else answer

    ask.asked = asked
    return ask


def unchanged_narration(prompt):
    """The editor handing the narration back as it was given."""
    return {"lines": [{"id": n["id"], "text": n["text"]} for n in json.loads(prompt)["narration"]]}


def test_the_script_is_written_first_and_the_bundle_keeps_its_essence_facts_and_narration(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions", course="AP Bio"))
    ask = asking({"script": script_data(), "bundle": bundle_data(), "narration edit": unchanged_narration})
    bundle = bundle_step.run(tmp_path, ask=ask)
    # The facts' audit begins beside the bundle call; with no answer here it is left to its own step.
    labels = [a[0] for a in ask.asked]
    assert labels.count("fact audit") == 1 and [x for x in labels if x != "fact audit"] == [
        "script", "bundle", "narration edit"]
    (_, first, system), (_, second, _) = [a for a in ask.asked if a[0] != "fact audit"][:2]
    assert "## The question" in system and "## The audience" in system and "## The facts" in system
    assert "## The song" not in system  # the script writer gets the brief's audience and facts, not the rest
    assert second.startswith(first) and "<script>" in second and '"id": "n2"' in second
    assert '"text": "The light reactions happen in the thylakoid membrane."' in second  # one space between words
    assert bundle.core_idea == "Light pumps protons, and their flow back makes ATP."
    assert [n.text for n in bundle.narration] == ["The light reactions happen in the thylakoid membrane.",
                                                  "So ATP synthase uses the returning protons to make ATP."]
    record = json.loads((tmp_path / script.RECORD).read_text())
    assert record["kept"] == {"essence": True, "facts": True, "narration": True, "scenes_as_written": 2, "reworded": 1}


def test_a_bundle_that_will_not_fit_its_script_keeps_its_own_facts_and_narration(tmp_path):
    longer = script_data(narration=[{"scene": "overview", "lines": ["One.", "Two.", "Three."]}])
    renamed = script_data(facts=[dict(f, id=f"g{k}") for k, f in enumerate(bundle_data()["facts"])])
    for data in (longer, renamed):
        b = Bundle.model_validate(bundle_data())
        out = script.follow(b, script.Script.model_validate(data), tmp_path)
        assert out.core_idea == data["core_idea"]
        kept = json.loads((tmp_path / script.RECORD).read_text())["kept"]
        if data is longer:
            assert out.narration == b.narration and not kept["narration"] and kept["facts"]
        else:
            assert out.facts == b.facts and not kept["facts"] and kept["narration"]


def test_each_sentence_plays_over_the_scene_its_passage_names_where_the_bundle_draws_it(tmp_path):
    moved = script_data(narration=[{"scene": "synthase", "lines": ["One."]}, {"scene": "elsewhere", "lines": ["Two."]}])
    out = script.follow(Bundle.model_validate(bundle_data()), script.Script.model_validate(moved), tmp_path)
    scenes = {d.line_id: d.scene for d in out.direction}
    assert (scenes["n1"], scenes["n2"]) == ("synthase", "synthase")  # n2 kept its own: no scene "elsewhere"
    kept = json.loads((tmp_path / script.RECORD).read_text())["kept"]
    assert kept["scenes_as_written"] == 0  # what the writer did, before the scenes were set


def test_without_a_script_the_bundle_is_written_as_before(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    for answer in (brain.BrainError("no answer"), script_data(narration=[{"scene": "elsewhere", "lines": ["Hi."]}])):
        ask = asking({"script": answer, "bundle": bundle_data(), "narration edit": unchanged_narration})
        bundle = bundle_step.run(tmp_path, ask=ask)
        assert "<script>" not in ask.asked[1][1]
        assert bundle.core_idea == bundle_data()["core_idea"]
        assert json.loads((tmp_path / script.RECORD).read_text())["why"]


def test_the_script_writer_hears_there_is_no_song_only_when_there_is_none():
    with_song = script.system_prompt(bundle_step.PROMPT)
    assert "No song for this droplet" not in with_song  # with the song on, as it always was
    without = script.system_prompt(bundle_step.LESSON_ONLY_PROMPT)
    assert "## No song for this droplet" in without and "there is no songwriter" in without
    assert "## The plan without the song" not in without  # the plan's rules are the bundle writer's


def test_facts_that_share_an_id_are_numbered_before_the_bundle_sees_them(tmp_path):
    # Muse Wonder gave every fact the id "[generated]" (Nov 2): the bundle would cite one id for four facts.
    facts = [dict(f, id="[generated]") for f in bundle_data()["facts"]]
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    ask = asking({"script": script_data(facts=facts), "bundle": bundle_data(), "narration edit": unchanged_narration})
    bundle_step.run(tmp_path, ask=ask)
    second = [a for a in ask.asked if a[0] == "bundle"][0][1]
    wanted = [f"f{k}" for k in range(1, len(facts) + 1)]
    assert [f["id"] for f in json.loads(second.split("<script>\n")[1].split("\n</script>")[0])["facts"]] == wanted
    assert [f["id"] for f in json.loads((tmp_path / script.RECORD).read_text())["script"]["facts"]] == wanted


def test_facts_numbered_their_own_way_keep_their_ids():
    data = script_data()
    kept = script.numbered(script.Script.model_validate(data))
    assert [f.id for f in kept.facts] == [f["id"] for f in data["facts"]]
    reordered = script_data(facts=list(reversed(data["facts"])))
    assert [f.id for f in script.numbered(script.Script.model_validate(reordered)).facts] == [
        f["id"] for f in reversed(data["facts"])]
