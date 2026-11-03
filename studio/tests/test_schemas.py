import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from studio.brain import SCHEMA_KEYWORDS, strict_schema
from studio.schemas import Bundle, FactAudit, FactCheck, LessonBundle, bundle_of, tag_of
from studio.tests.sample import bundle_data, lesson_bundle_data


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
        "scenes", "lesson_markdown", "music", "lyrics", "song_scenes", "narration", "direction", "builds_up", "spins",
        "blocks", "conversions", "meanings", "key_lines", "tags",
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
    assert tags.lesson == ["photosynthesis", "lightreactions", "apbio", "photosystem"]


def test_a_tag_reads_the_same_cleaned_twice():
    # A spacing accent leaves a combining mark beside the letter, which composes only at the end.
    for raw in ["A¨", "#A´b", "J̌ǰ", "ΐΪ́", "ﬁzz", "#Straße", "İstanbul", "ΟΔΟΣ_Σ"]:
        assert tag_of(tag_of(raw)) == tag_of(raw)
    assert tag_of("A¨") == "\u00e4"


def test_a_tag_that_helps_no_one_find_the_video_is_dropped():
    data = bundle_data()
    data["tags"]["song"] = ["#fyp", "#ForYou", "#For You Page", "#viral", "#osmosis"]
    assert Bundle.model_validate(data).tags.song == ["osmosis"]


def test_a_tag_that_names_only_the_videos_format_is_dropped():
    # On every droplet, so a search for it finds none in particular (the owner, Nov 3).
    data = bundle_data()
    data["tags"]["song"] = ["#vision", "#StudySong", "#studytok", "#kpop", "#EduTok"]
    data["tags"]["lesson"] = ["#vision", "#Explained", "#exam prep", "#retina", "#learnontiktok", "#studywithme"]
    tags = Bundle.model_validate(data).tags
    assert tags.song == ["vision", "kpop"] and tags.lesson == ["vision", "retina"]


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


def song_scene(scene_id: str, **changes) -> dict:
    return {"id": scene_id, "image_prompt": "Protons through ATP synthase.", **changes,
            "structures": [{"name": "ATP synthase", "aliases": [], "sam_phrase": "the atp synthase",
                            "color_hex": "#3366CC"}]}


def test_the_songs_own_pictures_join_the_scenes_marked_as_the_songs_and_are_never_written_out():
    data = bundle_data()
    data["song_scenes"] = [song_scene("chorus", stage_of="light reactions")]
    bundle = Bundle.model_validate(data)
    assert [(s.id, s.song_only) for s in bundle.scenes] == [
        ("hook", False), ("overview", False), ("synthase", False), ("chorus", True)]
    assert bundle.scenes[-1].stage_of == ""  # never a stage: the video would fade the lesson's stages into it
    assert bundle.song_scenes == [] and "song_scenes" not in bundle.model_dump()
    assert Bundle.model_validate_json(bundle.model_dump_json()) == bundle  # bundle.json reads back
    assert "song_only" not in strict_schema(Bundle)["properties"]["scenes"]["items"]["properties"]  # code's to say
    assert not Bundle.model_validate(bundle_data()).scenes[0].song_only  # a bundle from before


def test_the_songs_pictures_count_apart_from_the_lessons_7():
    data = bundle_data()
    data["scenes"] += [{**song_scene(f"detail-{i}"), "image_prompt": "A cell."} for i in range(4)]  # 7 for the lesson
    data["song_scenes"] = [song_scene(f"song-{i}") for i in range(4)]  # one over the song's 3: the checks keep 3
    assert len(Bundle.model_validate(data).scenes) == 11
    data["scenes"].append(song_scene("detail-4"))
    with pytest.raises(ValidationError, match="8 scenes besides the song's own, not 3-7"):
        Bundle.model_validate(data)


def test_a_part_the_songs_picture_draws_again_keeps_the_colour_it_was_first_drawn_in():
    data = bundle_data()
    data["song_scenes"] = [song_scene("chorus")]
    data["song_scenes"][0]["structures"][0].update(color_hex="#3366CD", sam_phrase="a bluish mushroom")
    given = json.dumps(data, sort_keys=True)
    bundle = Bundle.model_validate(data)  # not refused for "ATP synthase has two colours"
    part = bundle.scenes[-1].structures[0]
    assert (part.color_hex, part.sam_phrase) == ("#3366CC", "the atp synthase")
    assert json.dumps(data, sort_keys=True) == given  # the answer as the brain gave it is left as it was


def test_a_process_in_stages_may_take_up_to_8_scenes():
    from studio.schemas import stage_groups

    data = bundle_data()
    stages = [{"id": f"stage-{i}", "image_prompt": "A cell.", "stage_of": "mitosis",
               "structures": [{"name": "Thylakoid", "aliases": [], "sam_phrase": "the thylakoid",
                               "color_hex": "#2E8B57"}]} for i in range(5)]
    data["scenes"] += stages
    bundle = Bundle.model_validate(data)  # 8 scenes
    assert [s.id for s in stage_groups(bundle.scenes)["mitosis"]] == ["stage-0", "stage-1", "stage-2", "stage-3", "stage-4"]
    assert bundle.scenes[0].stage_of == ""
    for stage in stages:
        stage["stage_of"] = ""
    with pytest.raises(ValidationError, match="8 scenes, not 3-7"):
        Bundle.model_validate(data)


def test_the_lesson_video_alone_is_asked_for_the_bundle_without_its_song():
    """STUDIO_VIDEOS=lesson: the brain's schema is Bundle's, in its order, less the song."""
    lesson, full = strict_schema(LessonBundle), strict_schema(Bundle)
    assert list(lesson["properties"]) == [name for name in full["properties"]
                                          if name not in ("music", "lyrics", "song_scenes")]
    assert list(lesson["properties"]["key_lines"]["items"]["properties"]) == ["text", "from_narration"]
    assert list(lesson["properties"]["tags"]["properties"]) == ["lesson"]
    for name in ("facts", "scenes", "narration", "direction"):
        assert lesson["properties"][name] == full["properties"][name]


def test_a_bundle_with_no_song_reads_back_as_one_and_a_song_needs_its_lyrics():
    keys = [{"text": "Light splits water → O₂", "from_narration": "n1"}]
    bundle = bundle_of(LessonBundle.model_validate(lesson_bundle_data(keys)).model_dump())
    assert not bundle.sung and bundle.lyrics == [] and bundle.music.positive_styles == [] and bundle.tags.song == []
    assert [(k.from_line, k.from_narration) for k in bundle.key_lines] == [("", "n1")]
    assert Bundle.model_validate_json(bundle.model_dump_json()) == bundle  # bundle.json reads back
    assert Bundle.model_validate(bundle_data()).sung
    # A song planned with no lines to sing is no bundle at all, as before.
    styled = {**bundle.model_dump(), "music": {"positive_styles": ["upbeat pop"], "negative_styles": [], "sections": []}}
    with pytest.raises(ValidationError, match="lyrics and narration must not be empty"):
        Bundle.model_validate(styled)
