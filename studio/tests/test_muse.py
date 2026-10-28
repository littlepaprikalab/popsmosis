"""The Muse Image art and the SAM 3.1 locate step (Muse's brain, by default), with Meta's API stood in for."""

import base64
import json
import time

import numpy as np
import pytest

from studio import brain, jobs, ledger, media, muse, svg
from studio.schemas import Box, Locations, Timeline, locations_problems, timeline_problems
from studio.steps import art, locate
from studio.tests import sample_media

# Meta's own example of SAM 3.1's answer (meta-sam-parser's README): two matches on a 320 x 334 image.
SAM_SAMPLE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>\n"
)
# Where the stand-in SAM finds each structure of the sample bundle: (x, y, radius) on the art.
SPOTS = {"Thylakoid": (500, 500, 150), "Cytochrome b6f": (1300, 500, 100), "ATP synthase": (896, 512, 200)}


@pytest.fixture(autouse=True)
def meta(monkeypatch):
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")  # Muse Image paints, the default for Muse's brain
    monkeypatch.setenv("MODEL_API_KEY", "meta-test-key")
    monkeypatch.setattr(muse, "sleep", lambda s: None)


@pytest.fixture
def job(tmp_path):
    path = sample_media.write_job(tmp_path / "sample")
    (path / media.LOCATIONS).unlink()  # the sample's own, made of ellipses
    return path


