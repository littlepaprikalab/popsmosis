"""Gemini 3.1 Flash Image as the painter (studio/gemini.py), and the art and locate steps with it painting, with
the Gemini API, SAM 3.1 and the helper stood in for. The answer's shape is the one the live API gave back."""

import base64
import json
from pathlib import Path

import pytest

from studio import brain, gemini, jobs, knobs, ledger, media, svg
from studio.schemas import Axes, Bundle, Locations, locations_problems
from studio.steps import art, locate
from studio.tests import sample_media
from studio.tests.test_muse import FakeHelper, FakeMuse, FakeSend, picture

# What one live picture's answer reported: 46 tokens in, 1,509 out of which 1,120 are the picture's.
USAGE = {"promptTokenCount": 46, "candidatesTokenCount": 1509, "totalTokenCount": 1555,
         "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 46}],
         "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}], "serviceTier": "standard"}


def answer(*parts, finish="STOP", usage=USAGE, **extra):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish, "index": 0}],
            "usageMetadata": usage, "modelVersion": "gemini-3.1-flash-image", **extra}


def image_part(data: bytes, mime="image/jpeg", **extra):
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}, "thoughtSignature": "sig", **extra}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("MODEL_API_KEY", "meta-test-key")
    monkeypatch.setattr(gemini, "sleep", lambda s: None)


def calls(job):
    return [(row["label"], row["model"], row["input_tokens"], row["output_tokens"], row["cost_usd"])
            for row in ledger.read(job)]


# --- the API call ---------------------------------------------------------------------------


