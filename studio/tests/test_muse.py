"""The Muse Image art and the SAM 3.1 locate step (Muse's brain, by default), with Meta's API stood in for."""

import base64
import hashlib
import json
import re
import threading
import time

import numpy as np
import pytest

from studio import brain, checks, jobs, ledger, media, muse, svg
from studio.schemas import Box, Bundle, Locations, Scene, Structure, Timeline, locations_problems, timeline_problems
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
    monkeypatch.setattr(brain, "_chosen", "muse-wonder")  # Muse Image paints, the default for Muse's brain
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


SLATE = (40, 55, 75)  # the dark slate blue art.STYLE asks Muse Image to paint behind the diagram


def painting(scene_id: str, paper=(255, 255, 255)) -> bytes:
    """Muse Image's picture of a sample scene, stood in for: each structure a disc in its own
    colour, where the stand-in SAM finds it (SPOTS), on white or another plain background."""
    pixels = np.full((media.ART_HEIGHT, media.ART_WIDTH, 3), paper, np.uint8)
    for structure in next(s for s in sample_media.bundle().scenes if s.id == scene_id).structures:
        pixels[disc(*SPOTS[structure.name])] = tuple(bytes.fromhex(structure.color_hex[1:]))
    return svg.png(pixels)


class FakeMuse:
    """Stands in for muse.paint and muse.segment, records what each was asked, and logs each call
    made as they do (muse._log), at Meta's price."""

    def __init__(self, pictures=None, spots=None, sure=0.9, redraw_error=None):
        self.pictures = pictures or {}  # scene -> a list of picture bytes (or exceptions), one per try
        self.spots = SPOTS if spots is None else spots
        self.sure = sure  # SAM's confidence for a phrase, by structure name or for all
        self.redraw_error = redraw_error  # raised for every picture asked for with a reference
        self.painted: list[tuple[str, str]] = []
        self.references: list[tuple[str, object]] = []  # (label, the reference picture or None), per call
        self.segmented: list[tuple[str, str | None, Box | None]] = []

    def paint(self, job, label, prompt, reference=None):
        scene = label.split()[1]
        self.references.append((label, reference))
        if reference is not None:
            assert reference.exists()
            if self.redraw_error:
                raise self.redraw_error
        self.painted.append((label, prompt))
        tries = sum(1 for lab, _ in self.painted if lab.split()[1] == scene)
        answers = self.pictures.get(scene, [painting(scene)])
        answer = answers[min(tries, len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        muse._log(job, label, muse.IMAGE_MODEL, time.monotonic(), muse.IMAGE_PRICE_USD)
        return answer

    def segment(self, job, label, art_path, *, phrase=None, box=None):
        assert art_path.exists()
        asked = re.sub(r" (box|try \d+)$", "", label)
        name = next(n for n in SPOTS if asked.endswith(" " + n))
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
            answers = self.checks.get(scene, [{}])
            return {"text": [], "misplaced": [], "wrong": [], **answers[min(tries, len(answers)) - 1]}
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
    # The scenes are painted at the same time, so in any order.
    assert sorted(label for label, _ in fake.painted) == ["art hook", "art overview", "art synthase"]
    assert sorted(helper.labels()) == ["art check hook", "art check overview", "art check synthase"]

    prompt = dict(fake.painted)["art overview"]
    bundle = sample_media.bundle()
    assert prompt.startswith(bundle.scenes[1].image_prompt.rstrip(".")) and prompt.endswith(art.STYLE)
    assert "Colours: Thylakoid green, Cytochrome b6f orange." in prompt  # names, which the model reads
    assert "#" not in prompt and "away from the edges" not in prompt.replace(art.STYLE, "")

    for scene in bundle.scenes:
        pixels = sample_media_image(job / media.art_path(scene.id))
        assert pixels.shape == (media.ART_HEIGHT, media.ART_WIDTH, 3)
        made = json.loads((job / "art" / f"{scene.id}.json").read_text())
        assert made["key"] == art.key(bundle, scene) and made["model"] == muse.IMAGE_MODEL and made["try"] == 1
    assert not (job / media.LOCATIONS).exists() and not list((job / "art").glob("*.try*.png"))
    assert art.is_done(job) and not locate.is_done(job)


def staged(job):
    """The sample job with its overview and its ATP synthase close-up as two stages of one process."""
    data = sample_media.bundle().model_dump()
    for scene in data["scenes"][1:]:
        scene["stage_of"] = "photosynthesis"
    bundle = Bundle.model_validate(data)
    jobs.write(job / "bundle.json", bundle)
    return bundle


def test_a_later_stage_is_redrawn_from_the_first_stages_picture(job, monkeypatch):
    bundle = staged(job)
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper()
    assert art.run(job, helper) == "painted 3 scenes with Muse Image in 3 tries"
    labels = [label for label, _ in fake.painted]
    assert labels.index("art overview") < labels.index("art synthase")  # the first stage is painted first
    references = dict(fake.references)
    assert references["art hook"] is None and references["art overview"] is None
    assert references["art synthase"] == job / media.art_path("overview")
    redraw = art.REDRAW.format(process="photosynthesis")
    assert dict(fake.painted)["art synthase"].startswith(f"{redraw} ATP synthase.")
    assert redraw not in dict(fake.painted)["art overview"]
    checked = dict(helper.asked)
    assert "The scene is stage 1 of 2 of photosynthesis: the same subject" in checked["art check overview"]
    assert "The scene is stage 2 of 2 of photosynthesis" in checked["art check synthase"]
    assert "The scene is the video's opening" in checked["art check hook"]
    made = {s.id: json.loads((job / "art" / f"{s.id}.json").read_text()) for s in bundle.scenes}
    assert [made[s]["redrawn_from"] for s in ("hook", "overview", "synthase")] == ["", "", "overview"]
    assert art.is_done(job)


def test_a_later_stage_is_painted_again_whenever_its_first_stage_is(job, monkeypatch):
    bundle = staged(job)
    fake = FakeMuse().install(monkeypatch)
    art.run(job, FakeHelper())
    (job / "art" / "overview.json").unlink()  # its picture to be made again
    fake.painted.clear()
    assert art.run(job, FakeHelper()) == "painted 2 scenes with Muse Image in 2 tries, kept 1 painted before"
    assert sorted(label for label, _ in fake.painted) == ["art overview", "art synthase"]
    # The later stage's key follows its first stage's scene; a scene that is no stage keeps the key it
    # had before stages existed, so finished jobs are not painted again.
    hook, _, synthase = bundle.scenes
    moved = bundle.model_copy(deep=True)
    moved.scenes[1].image_prompt = "A thylakoid membrane, seen from above."
    assert art.key(moved, moved.scenes[2]) != art.key(bundle, synthase)
    before = json.dumps([bundle.topic, hook.model_dump(exclude={"stage_of"})], sort_keys=True)
    assert art.key(bundle, hook) == hashlib.sha256(before.encode()).hexdigest()[:16]


def test_a_stage_muse_image_will_not_redraw_is_painted_afresh(job, monkeypatch):
    staged(job)
    fake = FakeMuse(redraw_error=muse.MuseError("Muse Image refused the picture: 400 no images")).install(monkeypatch)
    assert art.run(job, FakeHelper()) == "painted 3 scenes with Muse Image in 3 tries"
    assert [(label, ref is not None) for label, ref in fake.references if label == "art synthase"] == [
        ("art synthase", True), ("art synthase", False)]
    assert not dict(fake.painted)["art synthase"].startswith("Redraw")
    made = json.loads((job / "art" / "synthase.json").read_text())
    assert made["redrawn_from"] == "" and "400 no images" in made["tries"][0]["redraw_failed"]


def test_what_the_check_still_finds_wrong_in_a_picture_is_kept_as_notes_for_the_video(job, monkeypatch):
    FakeMuse().install(monkeypatch)
    note = "The protein's head should face the stroma, not the lumen."
    helper = FakeHelper(checks={"synthase": [{"misplaced": ["head in the lumen"], "notes": [note]}],
                                "overview": [{"wrong": ["a little dark"]}]})  # no note: it misleads no one
    art.run(job, helper)
    assert "`notes`" in art.CHECK_PROMPT and "for the student" in art.CHECK_PROMPT
    made = json.loads((job / "art" / "synthase.json").read_text())
    assert len(made["tries"]) == 2 and made["check"]["notes"] == [note]  # still wrong after both tries
    bundle = checks.load(job / "bundle.json")
    assert art.notices(job, bundle) == {"synthase": [note]}
    staged(job)  # a changed bundle: the pictures are no longer this bundle's, so nothing is disclosed for them
    assert art.notices(job, checks.load(job / "bundle.json")) == {}


def test_a_stage_painted_afresh_and_declined_is_a_declined_try_not_a_failed_step(job, monkeypatch):
    staged(job)
    declined = muse.Declined("Muse Image declined the picture: blocked")
    fake = FakeMuse(pictures={"synthase": [declined, painting("synthase")]},
                    redraw_error=muse.MuseError("Muse Image refused the picture: 400 no images")).install(monkeypatch)
    # The redraw that failed is not a refusal (the same try was painted afresh); the declined picture is.
    assert art.run(job, FakeHelper()) == ("painted 3 scenes with Muse Image in 4 tries; 1 picture refused: synthase: "
                                          "Muse Image declined the picture: blocked")
    made = json.loads((job / "art" / "synthase.json").read_text())
    assert [sorted(t) for t in made["tries"][:2]] == [["redraw_failed", "try"], ["declined", "try"]]
    assert made["try"] == 2 and fake.references[-1] == ("art synthase try 2", None)


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


def test_a_picture_on_a_dark_background_is_fitted_on_that_colour():
    pixels = np.full((1000, 1000, 3), SLATE, np.uint8)
    pixels[300:700, 300:700] = (200, 0, 0)
    fitted = art.fit(svg.png(pixels))
    assert tuple(fitted[512, 10]) == pytest.approx(SLATE, abs=2)  # either side of the square, its own slate
    assert fitted[512, 896, 0] > 150


def test_a_picture_with_writing_or_mistakes_is_asked_for_again_with_what_was_wrong(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"hook": [{"text": ["ATP", "H+"]}, {}],
                         "synthase": [{"misplaced": ["head in the lumen"]}, {}]})
    result = art.run(job, helper)
    # The step's line names each picture refused and why, so the operator's summary can tell what came back wrong.
    assert result == ("painted 3 scenes with Muse Image in 5 tries; 2 pictures refused: hook: writing in it ('ATP', 'H+'); "
                      "synthase: head in the lumen")
    hook = [prompt for label, prompt in fake.painted if label.split()[1] == "hook"]
    synthase = [prompt for label, prompt in fake.painted if label.split()[1] == "synthase"]
    assert len(hook) == len(synthase) == 2
    assert "refused" not in hook[0]
    assert "it has writing in it ('ATP', 'H+'); it must have none" in hook[1]
    assert "head in the lumen" in synthase[1] and "writing" not in synthase[1]
    made = json.loads((job / "art" / "hook.json").read_text())
    assert made["try"] == 2
    # What each try was refused for is kept, not only the next prompt's quote of it.
    assert made["tries"] == [{"try": 1, "text": ["ATP", "H+"], "misplaced": [], "unnamed": [], "wrong": [], "notes": []},
                             {"try": 2, "text": [], "misplaced": [], "unnamed": [], "wrong": [], "notes": []}]
    assert sorted(p.name for p in (job / "art").glob("hook.try*.png")) == ["hook.try1.png"]


def test_after_the_last_try_the_scene_keeps_its_best_picture_without_writing(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"misplaced": ["no b6f"]}, {"text": ["ATP"]}],
                         "synthase": [{"misplaced": ["stalk missing"], "wrong": ["lumpy head"]},
                                      {"misplaced": ["stalk missing"]}]})
    result = art.run(job, helper)
    assert result.endswith("; the helper still saw: overview: no b6f; synthase: stalk missing")
    assert json.loads((job / "art" / "overview.json").read_text())["try"] == 1  # the other has writing
    assert json.loads((job / "art" / "synthase.json").read_text())["try"] == 2  # as misplaced, less else wrong
    assert len(fake.painted) == 5  # two pictures a scene at most