def picture(width=1456, height=832, color=(40, 140, 90)) -> bytes:
    """A PNG standing in for Muse Image's picture: a coloured square on white."""
    pixels = np.full((height, width, 3), 255, np.uint8)
    pixels[height // 4: 3 * height // 4, width // 4: 3 * width // 4] = color
    return svg.png(pixels)


def disc(x: int, y: int, r: int) -> np.ndarray:
    ys, xs = np.ogrid[:media.ART_HEIGHT, :media.ART_WIDTH]
    return (xs - x) ** 2 + (ys - y) ** 2 <= r * r


class FakeMuse:
    """Stands in for muse.paint and muse.segment, records what each was asked, and logs each call
    made as they do (muse._log), at Meta's price."""

    def __init__(self, pictures=None, spots=None, sure=0.9):
        self.pictures = pictures or {}  # scene -> a list of picture bytes (or exceptions), one per try
        self.spots = SPOTS if spots is None else spots
        self.sure = sure  # SAM's confidence for a phrase, by structure name or for all
        self.painted: list[tuple[str, str]] = []
        self.segmented: list[tuple[str, str | None, Box | None]] = []

    def paint(self, job, label, prompt):
        scene = label.split()[1]
        self.painted.append((label, prompt))
        tries = sum(1 for lab, _ in self.painted if lab.split()[1] == scene)
        answers = self.pictures.get(scene, [picture()])
        answer = answers[min(tries, len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        muse._log(job, label, muse.IMAGE_MODEL, time.monotonic(), muse.IMAGE_PRICE_USD)
        return answer

    def segment(self, job, label, art_path, *, phrase=None, box=None):
        assert art_path.exists()
        name = next(n for n in SPOTS if label.removesuffix(" box").endswith(" " + n))
        self.segmented.append((label, phrase, box))
        muse._log(job, label, muse.SAM_MODEL, time.monotonic(), muse.SAM_PRICE_USD)
        if name not in self.spots:
            return []
        x, y, r = self.spots[name]
        if box is not None:
            return [muse.Match(disc(x, y, r), None)]
        sure = self.sure.get(name, 0.9) if isinstance(self.sure, dict) else self.sure
        return [muse.Match(disc(x, y, r), sure)]

    def install(self, monkeypatch):
        monkeypatch.setattr(muse, "paint", self.paint)
        monkeypatch.setattr(muse, "segment", self.segment)
        return self


class FakeHelper:
    """Stands in for brain.ask's helper: checks each picture, and gives boxes for locate."""

    def __init__(self, checks=None, boxes=None):
        self.checks = checks or {}  # scene -> a list of ArtCheck dicts, one per try
        self.boxes = boxes or {}  # structure -> a Spot dict
        self.asked: list[tuple[str, str]] = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        assert role == "helper" and len(images) == 1 and images[0].exists()
        self.asked.append((label, prompt))
        if schema is art.ArtCheck:
            assert system == art.CHECK_PROMPT
            scene = label.split()[2]
            tries = sum(1 for lab, _ in self.asked if lab.startswith(f"art check {scene}"))
            answers = self.checks.get(scene, [{"text": [], "wrong": []}])
            return answers[min(tries, len(answers)) - 1]
        assert schema is locate.Spot and system == locate.BOX_PROMPT
        name = next(n for n in SPOTS if label.endswith(" " + n))
        return self.boxes.get(name, {"seen": False, "left": 0, "top": 0, "right": 1, "bottom": 1})

    def labels(self):
        return [label for label, _ in self.asked]


# --- the art --------------------------------------------------------------------------------


def test_muse_image_paints_each_scene_and_the_helper_checks_it(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper()
    assert art.run(job, helper) == "painted 3 scenes with Muse Image in 3 tries"
    assert [label for label, _ in fake.painted] == ["art hook", "art overview", "art synthase"]
    assert helper.labels() == ["art check hook", "art check overview", "art check synthase"]

    prompt = dict(fake.painted)["art overview"]
    bundle = sample_media.bundle()
    assert bundle.scenes[1].image_prompt in prompt and art.STYLE in prompt and bundle.topic in prompt
    assert "- Cytochrome b6f: the cytochrome b6f, coloured #E07A2F" in prompt
    assert "away from the edges" in prompt

    for scene in bundle.scenes:
        pixels = sample_media_image(job / media.art_path(scene.id))
        assert pixels.shape == (media.ART_HEIGHT, media.ART_WIDTH, 3)
        made = json.loads((job / "art" / f"{scene.id}.json").read_text())
        assert made["key"] == art.key(bundle, scene) and made["model"] == muse.IMAGE_MODEL and made["try"] == 1
    assert not (job / media.LOCATIONS).exists() and not list((job / "art").glob("*.try*.png"))
    assert art.is_done(job) and not locate.is_done(job)


def sample_media_image(path):
    import skia

    return skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, :3]


def test_a_picture_of_another_shape_is_fitted_to_the_art_on_white():
    pixels = art.fit(picture(width=1000, height=1000, color=(200, 0, 0)))
    assert pixels.shape == (media.ART_HEIGHT, media.ART_WIDTH, 3)
    assert (pixels[512, 10] == 255).all()  # the white either side of a square picture
    assert pixels[512, 896, 0] > 150 and pixels[512, 896, 1] < 60  # its red middle, in the middle
    with pytest.raises(media.StepFailed, match="not an image"):
        art.fit(b"not a picture")


def test_a_picture_with_writing_or_mistakes_is_asked_for_again_with_what_was_wrong(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"hook": [{"text": ["ATP", "H+"], "wrong": []},
                                  {"text": [], "wrong": ["no thylakoid stack"]},
                                  {"text": [], "wrong": []}]})
    result = art.run(job, helper)
    assert result == "painted 3 scenes with Muse Image in 5 tries"
    hook = [prompt for label, prompt in fake.painted if label.split()[1] == "hook"]
    assert len(hook) == 3
    assert "refused" not in hook[0]
    assert "it has writing in it ('ATP', 'H+'); it must have none" in hook[1]
    assert "no thylakoid stack" in hook[2] and "writing" not in hook[2]
    assert json.loads((job / "art" / "hook.json").read_text())["try"] == 3
    assert sorted(p.name for p in (job / "art").glob("hook.try*.png")) == ["hook.try1.png", "hook.try2.png"]


def test_after_the_last_try_the_scene_keeps_its_best_picture_without_writing(job, monkeypatch):
    FakeMuse().install(monkeypatch)
    helper = FakeHelper({"synthase": [{"text": [], "wrong": ["stalk missing", "wrong membrane"]},
                                      {"text": [], "wrong": ["stalk missing"]},
                                      {"text": ["ATP"], "wrong": []}]})
    result = art.run(job, helper)
    assert result.endswith("; the helper still saw: synthase: stalk missing")
    assert json.loads((job / "art" / "synthase.json").read_text())["try"] == 2


def test_a_scene_with_writing_in_every_picture_fails_the_step_and_keeps_the_others(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"text": ["e-"], "wrong": []}]})
    with pytest.raises(media.StepFailed, match=r"scene overview: no usable picture in 3 tries: it has writing"):
        art.run(job, helper)
    assert (job / "art" / "hook.json").exists() and not (job / "art" / "overview.json").exists()

    fake.painted.clear()
    assert art.run(job, FakeHelper()) == "painted 2 scenes with Muse Image in 2 tries, kept 1 painted before"
    assert [label for label, _ in fake.painted] == ["art overview", "art synthase"]
    fake.painted.clear()
    art.run(job, FakeHelper())  # done: running it again paints every scene
    assert len(fake.painted) == 3


