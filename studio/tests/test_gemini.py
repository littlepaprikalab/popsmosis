"""Gemini 3.1 Flash Image as the painter (studio/gemini.py), and the art and locate steps with it painting, with
the Gemini API, SAM 3.1 and the helper stood in for. The answer's shape is the one the live API gave back."""

import base64
import json

import pytest

from studio import brain, gemini, jobs, ledger, media
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

    def paint(self, job, label, prompt):
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
    assert [label for label, _ in painter.painted] == ["art hook", "art overview", "art synthase"]
    assert sam.painted == []  # Muse Image was not asked
    assert art.STYLE in dict(painter.painted)["art overview"]
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