def test_a_picture_with_only_lesser_faults_is_kept_and_they_are_recorded(job, monkeypatch):
    # Round 8: a second picture painted for a shape or a detail was no better 9 times in 11.
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"wrong": ["Calvin cycle arrows broken"]}]})
    assert art.run(job, helper) == ("painted 3 scenes with Muse Image in 3 tries; "
                                    "the helper still saw: overview: Calvin cycle arrows broken")
    assert len(fake.painted) == 3
    assert json.loads((job / "art" / "overview.json").read_text())["check"]["wrong"] == ["Calvin cycle arrows broken"]


def test_an_everyday_scene_is_asked_for_without_molecules():
    # In round 8 the style line's "proteins and molecules" scattered molecules round a sprinter.
    bundle = sample_media.bundle()
    hook, overview = (art.paint_prompt(bundle, scene) for scene in bundle.scenes[:2])
    analogy = art.paint_prompt(bundle, bundle.scenes[1].model_copy(update={"id": "analogy"}))
    assert hook.endswith(art.EVERYDAY_STYLE) and analogy.endswith(art.EVERYDAY_STYLE)
    assert art.MOLECULES not in hook + analogy
    assert overview.endswith(art.STYLE) and art.MOLECULES in overview


def test_the_helper_is_told_what_kind_of_scene_it_checks():
    bundle = sample_media.bundle()
    hook, overview, synthase = (art.check_prompt(bundle, scene) for scene in bundle.scenes)
    assert "The scene is the video's opening" in hook
    assert "The scene is the overview" in overview
    assert "The scene is a close-up of one part of the topic." in synthase
    analogy = bundle.scenes[2].model_copy(update={"id": "analogy"})
    told = art.check_prompt(bundle, analogy)
    assert "The scene is the lesson's analogy" in told and "not the topic's own parts" in told
    assert told.index("The scene is") < told.index("What the scene should show")