def test_a_declined_prompt_is_a_refused_try(job, monkeypatch):
    fake = FakeMuse({"hook": [muse.Declined("Muse Image declined the picture: unsafe"), picture()]}).install(monkeypatch)
    assert art.run(job, FakeHelper()) == "painted 3 scenes with Muse Image in 4 tries"
    second = [prompt for label, prompt in fake.painted if label == "art hook try 2"][0]
    assert "Muse Image declined the picture: unsafe; describe the scene plainly" in second

    FakeMuse({"hook": [muse.Declined("no")]}).install(monkeypatch)
    with pytest.raises(media.StepFailed, match="scene hook: no usable picture in 3 tries: no; describe"):
        art.run(job, FakeHelper())


def test_switching_the_art_makes_it_again(job, monkeypatch):
    from studio.tests.test_art import FakeArtist

    monkeypatch.setenv("BRAIN_PROVIDER", "claude")  # the SVG art is Claude's brain's to draw
    jobs.keep_studio_settings(job, {"art": {"claude": "muse"}})
    FakeMuse().install(monkeypatch)
    art.run(job, FakeHelper())
    assert art.is_done(job)
    jobs.keep_studio_settings(job, {"art": {"claude": "svg"}})
    assert not art.is_done(job)  # Muse Image's pictures are not the brain's SVG
    art.run(job, FakeArtist())
    assert art.is_done(job) and locate.is_done(job)
    assert not (job / "art" / "hook.json").exists()
    jobs.keep_studio_settings(job, {"art": {"claude": "muse"}})
    assert not art.is_done(job) and not locate.is_done(job)  # nor the SVG Muse Image's
    art.run(job, FakeHelper())
    assert art.is_done(job) and not (job / "art" / "hook.svg").exists()


def test_art_painted_for_an_earlier_bundle_is_not_done(job, monkeypatch):
    FakeMuse().install(monkeypatch)
    art.run(job, FakeHelper())
    data = sample_media.bundle().model_dump()
    data["scenes"][2]["structures"][0]["color_hex"] = "#AA0000"
    jobs.write(job / "bundle.json", data)
    assert not art.is_done(job)


# --- locate ---------------------------------------------------------------------------------


def painted(job, monkeypatch, **muse_args) -> FakeMuse:
    fake = FakeMuse(**muse_args).install(monkeypatch)
    art.run(job, FakeHelper())
    return fake


def test_sam_finds_every_structure_and_locate_writes_the_masks_and_locations(job, monkeypatch):
    fake = painted(job, monkeypatch)
    assert locate.run(job, FakeHelper()) == "located 4 of 4 structures with SAM 3.1"
    assert [(label, phrase) for label, phrase, _ in fake.segmented] == [
        ("locate hook Thylakoid", "the thylakoid"), ("locate overview Thylakoid", "the thylakoid"),
        ("locate overview Cytochrome b6f", "the cytochrome b6f"), ("locate synthase ATP synthase", "the atp synthase")]

    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert locations_problems(locations, sample_media.bundle()) == []
    where = locations.scene("overview").get("Cytochrome b6f")
    assert where.found_by == "sam" and where.box.x0 == 1200 and where.box.x1 == 1401
    assert abs(where.centroid.x - 1300.5) < 1 and abs(where.centroid.y - 500.5) < 1
    mask = sample_media_image(job / where.mask)
    assert mask.shape[:2] == (media.ART_HEIGHT, media.ART_WIDTH) and mask[500, 1300, 0] == 255
    assert locate.is_done(job) and art.is_done(job)
    for video in media.VIDEOS:  # what the timeline and renderer read fits
        assert timeline_problems(jobs.read(job / media.timeline_path(video), Timeline), locations) == []


def test_an_unsure_match_asks_the_helper_for_a_box_and_sam_for_what_is_in_it(job, monkeypatch):
    fake = painted(job, monkeypatch, sure={"ATP synthase": 0.2})
    helper = FakeHelper(boxes={"ATP synthase": {"seen": True, "left": 0.35, "top": 0.2, "right": 0.65, "bottom": 0.8}})
    assert locate.run(job, helper) == "located 4 of 4 structures with SAM 3.1, 1 in the helper's box"
    assert helper.labels() == ["locate box synthase ATP synthase"]
    label, phrase, box = fake.segmented[-1]
    assert label == "locate synthase ATP synthase box" and phrase is None
    assert box == Box(x0=627, y0=204, x1=1165, y1=820)
    located = jobs.read(job / media.LOCATIONS, Locations).scene("synthase").get("ATP synthase")
    assert located.found_by == "sam-box"