def test_paint_asks_for_one_16_9_picture_from_the_prompt_alone_and_logs_its_tokens_price(tmp_path, monkeypatch):
    png = picture()
    send = FakeSend((503, {"error": {"status": "UNAVAILABLE", "message": "busy"}}), (200, answer(image_part(png))))
    monkeypatch.setattr(gemini, "send", send)
    assert gemini.paint(tmp_path, "art hook", "a chloroplast") == png
    assert len(send.requests) == 2  # the busy server was asked again
    request = send.requests[-1]
    assert request["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                              "gemini-3.1-flash-image:generateContent")
    assert request["headers"]["x-goog-api-key"] == "gemini-test-key"
    assert request["body"] == {
        "contents": [{"role": "user", "parts": [{"text": "a chloroplast"}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9", "imageSize": "1K"}},
    }  # no tools: no Google Search
    # 46 in at $0.50, 1,120 image tokens at $60 and 389 thinking at $3, per million.
    assert calls(tmp_path) == [("art hook", "gemini-3.1-flash-image", 46, 1509, pytest.approx(0.06839))]


def test_the_picture_is_the_last_that_is_not_a_thought(tmp_path, monkeypatch):
    interim, final = picture(color=(200, 0, 0)), picture(color=(0, 0, 200))
    monkeypatch.setattr(gemini, "send", FakeSend((200, answer(
        {"text": "Planning the layout."}, image_part(interim, thought=True), image_part(final, mime="image/png")))))
    assert gemini.paint(tmp_path, "art hook", "x") == final


def test_a_picture_gemini_will_not_make_is_a_refused_try(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "send", FakeSend((200, {"promptFeedback": {"blockReason": "SAFETY"}})))
    with pytest.raises(gemini.Declined, match=r"blocked the prompt \(SAFETY\)"):
        gemini.paint(tmp_path, "art hook", "x")
    monkeypatch.setattr(gemini, "send", FakeSend((200, answer({"text": "I can't draw that."}, finish="IMAGE_SAFETY"))))
    with pytest.raises(gemini.Declined, match=r"made no picture \(IMAGE_SAFETY\)"):
        gemini.paint(tmp_path, "art hook", "x")
    monkeypatch.setattr(gemini, "send", FakeSend((200, answer(finish="MAX_TOKENS"))))
    with pytest.raises(gemini.GeminiError, match="no picture in it") as raised:
        gemini.paint(tmp_path, "art hook", "x")
    assert not isinstance(raised.value, gemini.Declined)
    monkeypatch.setattr(gemini, "send", FakeSend((400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad size"}})))
    with pytest.raises(gemini.GeminiError, match="refused the picture: 400 INVALID_ARGUMENT bad size"):
        gemini.paint(tmp_path, "art hook", "x")
    assert calls(tmp_path) == []  # nothing made, nothing billed
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gemini.GeminiError, match="Gemini 3.1 Flash Image needs GEMINI_API_KEY"):
        gemini.paint(tmp_path, "art hook", "x")


def test_an_answer_with_no_tokens_is_priced_as_one_1k_picture():
    assert gemini.cost(None) == (0, 0, 0.067)
    assert gemini.cost({"promptTokenCount": 10, "candidatesTokenCount": 1120, "thoughtsTokenCount": 100,
                        "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}]}) == \
        (10, 1220, pytest.approx((10 * 0.5 + 1120 * 60 + 100 * 3) / 1e6))


# --- the art and locate steps with Gemini painting ------------------------------------------------


class FakeGemini:
    """Stands in for gemini.paint, records what it was asked, and logs each picture as it does."""

    def __init__(self, pictures=None):
        self.pictures = pictures or {}  # scene -> a list of picture bytes (or exceptions), one per try
        self.painted: list[tuple[str, str]] = []

    def paint(self, job, label, prompt, reference=None):
        scene = label.split()[1]
        self.painted.append((label, prompt))
        tries = sum(1 for lab, _ in self.painted if lab.split()[1] == scene)
        answers = self.pictures.get(scene, [picture()])
        result = answers[min(tries, len(answers)) - 1]
        if isinstance(result, Exception):
            raise result
        gemini._log(job, label, 0.0, USAGE)
        return result

    def install(self, monkeypatch):
        monkeypatch.setattr(gemini, "paint", self.paint)
        return self


@pytest.fixture
def job(tmp_path):
    path = sample_media.write_job(tmp_path / "sample")  # the brain is Claude's: nothing else is in force
    (path / media.LOCATIONS).unlink()  # the sample's own, made of ellipses
    return path


def test_on_claudes_brain_gemini_paints_by_default_and_sam_finds_the_structures(job, monkeypatch):
    painter, sam = FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)
    helper = FakeHelper()
    assert art.painter(job) == "gemini"
    assert art.run(job, helper) == "painted 3 scenes with Gemini 3.1 Flash Image in 3 tries"
    assert sorted(label for label, _ in painter.painted) == ["art hook", "art overview", "art synthase"]  # at once
    assert sam.painted == []  # Muse Image was not asked
    assert dict(painter.painted)["art overview"].endswith(art.LABELS)
    for scene in sample_media.bundle().scenes:
        assert json.loads((job / "art" / f"{scene.id}.json").read_text())["model"] == "gemini-3.1-flash-image"
    assert art.is_done(job) and not locate.is_done(job)

    locate.run(job, helper)
    assert locate.is_done(job) and sam.segmented  # SAM 3.1 on Gemini's pictures
    assert {row["model"] for row in ledger.read(job)} == {"gemini-3.1-flash-image", "sam-3.1"}


def test_a_picture_gemini_declines_is_asked_for_again_more_plainly(job, monkeypatch):
    painter = FakeGemini({"hook": [gemini.Declined("Gemini 3.1 Flash Image made no picture (IMAGE_SAFETY)"),
                                   picture()]}).install(monkeypatch)
    art.run(job, FakeHelper())
    hook = [prompt for label, prompt in painter.painted if label.startswith("art hook")]
    assert len(hook) == 2 and "IMAGE_SAFETY" in hook[1] and "describe the scene plainly" in hook[1]


def test_the_admins_setting_picks_the_painter_and_switching_it_paints_again(job, monkeypatch):
    painter, muse_api = FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)
    jobs.keep_studio_settings(job, {"art": {"claude": "muse", "meta": "muse"}})
    assert art.painter(job) == "muse"
    art.run(job, FakeHelper())
    assert len(muse_api.painted) == 3 and painter.painted == [] and art.is_done(job)

    jobs.keep_studio_settings(job, {"art": {"claude": "gemini", "meta": "muse"}})
    assert not art.is_done(job)  # Muse Image's pictures are not Gemini's
    art.run(job, FakeHelper())
    assert len(painter.painted) == 3 and art.is_done(job)


def test_on_muses_brain_the_admin_can_have_gemini_paint(job, monkeypatch):
    monkeypatch.setattr(brain, "_chosen", "muse-spark")
    jobs.keep_studio_settings(job, {"art": {"meta": "gemini"}})
    assert art.painter(job) == "gemini"
    jobs.keep_studio_settings(job, {"art": {"meta": "svg"}})  # not Muse's to have: its default instead
    assert art.painter(job) == "muse"
    with brain.chosen("claude"):  # a topic sent to Claude goes by Claude's setting
        assert art.painter(job) == "gemini"


class FakeDrawer:
    """Claude drawing a scene to scale as SVG (studio/tests/art/), and the helper for the painted ones."""

    def __init__(self, helper):
        self.helper, self.drawn, self.roles = helper, [], []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if system != art.PROMPT:
            return self.helper(role, prompt, schema, images, system=system, label=label)
        assert role in ("brain", "drawer") and schema is None and not images
        self.drawn.append((label, prompt))
        self.roles.append(role)
        drawing = (Path(__file__).resolve().parent / "art" / f"{label.split()[1]}.svg").read_text(encoding="utf-8")
        return {"text": drawing}


def to_scale(job, scene_id="overview"):
    data = sample_media.bundle().model_dump()
    next(s for s in data["scenes"] if s["id"] == scene_id)["to_scale"] = True
    bundle = Bundle.model_validate(data)
    jobs.write(job / "bundle.json", bundle)
    return bundle


def test_a_scene_to_scale_is_drawn_as_svg_and_the_others_painted(job, monkeypatch):
    # Round 22's compound-interest bar chart came back from Gemini with bars at heights the lesson never gave.
    bundle = to_scale(job)
    painter, sam = FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)
    ask = FakeDrawer(FakeHelper())
    assert art.run(job, ask) == ("painted 2 scenes with Gemini 3.1 Flash Image in 2 tries; "
                                 "drew 1 scene to scale as SVG (overview)")
    assert sorted(label for label, _ in painter.painted) == ["art hook", "art synthase"]
    [(label, prompt)] = ask.drawn
    assert label == "art overview" and 'To scale: this scene\'s sizes must be right' in prompt
    assert all(f"- {fact.text}" in prompt for fact in bundle.facts)  # the numbers its sizes come from
    assert (job / "art" / "overview.svg").exists() and not (job / "art" / "overview.json").exists()
    assert art.is_done(job) and not locate.is_done(job)
    assert art.notices(job, bundle).keys() <= {"hook", "synthase"}  # no painted picture's notes on the drawing

    result = locate.run(job, ask)
    assert result.startswith("located 4 of 4 structures with SAM 3.1 and the SVG drawings (2 on 1 scene)")
    assert locate.is_done(job)
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert locations_problems(locations, bundle) == []
    assert {s.found_by for s in locations.scene("overview").structures} == {"svg"}
    assert locations.scene("overview").look == svg.LOOK
    assert {s.found_by for s in locations.scene("synthase").structures} == {"sam"}
    assert not any("overview" in label for label, _, _ in sam.segmented)  # no SAM on the drawing

    # Nothing changed: the next run draws and paints it all again, as a painted job's does.
    ask.drawn.clear()
    art.run(job, ask)
    assert [label for label, _ in ask.drawn] == ["art overview"]


def test_a_graph_is_drawn_to_scale_on_any_brain_unless_the_knob_leaves_it_to_the_brain(monkeypatch):
    monkeypatch.delenv(art.CLAUDE_DRAWS, raising=False)
    bundle = sample_media.bundle()
    graph = bundle.scenes[1].model_copy(update={"axes": Axes(x="Year", y="Balance")})
    assert graph.exact() and not bundle.scenes[1].exact()
    bundle = bundle.model_copy(update={"scenes": [bundle.scenes[0], graph, bundle.scenes[2]]})
    job = Path("unused")
    assert [art.drawn(job, bundle, s) for s in bundle.scenes] == [False, True, False]
    with brain.chosen("muse-spark"):  # Claude draws it for Muse's brain
        assert [art.drawn(job, bundle, s) for s in bundle.scenes] == [False, True, False]
        knobs.use({"knobs": {art.CLAUDE_DRAWS: "brain"}})  # as before Oct 30: Muse's painter paints the graph too
        assert [art.drawn(job, bundle, s) for s in bundle.scenes] == [False, False, False]
    assert [art.drawn(job, bundle, s) for s in bundle.scenes] == [False, True, False]  # Claude's brain draws its own


def test_a_scene_to_scale_with_no_usable_drawing_is_painted_instead_and_located_by_sam(job, monkeypatch):
    # A chart the brain cannot draw is painted, as before charts were drawn: a droplet with a painted chart, not none.
    bundle = to_scale(job)
    painter, sam = FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)

    class Refused(FakeDrawer):
        def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
            if system == art.PROMPT:
                self.drawn.append((label, prompt))
                return {"text": "<svg>not a drawing"}
            return super().__call__(role, prompt, schema, images, system=system, label=label)

    ask = Refused(FakeHelper())
    result = art.run(job, ask)
    assert len(ask.drawn) == art.MAX_TRIES and result.endswith("; painted overview instead of drawing it as SVG")
    assert sorted(label for label, _ in painter.painted) == ["art hook", "art overview", "art synthase"]
    made = json.loads((job / "art" / "overview.json").read_text())
    assert "no usable drawing" in made["instead_of_svg"]
    overview = bundle.scenes[1]
    assert not art.drawn(job, bundle, overview) and art.is_done(job)
    locate.run(job, ask)
    assert locate.is_done(job)
    assert {s.found_by for s in jobs.read(job / media.LOCATIONS, Locations).scene("overview").structures} == {"sam"}

    # Resumed, it stays painted; from scratch, it is drawn again.
    ask.drawn.clear()
    (job / "art" / "hook.json").unlink()
    art.run(job, ask)
    assert ask.drawn == []
    art.run(job, FakeDrawer(FakeHelper()))
    assert art.drawn(job, bundle, overview) and (job / "art" / "overview.svg").exists()


def test_an_everyday_scene_marked_to_scale_is_still_painted(job):
    bundle = to_scale(job, "hook")
    assert not art.drawn(job, bundle, bundle.scenes[0])


def math_lesson(job, course="6th grade math"):
    bundle = sample_media.bundle().model_copy(update={"course": course})
    jobs.write(job / "bundle.json", bundle)
    return bundle


def test_a_math_lessons_pictures_but_its_hook_are_drawn_by_claude_on_muses_brain(job, monkeypatch):
    # Oct 30: Muse Image painted a fractions lesson's six bars as seven, and thirds it was asked to group in twos
    # ungrouped, and the helper passed both.
    monkeypatch.delenv(art.CLAUDE_DRAWS, raising=False)
    monkeypatch.setattr(brain, "_chosen", "muse-wonder")
    bundle = math_lesson(job)
    painter = FakeMuse().install(monkeypatch)
    ask = FakeDrawer(FakeHelper())
    assert art.run(job, ask) == ("painted 1 scene with Muse Image in 1 try; "
                                 "drew 2 scenes to scale as SVG with Claude (overview, synthase)")
    assert [label for label, _ in painter.painted] == ["art hook"]  # an everyday picture, painted
    assert ask.roles == ["drawer", "drawer"]  # Claude, not the Muse brain
    for _, prompt in ask.drawn:  # to scale, from the facts' numbers
        assert "To scale: this scene's sizes must be right" in prompt
        assert all(f"- {fact.text}" in prompt for fact in bundle.facts)
    assert art.is_done(job)
    locate.run(job, ask)
    found = {s.scene: {loc.found_by for loc in s.structures} for s in jobs.read(job / media.LOCATIONS, Locations).scenes}
    assert found == {"hook": {"sam"}, "overview": {"svg"}, "synthase": {"svg"}}


def test_the_knob_at_charts_or_brain_has_a_math_lessons_pictures_painted(job, monkeypatch):
    bundle = math_lesson(job)
    for brain_name in ("claude", "muse-spark"):
        for knob in ("charts", "brain"):
            knobs.use({"knobs": {art.CLAUDE_DRAWS: knob}})
            with brain.chosen(brain_name):
                assert not any(art.drawn(job, bundle, s) for s in bundle.scenes)
                assert not any(art.exact(bundle, s) for s in bundle.scenes)
    knobs.use({"knobs": {art.CLAUDE_DRAWS: "math"}})
    with brain.chosen("claude"):  # Claude's brain draws them too, rather than Gemini painting them
        assert [art.drawn(job, bundle, s) for s in bundle.scenes] == [False, True, True]
        assert [art.exact(bundle, s) for s in bundle.scenes] == [False, True, True]  # the hook is no figure
    assert not any(art.drawn(job, sample_media.bundle(), s) for s in bundle.scenes)  # biology: painted


def test_the_knob_says_what_claude_draws_and_anything_else_is_its_default(monkeypatch):
    monkeypatch.delenv(art.CLAUDE_DRAWS, raising=False)
    assert art.claude_draws() == "math"
    monkeypatch.setenv(art.CLAUDE_DRAWS, " Charts ")
    assert art.claude_draws() == "charts"
    knobs.use({"knobs": {art.CLAUDE_DRAWS: "brain"}})  # the page's wins over the environment
    assert art.claude_draws() == "brain"
    knobs.use({"knobs": {art.CLAUDE_DRAWS: "everything"}})
    assert art.claude_draws() == "math"


def test_a_math_lesson_is_known_by_its_course_or_topic():
    bundle = sample_media.bundle()
    math = [("6th grade math", "dividing by a fraction"), ("AP Statistics", "what a p-value means"),
            ("Algebra 1", "what slope means"), ("AP Calculus AB", "the derivative"), ("", "adding fractions"),
            ("Personal Finance", "what a percentage rate means")]
    other = [("AP Biology", "the light reactions"), ("Chemistry", "fractional distillation"),
             ("8th grade Earth science", "why there are two high tides a day"), ("Personal Finance", "compound interest")]
    assert all(art.math_lesson(bundle.model_copy(update={"course": c, "topic": t})) for c, t in math)
    assert not any(art.math_lesson(bundle.model_copy(update={"course": c, "topic": t})) for c, t in other)


def test_a_chart_painted_before_charts_were_drawn_is_kept_and_a_painter_switch_repaints_it(job, monkeypatch):
    # A job under way when charts began to be drawn keeps its painted chart, rather than paying to draw it again.
    bundle = to_scale(job)
    painter, sam = FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)
    ask = FakeDrawer(FakeHelper())
    with monkeypatch.context() as m:
        m.setattr(art, "_to_scale", lambda bundle, group: False)  # as the art step painted it before
        art.run(job, ask)
    overview = bundle.scenes[1]
    assert not art.drawn(job, bundle, overview) and art.is_done(job) and ask.drawn == []
    locate.run(job, ask)
    assert locate.is_done(job)

    jobs.keep_studio_settings(job, {"art": {"claude": "muse", "meta": "muse"}})  # the admin switches painter
    assert not art.is_done(job) and not art.drawn(job, bundle, overview)
    art.run(job, ask)
    assert ask.drawn == [] and art.is_done(job)  # painted again, by Muse Image, not drawn
    assert json.loads((job / "art" / "overview.json").read_text())["model"] == "muse-image-1.0"


def test_the_stages_of_a_process_are_all_painted_when_one_cannot_be_drawn(job, monkeypatch):
    data = sample_media.bundle().model_dump()
    for scene in data["scenes"][1:]:
        scene["stage_of"] = "photosynthesis"
    data["scenes"][1]["to_scale"] = True
    bundle = Bundle.model_validate(data)
    jobs.write(job / "bundle.json", bundle)
    FakeGemini().install(monkeypatch), FakeMuse().install(monkeypatch)

    class HalfDrawn(FakeDrawer):  # the overview draws; the ATP synthase stage does not
        def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
            if system == art.PROMPT and "synthase" in label:
                return {"text": "<svg>not a drawing"}
            return super().__call__(role, prompt, schema, images, system=system, label=label)

    result = art.run(job, HalfDrawn(FakeHelper()))
    assert "drew" not in result and result.endswith("; painted synthase, overview instead of drawing them as SVG")
    assert not [s for s in bundle.scenes if (job / "art" / f"{s.id}.svg").exists()] and art.is_done(job)
    assert not any(art.drawn(job, bundle, s) for s in bundle.scenes)
