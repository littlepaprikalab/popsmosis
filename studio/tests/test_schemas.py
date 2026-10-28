import pytest
from pydantic import BaseModel, Field, ValidationError

from studio.brain import SCHEMA_KEYWORDS, strict_schema
from studio.schemas import Bundle, FactCheck
from studio.tests.sample import bundle_data


def walk(node):
    yield node
    for prop in node.get("properties", {}).values():
        yield from walk(prop)
    if "items" in node:
        yield from walk(node["items"])


@pytest.mark.parametrize("model", [Bundle, FactCheck])
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
        "course", "topic", "core_idea", "facts", "misconception", "frq",
        "scenes", "lesson_markdown", "music", "lyrics", "narration", "direction", "tags",
    ]
    facts = strict_schema(Bundle)["properties"]["facts"]["items"]
    assert list(facts["properties"]) == ["id", "text", "source_quote"]
    frq = strict_schema(Bundle)["properties"]["frq"]
    assert list(frq["properties"]) == ["prompt", "predict_question", "model_answer_steps", "twist"]


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


def test_a_bundle_written_before_tags_has_none():
    data = bundle_data()
    del data["tags"]
    tags = Bundle.model_validate(data).tags
    assert tags.song == [] and tags.lesson == []


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
        (lambda d: d["lyrics"][5].update(section="verse"), "lyrics run"),
        (lambda d: d["direction"].pop(), "no direction for n2"),
        (lambda d: d["direction"][0].update(scene="nowhere"), "unknown scene 'nowhere'"),
        (lambda d: d["narration"][0].update(id="v1-1"), "duplicate line ids: v1-1"),
        (lambda d: d.update(lesson_markdown="x" * 5001), "over 5000"),
        (lambda d: d["frq"].update(extra="nope"), "Extra inputs are not permitted"),
    ],
)
def test_bundle_problems_are_caught(change, message):
    with pytest.raises(ValidationError, match=message):
        Bundle.model_validate(broken(change))