def test_a_structure_nobody_finds_is_left_out_unless_too_few_are_left(job, monkeypatch):
    painted(job, monkeypatch, spots={k: v for k, v in SPOTS.items() if k != "Cytochrome b6f"})
    helper = FakeHelper(boxes={"Cytochrome b6f": {"seen": True, "left": 0.7, "top": 0.4, "right": 0.8, "bottom": 0.6}})
    result = locate.run(job, helper)
    assert result == "located 3 of 4 structures with SAM 3.1; left out Cytochrome b6f on overview"
    assert jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f") is None

    painted(job, monkeypatch, spots={k: v for k, v in SPOTS.items() if k != "ATP synthase"})
    with pytest.raises(media.StepFailed, match="scene synthase: SAM 3.1 found 0 of 1 structures; not ATP synthase"):
        locate.run(job, FakeHelper())
    assert not (job / media.LOCATIONS).exists()


def test_the_helper_box_is_dropped_when_it_is_not_seen_a_sliver_or_the_whole_picture():
    scene = sample_media.bundle().scenes[2]
    structure = scene.structures[0]

    def box(**spot):
        return locate.helper_box(scene, structure, media_path(), lambda *a, **k: {"seen": True, **spot})

    assert box(left=0.1, top=0.1, right=0.2, bottom=0.3) == Box(x0=179, y0=102, x1=359, y1=308)
    assert box(left=0.2, top=0.3, right=0.1, bottom=0.1) == Box(x0=179, y0=102, x1=359, y1=308)  # flipped
    assert box(left=-1, top=0.5, right=0.5, bottom=2) == Box(x0=0, y0=512, x1=896, y1=1024)  # clipped
    assert box(left=0.5, top=0.5, right=0.5005, bottom=0.6) is None
    assert box(left=0, top=0, right=1, bottom=1) is None
    assert locate.helper_box(scene, structure, media_path(),
                             lambda *a, **k: {"seen": False, "left": 0.1, "top": 0.1, "right": 0.5, "bottom": 0.5}) is None


def media_path():
    from pathlib import Path

    return Path(__file__)  # any file: the stand-in helper does not look


def test_locate_without_muse_art_says_what_to_do(job, monkeypatch):
    with pytest.raises(media.StepFailed, match="no Muse Image art for this bundle; run the art step first"):
        locate.run(job, FakeHelper())
    painted(job, monkeypatch)
    locate.run(job, FakeHelper())
    monkeypatch.setenv("BRAIN_PROVIDER", "claude")
    jobs.keep_studio_settings(job, {"art": {"claude": "svg"}})  # Muse Image's art is not the SVG art Claude would draw
    with pytest.raises(media.StepFailed, match="no SVG art and locations.json for this bundle; run the art step"):
        locate.run(job, FakeHelper())


# --- the API calls --------------------------------------------------------------------------