def test_a_scene_with_writing_in_every_picture_is_cleaned_or_kept_with_a_note(job, monkeypatch):
    # The painter is asked once to take the writing out of the least-bad try; when that works, the cleaned one is kept.
    fake = FakeMuse().install(monkeypatch)
    art.run(job, FakeHelper({"overview": [{"text": ["e-"]}, {"text": ["e-"]}, {}]}))
    made = json.loads((job / "art" / "overview.json").read_text())
    assert made["try"] == 3 and made["tries"][-1]["cleaned"] in (1, 2)
    clean = [(label, prompt) for label, prompt in fake.painted if label == "art overview clean"]
    assert clean and clean[0][1] == art.CLEAN_PROMPT
    assert art.WRITING_NOTE not in made["check"]["notes"]

    # When the writing stays, the least-bad try is kept rather than failing the droplet, and the video says so.
    fake.painted.clear()
    art.run(job, FakeHelper({"overview": [{"text": ["e-"]}]}))
    made = json.loads((job / "art" / "overview.json").read_text())
    assert made["try"] in (1, 2) and art.WRITING_NOTE in made["check"]["notes"]
    assert art.notices(job, checks.load(job / "bundle.json"))["overview"][-1] == art.WRITING_NOTE


def test_a_declined_prompt_is_a_refused_try(job, monkeypatch):
    fake = FakeMuse({"hook": [muse.Declined("Muse Image declined the picture: unsafe"), picture()]}).install(monkeypatch)
    assert art.run(job, FakeHelper()) == ("painted 3 scenes with Muse Image in 4 tries; 1 picture refused: hook: "
                                          "Muse Image declined the picture: unsafe")
    second = [prompt for label, prompt in fake.painted if label == "art hook try 2"][0]
    assert "Muse Image declined the picture: unsafe; describe the scene plainly" in second

    FakeMuse({"hook": [muse.Declined("no")]}).install(monkeypatch)
    with pytest.raises(media.StepFailed, match="scene hook: no usable picture in 2 tries: no; describe"):
        art.run(job, FakeHelper())


