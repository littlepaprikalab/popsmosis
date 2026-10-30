import json

from studio import brain
from studio.jobs import read, write
from studio.schemas import Bundle
from studio.steps import narration
from studio.tests.sample import bundle_data


def bundle() -> Bundle:
    return Bundle.model_validate(bundle_data())


def answering(lines=None, error=None):
    """Stands in for brain.ask: answers the editor with these lines, or raises."""
    asked = []

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        asked.append((role, json.loads(prompt), system, label))
        if error:
            raise error
        return {"lines": lines}

    ask.asked = asked
    return ask


def test_the_narration_comes_back_in_its_new_order_and_each_sentence_keeps_its_direction(tmp_path):
    b = bundle()
    (first, second) = b.narration
    ask = answering([{"id": second.id, "text": second.text}, {"id": first.id, "text": "  So   " + first.text}])
    out = narration.edit(b, ask, tmp_path)
    assert [n.id for n in out.narration] == [second.id, first.id]
    assert out.narration[1].text == "So " + first.text and out.narration[1].names == first.names
    spoken = {first.id, second.id}
    assert [d.line_id for d in out.direction if d.line_id in spoken] == [second.id, first.id]  # a direction follows
    assert [d.line_id for d in out.direction if d.line_id not in spoken] == [
        d.line_id for d in b.direction if d.line_id not in spoken]  # the lyrics' as they were
    (role, sent, system, label), = ask.asked
    assert role == "brain" and label == "narration edit" and system == narration.PROMPT
    assert [n["id"] for n in sent["narration"]] == [first.id, second.id] and sent["narration"][0]["scene"]
    assert sent["facts"] and sent["core_idea"] == b.core_idea
    record = json.loads((tmp_path / narration.RECORD).read_text())
    assert record["kept"] is True and record["after"][0]["id"] == second.id


def test_an_edit_that_drops_a_sentence_runs_long_or_fails_leaves_the_narration_as_written(tmp_path):
    b = bundle()
    first, second = b.narration
    dropped = answering([{"id": first.id, "text": first.text}])
    assert narration.edit(b, dropped, tmp_path) is b
    assert "exactly once" in json.loads((tmp_path / narration.RECORD).read_text())["why"]
    long = answering([{"id": first.id, "text": first.text + " and" * 12},  # each sentence short enough...
                      {"id": second.id, "text": second.text + " and" * 12}])  # ...but the whole far longer
    assert narration.edit(b, long) is b
    empty = answering([{"id": first.id, "text": " "}, {"id": second.id, "text": second.text}])
    assert narration.edit(b, empty) is b
    assert narration.edit(b, answering(error=brain.BrainError("no answer"))) is b


def test_the_step_by_itself_edits_the_bundle_and_the_fact_check_must_run_again(tmp_path):
    b = bundle()
    write(tmp_path / "bundle.json", b)
    (tmp_path / "factcheck.json").write_text("{}")
    first, second = b.narration
    said = narration.run(tmp_path, answering([{"id": second.id, "text": second.text},
                                              {"id": first.id, "text": first.text}]))
    assert said == "narration edited: 2 sentences in new places, 0 reworded"
    assert [n.id for n in read(tmp_path / "bundle.json", Bundle).narration] == [second.id, first.id]
    assert not (tmp_path / "factcheck.json").exists() and (tmp_path / "checks.json").exists()


def test_a_sentence_the_editor_draws_out_past_the_longest_keeps_the_writers_words(tmp_path):
    b = bundle()
    first, second = b.narration
    longer = first.text + " and so on, " * 6
    out = narration.edit(b, answering([{"id": second.id, "text": second.text}, {"id": first.id, "text": longer}]))
    assert [n.id for n in out.narration] == [second.id, first.id]  # still moved
    assert out.narration[1].text == first.text  # but in its own words