class FakeSend:
    """Stands in for muse.send: answers each request with the next of `answers` (status, body)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, method, url, headers, body):
        self.requests.append({"method": method, "url": url, "headers": headers, "body": json.loads(body)})
        status, answer = self.answers.pop(0)
        return status, {}, answer if isinstance(answer, bytes) else json.dumps(answer).encode()


def calls(job):
    return [(row["label"], row["model"], row["cost_usd"]) for row in ledger.read(job)]


def test_paint_asks_muse_image_for_a_png_without_web_search_and_logs_its_price(tmp_path, monkeypatch):
    png = picture()
    send = FakeSend((503, {"error": {"message": "busy"}}),
                    (200, {"data": [{"b64_json": base64.b64encode(png).decode()}], "usage": {"input_tokens": 9}}))
    monkeypatch.setattr(muse, "send", send)
    assert muse.paint(tmp_path, "art hook", "a chloroplast") == png
    request = send.requests[-1]
    assert request["url"] == "https://api.meta.ai/v1/images/generations" and len(send.requests) == 2
    assert request["headers"]["Authorization"] == "Bearer meta-test-key"
    assert request["body"] == {"model": "muse-image-1.0", "prompt": "a chloroplast", "n": 1, "size": "1792x1024",
                               "response_format": "b64_json", "output_format": "png",
                               "tool_enablement": {"enable_web_search": False, "enable_shell": False}}
    assert calls(tmp_path) == [("art hook", "muse-image-1.0", 0.01)]


def test_paint_says_why_a_picture_is_not_made(tmp_path, monkeypatch):
    monkeypatch.setattr(muse, "send", FakeSend(
        (400, {"error": {"code": "content_policy_violation", "param": "prompt", "message": "blocked"}})))
    with pytest.raises(muse.Declined, match="declined the picture: blocked"):
        muse.paint(tmp_path, "art hook", "x")
    monkeypatch.setattr(muse, "send", FakeSend((500, {"error": {"message": "boom"}})))  # not retried
    with pytest.raises(muse.MuseError, match="refused the picture: 500 boom"):
        muse.paint(tmp_path, "art hook", "x")
    monkeypatch.setattr(muse, "send", FakeSend((200, {"data": []})))
    with pytest.raises(muse.MuseError, match="no picture in it"):
        muse.paint(tmp_path, "art hook", "x")
    assert calls(tmp_path) == []  # nothing made, nothing billed
    monkeypatch.delenv("MODEL_API_KEY")
    with pytest.raises(brain.BrainError, match="needs MODEL_API_KEY"):
        muse.paint(tmp_path, "art hook", "x")


def test_segment_sends_the_phrase_and_the_art_and_puts_each_mask_back_on_it(tmp_path, monkeypatch):
    art_file = tmp_path / "hook.png"
    art_file.write_bytes(picture(media.ART_WIDTH, media.ART_HEIGHT))
    answer = {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": SAM_SAMPLE}]}]}
    send = FakeSend((200, answer))
    monkeypatch.setattr(muse, "send", send)
    found = muse.segment(tmp_path, "locate hook Thylakoid", art_file, phrase="green disc")
    request = send.requests[0]
    assert request["url"] == "https://api.meta.ai/v1/responses"
    content = request["body"]["input"][0]["content"]
    assert request["body"]["model"] == "sam-3.1" and request["body"]["metadata"] == {"include_confidence": "true"}
    assert content[0] == {"type": "input_text", "text": "green disc"}
    assert content[1]["image_url"] == "data:image/png;base64," + base64.b64encode(art_file.read_bytes()).decode()

    assert len(found) == 2
    ys, xs = np.nonzero(found[0].mask)
    assert found[0].mask.shape == (media.ART_HEIGHT, media.ART_WIDTH)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (211, 270, 228, 254)
    assert calls(tmp_path) == [("locate hook Thylakoid", "sam-3.1", 0.0025)]


def test_segment_with_a_box_sends_sams_box_prompt(tmp_path, monkeypatch):
    art_file = tmp_path / "hook.png"
    art_file.write_bytes(picture(media.ART_WIDTH, media.ART_HEIGHT))
    send = FakeSend((200, {"status": "completed", "output": []}))
    monkeypatch.setattr(muse, "send", send)
    assert muse.segment(tmp_path, "locate hook Thylakoid box", art_file, box=Box(x0=10, y0=20, x1=110.5, y1=220)) == []
    text = send.requests[0]["body"]["input"][0]["content"][0]["text"]
    assert text == "<0f>0<|box;x1=10;y1=20;x2=110;y2=219;w=1792;h=1024|>"
    with pytest.raises(ValueError):
        muse.segment(tmp_path, "x", art_file)

    monkeypatch.setattr(muse, "send", FakeSend((200, {"status": "failed", "error": {"code": "x"}})))
    with pytest.raises(muse.MuseError, match="did not finish: failed"):
        muse.segment(tmp_path, "x", art_file, phrase="y")


def test_a_coarse_mask_is_stretched_over_its_box_and_clipped_to_the_art():
    from types import SimpleNamespace

    raster = np.array([[1, 0], [0, 1]], bool)
    whole = muse._placed(raster, SimpleNamespace(left=10, top=10, right=14, bottom=14), 20, 20)
    assert whole[10:14, 10:14].astype(int).tolist() == [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 1, 1], [0, 0, 1, 1]]
    assert whole.sum() == 8
    edge = muse._placed(raster, SimpleNamespace(left=18, top=18, right=22, bottom=22), 20, 20)
    assert edge.shape == (20, 20) and edge[18:20, 18:20].all() and edge.sum() == 4  # only its top-left is on the art
    assert muse.matches("", 20, 20) == []


def test_the_helpers_prompts_read_cleanly_and_its_answers_fit_both_providers():
    for schema in (art.ArtCheck, locate.Spot):
        brain.strict_schema(schema)  # raises on a keyword one provider would refuse
    for prompt in (art.CHECK_PROMPT, locate.BOX_PROMPT, art.STYLE):
        assert "\\" not in prompt and "  " not in prompt