def test_switching_the_art_makes_it_again(job, monkeypatch):
    from studio.tests.test_art import FakeArtist

    monkeypatch.setattr(brain, "_chosen", "claude")  # the SVG art is Claude's brain's to draw
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
    assert sorted((label, phrase) for label, phrase, _ in fake.segmented) == [  # scenes at once: any order
        ("locate hook Thylakoid", "the thylakoid"), ("locate overview Cytochrome b6f", "the cytochrome b6f"),
        ("locate overview Thylakoid", "the thylakoid"), ("locate synthase ATP synthase", "the atp synthase")]

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


class TogetherMuse(FakeMuse):
    """SAM that answers the overview's two structures only once both have been asked: one after another, it
    never would."""

    def __init__(self):
        super().__init__()
        self.both = threading.Barrier(2, timeout=5)

    def segment(self, job, label, art_path, *, phrase=None, box=None):
        if label.startswith("locate overview "):
            self.both.wait()
        return super().segment(job, label, art_path, phrase=phrase, box=box)


def test_the_structures_of_a_scene_are_asked_for_at_the_same_time_and_kept_in_order(job, monkeypatch):
    painted(job, monkeypatch)
    TogetherMuse().install(monkeypatch)
    assert locate.run(job, FakeHelper()) == "located 4 of 4 structures with SAM 3.1"
    overview = jobs.read(job / media.LOCATIONS, Locations).scene("overview")
    assert [s.name for s in overview.structures] == ["Thylakoid", "Cytochrome b6f"]  # the scene's order


