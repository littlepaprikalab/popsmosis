import pytest
from pydantic import BaseModel, Field, ValidationError

from studio.brain import SCHEMA_KEYWORDS, strict_schema
from studio.schemas import Bundle, FactAudit, FactCheck, tag_of
from studio.tests.sample import bundle_data


def walk(node):
    yield node
    for prop in node.get("properties", {}).values():
        yield from walk(prop)
    if "items" in node:
        yield from walk(node["items"])


@pytest.mark.parametrize("model", [Bundle, FactAudit, FactCheck])
def test_schema_uses_only_what_both_providers_accept(model):
    schema = strict_schema(model)
    for node in walk(schema):
        assert set(node) <= SCHEMA_KEYWORDS, node
        assert node["type"] in {"object", "array", "string", "number", "boolean"}
        if node["type"] == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])


def test_bundle_fields_are_in_the_order_the_model_writes_them():
    assert list(strict_schema(Bundle)["properties"]) == [
        "course", "topic", "audience", "core_idea", "why_chain", "key_terms", "facts", "misconception",
        "scenes", "lesson_markdown", "music", "lyrics", "narration", "direction", "builds_up", "spins", "blocks", "conversions", "key_lines", "tags",
    ]
    facts = strict_schema(Bundle)["properties"]["facts"]["items"]
    assert list(facts["properties"]) == ["id", "text", "source_quote"]


def test_constraints_that_would_reach_the_schema_are_refused():
    class Lengthy(BaseModel):
        name: str = Field(min_length=1)

    class Optional_(BaseModel):
        name: str | None

    with pytest.raises(ValueError, match="minLength"):
        strict_schema(Lengthy)
    with pytest.raises(ValueError, match="anyOf"):
        strict_schema(Optional_)


def test_sample_bundle_is_valid_and_colours_are_normalised():
    bundle = Bundle.model_validate(bundle_data())
    assert bundle.scenes[0].structures[0].color_hex == "#2E8B57"


def test_tags_are_written_as_hashtags_are_searched():
    data = bundle_data()
    data["tags"]["song"] = ["#AP Bio", "apbio", "#Light-Reactions", "#", "#" + "x" * 41, "#Révolution_Française",
                            *[f"#t{n}" for n in range(12)]]
    tags = Bundle.model_validate(data).tags
    assert tags.song == ["apbio", "lightreactions", "révolution_française", "t0", "t1", "t2", "t3", "t4", "t5", "t6"]
    assert tags.lesson == ["photosynthesis", "lightreactions", "apbio", "explained"]


def test_a_tag_reads_the_same_cleaned_twice():
    # A spacing accent leaves a combining mark beside the letter, which composes only at the end.
    for raw in ["A¨", "#A´b", "J̌ǰ", "ΐΪ́", "ﬁzz", "#Straße", "İstanbul", "ΟΔΟΣ_Σ"]:
        assert tag_of(tag_of(raw)) == tag_of(raw)
    assert tag_of("A¨") == "\u00e4"


def test_a_bundle_written_before_tags_has_none():
    data = bundle_data()
    del data["tags"]
    tags = Bundle.model_validate(data).tags
    assert tags.song == [] and tags.lesson == []


def test_a_bundle_from_before_the_audience_reads_as_high_school():
    data = bundle_data()
    del data["audience"]
    assert Bundle.model_validate(data).audience == "high school"
    assert Bundle.model_validate({**bundle_data(), "audience": "about age 10"}).audience == "about age 10"


def broken(change):
    data = bundle_data()
    change(data)
    return data


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["facts"].extend([{**d["facts"][0], "id": f"f{n}"} for n in (4, 5)]), "5 facts"),
        (lambda d: d["scenes"].pop(), "2 scenes"),
        (lambda d: d["lyrics"][0].update(fact_id="f9"), "unknown fact 'f9'"),
        (lambda d: d["scenes"][1]["structures"][0].update(color_hex="#000000"), "Thylakoid has two colours"),
        (lambda d: d["scenes"][0]["structures"][0].update(color_hex="green"), "not #RRGGBB"),
        (lambda d: d["direction"].pop(), "no direction for n2"),
        (lambda d: d["direction"][0].update(scene="nowhere"), "unknown scene 'nowhere'"),
        (lambda d: d["narration"][0].update(id="v1-1"), "duplicate line ids: v1-1"),
        (lambda d: d.update(lesson_markdown="x" * 5001), "over 5000"),
        (lambda d: d["music"].update(extra="nope"), "Extra inputs are not permitted"),
    ],
)
def test_bundle_problems_are_caught(change, message):
    with pytest.raises(ValidationError, match=message):
        Bundle.model_validate(broken(change))


def test_the_song_sections_follow_the_lyrics():
    # A chorus sung once more than music.sections lists (as Claude wrote one on Oct 28) is the song as sung.
    data = bundle_data()
    data["lyrics"][5]["section"] = "verse"
    bundle = Bundle.model_validate(data)
    assert bundle.music.sections == [line.section for i, line in enumerate(bundle.lyrics)
                                     if i == 0 or line.section != bundle.lyrics[i - 1].section]


def test_a_bundle_written_before_particles_carried_anything_loads_unmarked():
    data = bundle_data()
    for d in data["direction"]:
        del d["carries"]
    assert {d.carries for d in Bundle.model_validate(data).direction} == {""}


def test_a_bundle_written_before_glows_loads_with_none():
    data = bundle_data()
    for d in data["direction"]:
        del d["glow"]
    assert {d.glow for d in Bundle.model_validate(data).direction} == {""}


def test_a_bundle_written_before_build_ups_loads_with_none():
    data = bundle_data()
    data.pop("builds_up", None)
    assert Bundle.model_validate(data).builds_up == []


def test_a_bundle_written_with_an_exam_question_still_loads_without_it():
    data = bundle_data()
    data["frq"] = {"prompt": "Explain it.", "predict_question": "Which?", "model_answer_steps": ["One."], "twist": "And?"}
    assert "frq" not in Bundle.model_validate(data).model_dump()


def test_a_process_in_stages_may_take_up_to_8_scenes():
    from studio.schemas import stage_groups

    data = bundle_data()
    stages = [{"id": f"stage-{i}", "image_prompt": "A cell.", "stage_of": "mitosis",
               "structures": [{"name": "Thylakoid", "aliases": [], "sam_phrase": "the thylakoid",
                               "color_hex": "#2E8B57"}]} for i in range(4)]
    data["scenes"] += stages
    bundle = Bundle.model_validate(data)  # 7 scenes
    assert [s.id for s in stage_groups(bundle.scenes)["mitosis"]] == ["stage-0", "stage-1", "stage-2", "stage-3"]
    assert bundle.scenes[0].stage_of == ""
    for stage in stages:
        stage["stage_of"] = ""
    with pytest.raises(ValidationError, match="7 scenes, not 3-6"):
        Bundle.model_validate(data)