def test_an_unsure_match_asks_the_helper_for_a_box_and_sam_for_what_is_in_it(job, monkeypatch):
    fake = painted(job, monkeypatch, sure={"ATP synthase": 0.2})
    helper = FakeHelper(boxes={"ATP synthase": {"seen": True, "left": 0.35, "top": 0.2, "right": 0.65, "bottom": 0.8}})
    assert locate.run(job, helper) == "located 4 of 4 structures with SAM 3.1, 1 in the helper's box"
    assert helper.labels() == ["locate box synthase ATP synthase"]
    label, phrase, box = next(call for call in fake.segmented if call[0].endswith(" box"))
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


class PlainWordsMuse(FakeMuse):
    """SAM as it was on the first live runs: sure of colour-and-noun phrases ("orange region"),
    blind to the bundle's own."""

    def segment(self, job, label, art_path, *, phrase=None, box=None):
        found = super().segment(job, label, art_path, phrase=phrase, box=box)
        sure = 0.9 if phrase is None or phrase.endswith(" region") else 0.1
        return [muse.Match(m.mask, sure) for m in found]


def test_sam_is_asked_in_plainer_words_before_the_helper_is(job, monkeypatch):
    fake = painted(job, monkeypatch)
    fake = PlainWordsMuse().install(monkeypatch)
    helper = FakeHelper()
    assert locate.run(job, helper) == "located 4 of 4 structures with SAM 3.1"
    assert helper.labels() == []  # no helper box needed
    asked = [(label, phrase) for label, phrase, _ in fake.segmented if label.startswith("locate overview Cytochrome")]
    assert asked == [("locate overview Cytochrome b6f", "the cytochrome b6f"),
                     ("locate overview Cytochrome b6f try 2", "orange region")]  # "b6f" is no everyday noun
    located = jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f")
    assert located.found_by == "sam" and abs(located.centroid.x - 1300.5) < 1


def test_the_plainer_words_are_the_structures_colour_and_the_phrases_last_noun():
    def asked(phrase, color_hex):
        return locate.phrases(Structure(name="x", aliases=[], sam_phrase=phrase, color_hex=color_hex))

    assert asked("golden yellow small flashlight", "#E9A000") == [
        "golden yellow small flashlight", "yellow flashlight", "yellow region"]
    assert asked("pale green fluid stroma", "#90BE6D") == [
        "pale green fluid stroma", "light green stroma", "light green region"]
    assert asked("gold mushroom-shaped synthase complex", "#C99A2B")[1] == "yellow complex"
    assert asked("blue region", "#3A86FF") == ["blue region"]  # nothing plainer left to ask
    assert asked("the cytochrome b6f", "#E07A2F") == ["the cytochrome b6f", "orange region"]
    words = {"#E76F51": "orange", "#D64550": "red", "#2A9D8F": "teal", "#6A4C93": "purple", "#277DA1": "blue",
             "#4A7C2E": "green", "#8B4513": "brown", "#1B2A49": "dark blue", "#F4F4F4": "white", "#7F7F7F": "grey"}
    assert {h: art.colour_word(h) for h in words} == words


def test_a_match_in_another_structures_colour_is_dropped_and_the_structure_left_out(job, monkeypatch):
    # SAM puts Cytochrome b6f (orange) on the Thylakoid (green) whatever it is asked: a match it must
    # not keep, since the label would point at the wrong structure.
    painted(job, monkeypatch, spots={**SPOTS, "Cytochrome b6f": SPOTS["Thylakoid"]})
    helper = FakeHelper(boxes={"Cytochrome b6f": {"seen": True, "left": 0.2, "top": 0.3, "right": 0.35, "bottom": 0.7}})
    assert locate.run(job, helper) == ("located 3 of 4 structures with SAM 3.1; dropped 3 matches in another "
                                       "structure's colour; left out Cytochrome b6f on overview")
    assert jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f") is None


def test_a_paler_duller_version_of_a_structures_colour_counts_as_it_and_another_hue_does_not(tmp_path):
    # Round 8: asked for green, Muse Image painted ATP synthase sage with pale highlights, which plain Lab
    # distance put nearer the white oxygen than the bundle's bright green, so it was left out.
    pixels = np.full((media.ART_HEIGHT, media.ART_WIDTH, 3), SLATE, np.uint8)
    synthase_at = disc(500, 500, 150)
    pixels[synthase_at] = (138, 185, 130)  # sage
    pixels[synthase_at & disc(450, 450, 110)] = (200, 230, 195)  # its pale highlight
    pixels[disc(1300, 500, 100)] = (245, 245, 245)  # the oxygen
    pixels[disc(896, 900, 80)] = (150, 120, 90)  # brown earth: no structure's colour
    path = tmp_path / "art.png"
    path.write_bytes(svg.png(pixels))
    synthase, oxygen, cyanide = (
        Structure(name="ATP synthase", aliases=[], sam_phrase="green mushroom", color_hex="#3DDC84"),
        Structure(name="oxygen", aliases=[], sam_phrase="white spheres", color_hex="#F0F0F0"),
        Structure(name="cyanide", aliases=[], sam_phrase="magenta wedge", color_hex="#FF3DCB"))  # not painted
    colours = locate.Colours(path, Scene(id="detail", image_prompt="A membrane.",
                                         structures=[synthase, oxygen, cyanide]))
    assert colours.judge(synthase_at, synthase) == (0.0, 1.0)
    assert colours.fits(synthase_at, synthase) and not colours.fits(synthase_at, oxygen)
    assert colours.judge(disc(1300, 500, 100), oxygen) == (0.0, 1.0)
    # Brown earth is another hue. Green is painted elsewhere, so the earth is not the synthase (round 8's
    # "green turbine" came back as earth); magenta is painted nowhere, so it cannot be judged by colour.
    earth = disc(896, 900, 80)
    assert colours.judge(earth, synthase) == (0.0, 0.0) and not colours.fits(earth, synthase)
    assert colours.judge(earth, cyanide) == (0.0, None) and colours.fits(earth, cyanide)


def test_a_match_on_the_white_background_is_dropped(job, monkeypatch):
    painted(job, monkeypatch, spots={**SPOTS, "Cytochrome b6f": (200, 900, 80)})  # where nothing is painted
    result = locate.run(job, FakeHelper())
    assert result == ("located 3 of 4 structures with SAM 3.1; dropped 2 matches in another structure's colour; "
                      "left out Cytochrome b6f on overview")


def test_a_match_on_a_dark_background_is_dropped_too(job, monkeypatch):
    # The background is read off the art's edge, so the dark slate counts as background as white did.
    painted(job, monkeypatch, pictures={scene.id: [painting(scene.id, SLATE)] for scene in sample_media.bundle().scenes},
            spots={**SPOTS, "Cytochrome b6f": (200, 900, 80)})  # where nothing is painted
    result = locate.run(job, FakeHelper())
    assert result == ("located 3 of 4 structures with SAM 3.1; dropped 2 matches in another structure's colour; "
                      "left out Cytochrome b6f on overview")


def test_the_background_is_read_off_the_pictures_edge():
    pixels = np.full((200, 300, 3), SLATE, np.uint8)
    pixels[50:150, 100:200] = (200, 0, 0)
    pixels[0, :40] = (255, 255, 255)  # a stray light streak along the edge does not move the median
    assert media.paper(pixels) == SLATE


def test_a_picture_in_none_of_the_structures_colours_is_not_judged_by_colour(job, monkeypatch):
    painted(job, monkeypatch, pictures={scene.id: [picture(color=(150, 60, 200))]  # purple: no structure's
                                        for scene in sample_media.bundle().scenes})
    assert locate.run(job, FakeHelper()) == "located 4 of 4 structures with SAM 3.1"


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
    monkeypatch.setattr(brain, "_chosen", "claude")
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


def test_paint_redraws_a_reference_picture_with_the_edits_endpoint(tmp_path, monkeypatch):
    png, first = picture(), tmp_path / "prophase.png"
    first.write_bytes(picture(color=(200, 0, 0)))
    send = FakeSend((200, {"data": [{"b64_json": base64.b64encode(png).decode()}]}))
    monkeypatch.setattr(muse, "send", send)
    assert muse.paint(tmp_path, "art metaphase", "the next stage", first) == png
    request = send.requests[-1]
    assert request["url"] == "https://api.meta.ai/v1/images/edits"
    assert request["body"]["prompt"] == "the next stage"
    assert request["body"]["images"] == [
        {"image_url": "data:image/png;base64," + base64.b64encode(first.read_bytes()).decode()}]
    assert calls(tmp_path) == [("art metaphase", "muse-image-1.0", 0.01)]


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


def test_a_dropped_connection_is_asked_again(tmp_path, monkeypatch):
    tries = []

    def drops_once(method, url, headers, body):
        tries.append(url)
        if len(tries) == 1:
            raise muse.NoAnswer("Meta's API did not answer: [Errno 104] Connection reset by peer")
        return 200, {}, json.dumps({"data": [{"b64_json": base64.b64encode(picture()).decode()}]}).encode()

    monkeypatch.setattr(muse, "send", drops_once)
    assert muse.paint(tmp_path, "art hook", "a leaf") == picture() and len(tries) == 2
