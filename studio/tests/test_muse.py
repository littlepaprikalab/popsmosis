"""The Muse Image art and the SAM 3.1 locate step (Muse's brain, by default), with Meta's API stood in for."""

import base64
import hashlib
import json
import re
import shutil
import threading
import time

import numpy as np
import pytest

from studio import brain, checks, jobs, ledger, media, muse, repair, svg
from studio.schemas import Axes, Box, Bundle, Locations, Scene, Structure, Timeline, locations_problems, timeline_problems
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


SLATE = (40, 55, 75)  # the dark slate blue Muse Image was asked to paint behind the diagram until Oct 31


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
        answers = self.pictures[scene] if scene in self.pictures else [painting(scene)]
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

    def __init__(self, checks=None, boxes=None, wrong=None):
        # scene -> a list of PictureCheck dicts, one per check. A note given as a sentence is about the lesson's first
        # fact and the whole picture; a label as LABEL_BOX's dict.
        self.checks = checks or {}
        self.boxes = boxes or {}  # structure -> a Spot dict
        self.wrong = wrong or {}  # scene -> the names whose pinned label the helper sees on the wrong thing
        self.asked: list[tuple[str, str]] = []
        self.sizes: list[tuple[int, int]] = []  # each picture the check was shown, as sent
        self.rewrites: dict[str, str] = {}  # scene -> what the writer's role answers on seeing its first picture

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        assert len(images) == 1 and images[0].exists()
        self.asked.append((label, prompt))
        if system == art.REWRITE_PROMPT:  # the writer's role, after a picture went wrong: its words again
            assert role == "brain" and schema is None and label.startswith("art rewrite ")
            scene = label.split()[2]
            if scene in self.rewrites:
                return {"text": self.rewrites[scene]}
            # By default it keeps a picture the check found no part out of place or wrong in, and rewrites any other.
            clean = "\n- misplaced: " not in prompt and "\n- wrong: " not in prompt
            part = re.search(r"each to be tagged once:\n- (.+?) \(", prompt).group(1)  # its first part, tagged
            return {"text": art.KEEP if clean else f"Rewritten {scene} [[{part}]] after its picture went wrong."}
        assert role == "helper"
        if schema is art.PictureCheck:
            assert system == art.CHECK_PROMPT and "The lesson's facts:\n- f1: " in prompt
            from PIL import Image
            with Image.open(images[0]) as shown:
                self.sizes.append(shown.size)
            scene = label.split()[2]
            tries = sum(1 for lab, _ in self.asked if lab.startswith(f"art check {scene}"))
            answers = self.checks.get(scene, [{}])
            answer = {"labels": [], "misplaced": [], "wrong": [], "notes": [], **answers[min(tries, len(answers)) - 1]}
            unboxed = {"note": "", "left": 0, "top": 0, "right": 0, "bottom": 0}  # a sentence: no box round it
            answer["labels"] = [x if isinstance(x, dict) else {"text": x, **unboxed} for x in answer["labels"]]
            answer["notes"] = [x if isinstance(x, dict) else {"fact": "f1", "text": x, **unboxed} for x in answer["notes"]]
            return answer
        if schema is locate.LabelCheck:
            assert system == locate.LABELS_PROMPT and "The names pinned on it: " in prompt
            return {"wrong": [{"name": name, "on": "something else"} for name in self.wrong.get(label.split()[2], [])]}
        assert schema is locate.Spot and system == locate.BOX_PROMPT
        name = next(n for n in SPOTS if label.endswith(" " + n))
        return self.boxes.get(name, {"seen": False, "left": 0, "top": 0, "right": 1, "bottom": 1})

    def labels(self):
        """What the helper was asked, but the label check every located scene gets and the writer's rewrites."""
        return [label for label, _ in self.asked if not label.startswith(("label check ", "art rewrite "))]

    def label_checks(self):
        return [label for label, _ in self.asked if label.startswith("label check ")]


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
    assert prompt.startswith(bundle.scenes[1].image_prompt.rstrip(".")) and prompt.endswith(art.labels_line(bundle.scenes[1]))
    assert "Colours: Thylakoid green, Cytochrome b6f orange." in prompt  # names, which the model reads
    assert "#" not in prompt and "slate blue" not in prompt  # no hex codes, and no style line

    for scene in bundle.scenes:
        pixels = sample_media_image(job / media.art_path(scene.id))
        assert pixels.shape == (media.ART_HEIGHT, media.ART_WIDTH, 3)
        made = json.loads((job / "art" / f"{scene.id}.json").read_text())
        assert made["key"] == art.key(bundle, scene) and made["model"] == muse.IMAGE_MODEL and made["try"] == 1
        assert made["labelled"] is True and "flags" not in made  # it names its own parts, none of them wrongly
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
    before = json.dumps([bundle.topic, hook.model_dump(exclude={"stage_of", "axes", "to_scale", "song_only"})],
                        sort_keys=True)
    assert art.key(bundle, hook) == hashlib.sha256(before.encode()).hexdigest()[:16]


def test_a_scene_with_no_axes_keeps_the_art_key_it_had_before_axes_existed():
    # The keys main gave the sample's scenes before Scene.axes: a field added with a default must not make every
    # finished job's art look stale, which would paint it all again.
    bundle = sample_media.bundle()
    assert {s.id: art.key(bundle, s) for s in bundle.scenes} == {
        "hook": "ee481cd7f80f2d2b", "overview": "367fe4f994221ec2", "synthase": "0eb39285f943171e"}
    graph = bundle.scenes[1].model_copy(update={"axes": Axes(x="Quantity", y="Price")})
    assert art.key(bundle, graph) != art.key(bundle, bundle.scenes[1])  # naming the axes asks for them in the art


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
    assert not [label for label in helper.labels() if label.startswith(("art notes", "art flags"))]  # one look
    staged(job)  # a changed bundle: the pictures are no longer this bundle's, so nothing is disclosed for them
    assert art.notices(job, checks.load(job / "bundle.json")) == {}


def test_a_note_that_names_no_fact_of_the_lesson_is_never_shown(job, monkeypatch):
    # Round 20's kidney close-up was shown beside a note putting its glucose carriers on the wrong side, from the
    # checker's own knowledge. A note must rest on a fact of the lesson, which the fact-check passed (Nov 2).
    FakeMuse().install(monkeypatch)
    right = "The protein's head should face the stroma."
    unfounded = {"fact": "f9", "text": "The carriers belong on the capillary's side.", "note": "", "left": 0, "top": 0,
                 "right": 0, "bottom": 0}
    helper = FakeHelper(checks={"synthase": [{"misplaced": ["head in the lumen"], "notes": [right, unfounded]}]})
    art.run(job, helper)
    assert art.notices(job, checks.load(job / "bundle.json")) == {"synthase": [right]}


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


def test_a_part_out_of_place_is_asked_for_again_and_a_wrong_label_kept(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"hook": [{"labels": ["'ATP' points at the leaf"]}, {}],
                         "synthase": [{"misplaced": ["head in the lumen"]}, {}]})
    result = art.run(job, helper)
    # The step's line names each picture refused and why, so the operator's summary can tell what came back wrong.
    assert result == ("painted 3 scenes with Muse Image in 4 tries; 1 picture refused: synthase: head in the lumen; "
                      "the helper still saw: hook: 'ATP' points at the leaf")
    hook = [prompt for label, prompt in fake.painted if label.split()[1] == "hook"]
    synthase = [prompt for label, prompt in fake.painted if label.split()[1] == "synthase"]
    assert len(hook) == 1 and len(synthase) == 2  # a wrong label is no reason to paint again
    # The second try is painted from the scene's words as the writer's role wrote them again, having seen the first
    # picture and what the check found in it: not from the same words with the problem quoted after them.
    [(_, shown)] = [(label, prompt) for label, prompt in helper.asked if label == "art rewrite synthase"]
    assert "- misplaced: head in the lumen" in shown and synthase[0] in shown
    assert "refused" not in synthase[0] and synthase[1].startswith(
        'Rewritten synthase (blue, label "ATP synthase") after its picture went wrong.')
    assert "head in the lumen" not in synthase[1]
    made = json.loads((job / "art" / "synthase.json").read_text())
    assert made["try"] == 2
    # What each try was refused for is kept, not only the next prompt's quote of it, with the words it was rewritten to.
    assert made["tries"] == [{"try": 1, "labels": [], "misplaced": ["head in the lumen"], "wrong": [], "notes": [],
                              "rewritten": "Rewritten synthase [[ATP synthase]] after its picture went wrong."},
                             {"try": 2, "labels": [], "misplaced": [], "wrong": [], "notes": []}]
    assert sorted(p.name for p in (job / "art").glob("synthase.try*.png")) == ["synthase.try1.png"]


def test_after_the_last_try_the_scene_keeps_its_best_picture(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"misplaced": ["no b6f", "no PSII"]}, {"misplaced": ["no b6f"],
                                                                            "labels": ["'PSII' on the b6f"]}],
                         "synthase": [{"misplaced": ["stalk missing"], "wrong": ["lumpy head"]},
                                      {"misplaced": ["stalk missing"]}]})
    result = art.run(job, helper)
    assert result.endswith("; the helper still saw: overview: no b6f; overview: 'PSII' on the b6f; "
                           "synthase: stalk missing")
    assert json.loads((job / "art" / "overview.json").read_text())["try"] == 2  # fewer out of place, a wrong label
    assert json.loads((job / "art" / "synthase.json").read_text())["try"] == 2  # as misplaced, less else wrong
    assert len(fake.painted) == 5  # two pictures a scene at most


def test_a_picture_with_anything_wrong_is_painted_again_from_rewritten_words(job, monkeypatch):
    # Round 8: a second picture painted for a shape or a detail, from the same words, was no better 9 times in 11. Nov
    # 2: painted again from words rewritten by the writer's role, which saw the picture, the airplane wing's slabs and
    # the filled signal bars (each only "wrong") came out right; the owner had wrong pictures painted again too.
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"wrong": ["Calvin cycle arrows broken"]}, {}]})
    assert art.run(job, helper) == ("painted 3 scenes with Muse Image in 4 tries; "
                                    "1 picture refused: overview: Calvin cycle arrows broken")
    assert len(fake.painted) == 4
    made = json.loads((job / "art" / "overview.json").read_text())
    assert made["try"] == 2 and made["check"]["wrong"] == []
    assert made["prompt"].startswith("Rewritten overview (green, label \"Thylakoid\") after its picture went wrong.")


def test_the_writer_looks_at_every_first_picture_and_only_its_rewrite_is_painted_again(job, monkeypatch):
    # The owner (Nov 2): the check missed the airplane wing's slabs, and a second painting of a good picture can come
    # out worse, so the writer's role sees each first picture and the scene is painted again only when it asks.
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"wrong": ["Calvin cycle arrows broken"]}]})
    helper.rewrites = {"hook": "A leaf [[Thylakoid]] in the sun, drawn larger.", "overview": art.KEEP}
    art.run(job, helper)
    assert sorted(label for label, _ in helper.asked if label.startswith("art rewrite ")) == [
        "art rewrite hook", "art rewrite overview", "art rewrite synthase"]
    painted = [label.split()[1] for label, _ in fake.painted]
    assert sorted(painted) == ["hook", "hook", "overview", "synthase"]  # the hook's picture was clean, and redone
    made = json.loads((job / "art" / "overview.json").read_text())
    assert made["try"] == 1 and made["tries"][0]["kept"] and made["check"]["wrong"] == ["Calvin cycle arrows broken"]
    assert json.loads((job / "art" / "hook.json").read_text())["tries"][0]["rewritten"] == helper.rewrites["hook"]


def test_a_picture_the_writer_rejected_is_not_kept_over_its_repaint_for_a_small_fault(job, monkeypatch):
    # Nov 2: the writer rejected the airplane's stall picture, which the check had passed (no air broke away over the
    # top), and its repaint came back with one small fault; the step kept the rejected one by the check alone.
    FakeMuse().install(monkeypatch)
    helper = FakeHelper({"hook": [{}, {"wrong": ["the leaf is a little pale"]}]})
    helper.rewrites = {"hook": "A leaf [[Thylakoid]] in the sun, its veins showing."}
    assert "1 picture refused: hook: the writer asked for it again" in art.run(job, helper)
    made = json.loads((job / "art" / "hook.json").read_text())
    assert made["try"] == 2 and made["check"]["wrong"] == ["the leaf is a little pale"]


def test_a_rewrite_that_fails_paints_again_from_the_same_words(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)

    class NoWriter(FakeHelper):
        def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
            if system == art.REWRITE_PROMPT:
                raise brain.BrainError("the brain is down")
            return super().__call__(role, prompt, schema, images, system=system, label=label)

    art.run(job, NoWriter({"synthase": [{"misplaced": ["head in the lumen"]}, {}]}))
    synthase = [prompt for label, prompt in fake.painted if label.split()[1] == "synthase"]
    assert len(synthase) == 2 and "refused: head in the lumen" in synthase[1]
    assert len(fake.painted) == 4  # the clean hook and overview are not painted again
    assert "rewritten" not in json.loads((job / "art" / "synthase.json").read_text())["tries"][0]


def test_every_scene_is_asked_to_label_its_structures_and_for_no_look():
    # Oct 31: without the style line (a flat dark slate blue background, rounded proteins, only the parts named, no
    # text) the pictures came back better, and the operator's labels line, last, got them labelled.
    bundle = sample_media.bundle()
    for scene in bundle.scenes:
        prompt = art.paint_prompt(bundle, scene)
        colours = prompt.split(" Colours: ")[1].split(". ")[0]
        assert prompt.endswith(" Colours: " + colours + ". " + art.labels_line(scene))
        assert not any(old in prompt for old in ("slate blue", "No text", "Proteins and molecules", "Draw only"))
    refused = art.paint_prompt(bundle, bundle.scenes[1], ["no b6f"])
    assert refused.endswith("refused: no b6f. Make it again with these fixed. " + art.labels_line(bundle.scenes[1]))


def test_the_helper_is_told_what_kind_of_scene_it_checks():
    bundle = sample_media.bundle()
    hook, overview, synthase = (art.check_prompt(bundle, scene) for scene in bundle.scenes)
    assert "The scene is the video's opening" in hook
    assert "The scene is the overview" in overview
    assert "The scene is a close-up of one part of the topic." in synthase
    assert overview.index("The scene is") < overview.index("What the scene should show")
    song = bundle.scenes[2].model_copy(update={"id": "chorus", "song_only": True})
    assert f"The scene is {art.SONG}" in art.check_prompt(bundle, song)
    # Which video plays a picture is no part of what it shows: a song's picture the lesson takes keeps its painting.
    assert art.key(bundle, song) == art.key(bundle, song.model_copy(update={"song_only": False}))


# Where the helper finds a wrong label, and its post-it: a box as fractions of the art, round art pixels
# (840, 140)-(960, 210).
LABEL_BOX = {"text": "PSI", "note": "This is cytochrome b6f", "left": 840 / media.ART_WIDTH,
             "top": 140 / media.ART_HEIGHT, "right": 960 / media.ART_WIDTH, "bottom": 210 / media.ART_HEIGHT}


def test_a_wrong_label_is_kept_and_flagged_where_the_helper_finds_it(job, monkeypatch):
    fake = FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"labels": [LABEL_BOX]}]})
    assert art.run(job, helper) == ("painted 3 scenes with Muse Image in 3 tries; "
                                    "the helper still saw: overview: 'PSI': This is cytochrome b6f")
    assert [label for label, _ in fake.painted if label.split()[1] == "overview"] == ["art overview"]  # not again
    # One look boxes the label: no second look at it (Nov 2), and the picture it saw was shrunk to CHECK_WIDTH.
    assert [label for label in helper.labels() if "overview" in label] == ["art check overview"]
    assert all(width == art.CHECK_WIDTH for width, _ in helper.sizes)
    made = json.loads((job / "art" / "overview.json").read_text())
    assert made["labelled"] is True and made["flags"] == [LABEL_BOX]
    bundle = checks.load(job / "bundle.json")
    [flag] = art.flags(job, bundle)["overview"]
    assert (flag.box.x0, flag.box.y0, flag.box.x1, flag.box.y1) == pytest.approx((840, 140, 960, 210))  # art pixels
    assert flag.note == "This is cytochrome b6f"
    assert list(art.flags(job, bundle)) == ["overview"] and art.notices(job, bundle) == {}
    assert sorted(art.self_labelled(job, bundle)) == ["hook", "overview", "synthase"]

    # A wrong label with no box round it is not flagged; the picture stands.
    art.run(job, FakeHelper({"overview": [{"labels": [{**LABEL_BOX, "left": 0.5, "right": 0.5}]}]}))
    made = json.loads((job / "art" / "overview.json").read_text())
    assert "flags" not in made and art.flags(job, bundle) == {} and made["check"]["labels"] == [
        "'PSI': This is cytochrome b6f"]


def test_a_picture_painted_before_it_labelled_itself_is_labelled_by_the_video(job, monkeypatch):
    # Its writing was painted out for the video's labels, and its record says nothing of labels.
    FakeMuse().install(monkeypatch)
    art.run(job, FakeHelper())
    record = job / "art" / "hook.json"
    made = json.loads(record.read_text())
    del made["labelled"]
    jobs.write(record, made)
    assert sorted(art.self_labelled(job, checks.load(job / "bundle.json"))) == ["overview", "synthase"]


def test_a_declined_prompt_is_a_refused_try(job, monkeypatch):
    fake = FakeMuse({"hook": [muse.Declined("Muse Image declined the picture: unsafe"), picture()]}).install(monkeypatch)
    assert art.run(job, FakeHelper()) == ("painted 3 scenes with Muse Image in 4 tries; 1 picture refused: hook: "
                                          "Muse Image declined the picture: unsafe")
    second = [prompt for label, prompt in fake.painted if label == "art hook try 2"][0]
    assert "Muse Image declined the picture: unsafe; describe the scene plainly" in second

    FakeMuse({"hook": [muse.Declined("no")]}).install(monkeypatch)
    with pytest.raises(media.StepFailed, match="scene hook: no usable picture in 2 tries: no; describe"):
        art.run(job, FakeHelper())


def test_a_topic_past_its_spend_ceiling_asks_meta_for_nothing_and_fails_the_step(job, monkeypatch):
    """The ceiling is checked before each picture, and is not a refused picture: the art step fails on it."""
    sent = []
    monkeypatch.setattr(muse, "send", lambda *a: sent.append(a) or (500, {}, b"{}"))
    (job / "settings.json").write_text(json.dumps({"art": {"claude": "gemini", "meta": "muse"}, "spend": {"topicUsd": 1}}))
    (job / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "muse-wonder-1.0", "cost_usd": 1.2}) + "\n")
    with pytest.raises(ledger.OverCeiling, match=r"cost \$1\.20"):
        muse.paint(job, "art hook", "a chloroplast")
    with pytest.raises(ledger.OverCeiling):
        muse.segment(job, "locate hook Thylakoid", job / media.art_path("hook"), phrase="thylakoid")
    with pytest.raises(ledger.OverCeiling):
        art.run(job, FakeHelper())
    assert sent == []


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


def test_the_songs_own_picture_is_painted_located_and_played_by_the_song_alone(job, monkeypatch, tmp_path):
    """Across the seams: a song's picture from the bundle through the art, the locate step, both timelines and a
    frame of the song video."""
    from studio.render.encode import write_poster
    from studio.steps import timeline

    data = sample_media.bundle().model_dump()
    data["song_scenes"] = [{"id": "chorus", "image_prompt": "ATP synthase in the membrane, up close.", "structures": [
        {"name": "ATP synthase", "aliases": [], "sam_phrase": "the atp synthase", "color_hex": "#3366CC"}]}]
    for d in data["direction"]:
        if d["line_id"].startswith("c"):  # the chorus, three times
            d["scene"] = "chorus"
    bundle, report = checks.review(Bundle.model_validate(data))
    assert report.problems == [] and bundle.scenes[-1].song_only
    jobs.write(job / "bundle.json", bundle)
    pixels = np.full((media.ART_HEIGHT, media.ART_WIDTH, 3), 255, np.uint8)
    pixels[disc(*SPOTS["ATP synthase"])] = (0x33, 0x66, 0xCC)
    fake = FakeMuse(pictures={"chorus": [svg.png(pixels)]}).install(monkeypatch)
    helper = FakeHelper()
    art.run(job, helper)
    assert sorted(label for label, _ in fake.painted) == ["art chorus", "art hook", "art overview", "art synthase"]
    assert any(f"The scene is {art.SONG}" in prompt for _, prompt in helper.asked)
    assert locate.run(job, FakeHelper()) == "located 5 of 5 structures with SAM 3.1"
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert locations_problems(locations, bundle) == []

    timeline.run(job)
    song, lesson = (jobs.read(job / media.timeline_path(video), Timeline) for video in ("song", "lesson"))
    assert {b.line_id for b in song.beats if b.scene == "chorus"} == {"c1-1", "c2-1", "c3-1"}
    assert "chorus" not in {b.scene for b in lesson.beats} and "chorus" in song.ai_pictures
    assert timeline_problems(song, locations) == [] and timeline_problems(lesson, locations) == []
    poster = tmp_path / "song.jpg"
    at = write_poster(job, song, poster)
    assert poster.stat().st_size > 0 and 0 <= at <= song.duration


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
    for where in overview.structures:  # each with its own match, not another's answered at the same time
        x, y, r = SPOTS[where.name]
        assert (where.box.x0, where.box.y0) == (x - r, y - r)


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


def test_a_structure_sam_cannot_trace_even_in_the_helpers_box_is_its_colour_there(job, monkeypatch):
    # Round 19's glomerulus, a knot of thin pink tubes, was no one shape to SAM, even boxed.
    painted(job, monkeypatch, spots={k: v for k, v in SPOTS.items() if k != "Cytochrome b6f"})
    helper = FakeHelper(boxes={"Cytochrome b6f": {"seen": True, "left": 0.7, "top": 0.4, "right": 0.8, "bottom": 0.6}})
    assert locate.run(job, helper) == "located 4 of 4 structures with SAM 3.1, 1 in the helper's box"
    found = jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f")
    assert found.found_by == "helper" and 1200 <= found.label_anchor.x <= 1400  # where it is painted


def test_a_structure_nobody_finds_is_left_out_unless_too_few_are_left(job, monkeypatch):
    painted(job, monkeypatch, spots={k: v for k, v in SPOTS.items() if k != "Cytochrome b6f"})
    # The helper's box holds none of its colour either.
    helper = FakeHelper(boxes={"Cytochrome b6f": {"seen": True, "left": 0.02, "top": 0.4, "right": 0.1, "bottom": 0.6}})
    result = locate.run(job, helper)
    assert result == "located 3 of 4 structures with SAM 3.1; left out Cytochrome b6f on overview"
    assert jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f") is None

    painted(job, monkeypatch, spots={k: v for k, v in SPOTS.items() if k != "ATP synthase"})
    with pytest.raises(media.StepFailed, match="scene synthase: SAM 3.1 found 0 of 1 structures; not ATP synthase"):
        locate.run(job, FakeHelper())
    assert not (job / media.LOCATIONS).exists()


def test_a_label_pinned_on_the_wrong_thing_is_found_again_in_the_helpers_box_or_left_out(job, monkeypatch):
    # Round 19's rock cycle pinned "Mountain" on a purple band underground: SAM's "grey mountain" found both.
    painted(job, monkeypatch)
    boxed = {"Cytochrome b6f": {"seen": True, "left": 0.7, "top": 0.4, "right": 0.8, "bottom": 0.6}}
    helper = FakeHelper(boxes=boxed, wrong={"overview": ["Cytochrome b6f"]})
    assert locate.run(job, helper) == "located 4 of 4 structures with SAM 3.1, 1 in the helper's box"
    assert sorted(helper.label_checks()) == ["label check hook", "label check overview", "label check synthase"]
    assert helper.labels() == ["locate box overview Cytochrome b6f"]
    assert jobs.read(job / media.LOCATIONS, Locations).scene("overview").get("Cytochrome b6f").found_by == "sam-box"
    pinned = next(prompt for label, prompt in helper.asked if label == "label check overview")
    assert pinned.endswith("The names pinned on it: Thylakoid, Cytochrome b6f.")

    unseen = FakeHelper(wrong={"overview": ["Cytochrome b6f"]})  # nor can the helper box it: left out
    assert locate.run(job, unseen) == "located 3 of 4 structures with SAM 3.1; left out Cytochrome b6f on overview"


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
             "#4A7C2E": "green", "#8B4513": "brown", "#1B2A49": "dark blue", "#F4F4F4": "white", "#7F7F7F": "grey",
             # the pinks and purples round 17 named wrong ("red", "light pink"), and plain pink, once "light red"
             "#F06292": "pink", "#BA68C8": "light purple", "#9C27B0": "purple", "#FFC0CB": "pink",
             "#DC143C": "red",
             # Nov 1: the browns the writer named so ("orange" until then), and water's cyan beside a teal
             "#A0703C": "brown", "#A86B32": "brown", "#A0522D": "brown", "#00ACC1": "cyan", "#00897B": "teal",
             "#17A2A2": "teal",
             # Nov 2: the beetles' leaves, "light green" to the writer, were painted teal; teal itself stays teal
             "#43AA8B": "green", "#008080": "teal"}
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


def test_no_more_sam_calls_wait_on_meta_at_once_than_its_slots_however_many_ask(tmp_path, monkeypatch):
    art_file = tmp_path / "hook.png"
    art_file.write_bytes(picture(media.ART_WIDTH, media.ART_HEIGHT))
    monkeypatch.setattr(muse, "_sam_slots", threading.BoundedSemaphore(2))
    counting, waiting, most = threading.Lock(), [0], [0]

    def send(method, url, headers, body):
        with counting:
            waiting[0] += 1
            most[0] = max(most[0], waiting[0])
        time.sleep(0.05)
        with counting:
            waiting[0] -= 1
        return 200, {}, json.dumps({"status": "completed", "output": []}).encode()

    monkeypatch.setattr(muse, "send", send)
    ledger.parallel(lambda i: muse.segment(tmp_path, f"locate hook Thylakoid {i}", art_file, phrase="green disc"),
                    range(8), 8)
    assert most[0] == 2
    assert len(calls(tmp_path)) == 8


def test_a_sam_call_keeps_its_slot_while_it_waits_to_ask_again(tmp_path, monkeypatch):
    art_file = tmp_path / "hook.png"
    art_file.write_bytes(picture(media.ART_WIDTH, media.ART_HEIGHT))
    monkeypatch.setattr(muse, "_sam_slots", threading.BoundedSemaphore(1))
    held = []
    monkeypatch.setattr(muse, "sleep", lambda s: held.append(not muse._sam_slots.acquire(blocking=False)))
    monkeypatch.setattr(muse, "send", FakeSend((429, {"error": {"message": "busy"}}),
                                               (200, {"status": "completed", "output": []})))
    assert muse.segment(tmp_path, "locate hook Thylakoid", art_file, phrase="green disc") == []
    assert held == [True]  # a busy Meta slows the others down rather than letting another call in


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
    for schema in (art.PictureCheck, locate.Spot):
        brain.strict_schema(schema)  # raises on a keyword one provider would refuse
    for prompt in (art.CHECK_PROMPT, locate.BOX_PROMPT, *map(art.labels_line, sample_media.bundle().scenes)):
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


def test_a_note_about_one_place_is_stuck_there_and_one_about_the_whole_picture_keeps_its_short_note(job, monkeypatch):
    # The owner (Nov 1): a note on a card beside the picture left the learner to find the place; one about a place
    # is a sticky note there, with no ring. One with no one place goes in a corner of the picture as its short note
    # (Nov 2), or as the sentence where the check wrote none.
    placed = "The picture shows only plus signs outside; the inner face of the membrane is negative."
    whole = "The picture shows far too many pumps for one patch of membrane."
    sentence = "The picture draws the membrane far too thick for its pumps."
    FakeMuse().install(monkeypatch)
    pin = {"fact": "f2", "text": placed, "note": "The inner face is negative", "left": 0.1, "top": 0.6, "right": 0.9,
           "bottom": 0.7}
    short = {"fact": "f2", "text": whole, "note": "One patch holds a few pumps", "left": 0, "top": 0, "right": 0,
             "bottom": 0}
    helper = FakeHelper({"overview": [{"notes": [pin, short, sentence]}]})
    art.run(job, helper)
    bundle = checks.load(job / "bundle.json")
    assert art.notices(job, bundle) == {"overview": ["One patch holds a few pumps", sentence]}
    [flag] = art.flags(job, bundle)["overview"]
    assert flag.note == "The inner face is negative" and flag.ring is False
    assert (flag.box.y0, flag.box.y1) == pytest.approx((0.6 * media.ART_HEIGHT, 0.7 * media.ART_HEIGHT))


def test_a_wrong_label_reaches_the_videos_flagged_on_its_picture_and_noted_beside_it(job, monkeypatch):
    # Across the steps: the painted picture labels its own parts, so the video draws none over it; the label the
    # check found wrong is flagged on the picture with its sticky note, and a corner of the picture keeps only the
    # notes that are not about it, in both timelines, upright and wide, the picture keeping the whole frame.
    from studio.render import primitives as p
    from studio.render.renderer import Renderer
    from studio.steps import timeline as timeline_step

    note = "Cytochrome b6f should sit across the membrane, not on it."
    FakeMuse().install(monkeypatch)
    helper = FakeHelper({"overview": [{"labels": [LABEL_BOX], "notes": [note]}]})
    art.run(job, helper)
    locate.run(job, helper)
    timeline_step.run(job)
    song = jobs.read(job / media.timeline_path("song"), Timeline)
    lesson = jobs.read(job / media.timeline_path("lesson"), Timeline)
    assert song.notices == {"overview": [note]}  # the flagged label's note is its sticky note's alone
    assert lesson.notices == ({"overview": [note]} if any(b.scene == "overview" for b in lesson.beats) else {})
    shown = sorted({b.scene for b in song.beats})
    assert sorted(song.self_labelled) == shown and list(song.flags) == ["overview"]
    assert (song.flags["overview"][0].box.x0, song.flags["overview"][0].box.y1) == pytest.approx((840, 210))
    assert song.flags["overview"][0].note == "This is cytochrome b6f"
    hook_path = job / media.timeline_path(media.HOOK)
    hook = jobs.read(hook_path, Timeline)  # the sample's first chorus and the verse line before it
    yellow = p.rgb("#FFE27A")

    def corner_note(renderer: Renderer, frame: np.ndarray) -> bool:
        rect = renderer._corners["overview"][0]
        b, g, r, _ = frame[round(rect[3] - 10), round((rect[0] + rect[2]) / 2)]  # under its words
        return bool(np.allclose((r, g, b), yellow, atol=12))

    upright = Renderer(job, hook, "v")
    beat = next(b for b in hook.beats if b.scene == "overview")
    view = upright.view("overview")
    assert view == Renderer(job, hook.model_copy(update={"notices": {}}), "v").view("overview")  # nothing moves
    assert view[0] == 0 and view[2] == upright.layout.width
    assert corner_note(upright, upright.frame((beat.t0 + beat.t1) / 2))
    wide = Renderer(job, song)
    beat = next(b for b in song.beats if b.scene == "overview" and b.labels)
    t = (beat.t0 + beat.t1) / 2
    framed = wide.frame(t).copy()
    assert wide.view("overview")[2] == wide.layout.width and corner_note(wide, framed)

    def drawn(timeline: Timeline) -> np.ndarray:
        """Where the frame at t differs from the one the timeline as it is draws: what it adds or leaves out."""
        return np.abs(Renderer(job, timeline).frame(t)[..., :3].astype(int) - framed[..., :3]).sum(axis=2) > 30

    # The wrong label is above the top of the wide frame, the picture cropped to fill it: never on screen, so its note
    # goes in the corner with the rest rather than nowhere. Unflagged, the frame loses it; labelled by the video, it
    # gains its label pills.
    assert wide._corner_notes("overview") == [note, "This is cytochrome b6f"]
    flag = drawn(song.model_copy(update={"flags": {}}))
    shown = framed[flag][..., [2, 1, 0]].astype(int)
    assert (np.abs(shown - yellow).sum(axis=1) < 30).sum() > 5000
    assert drawn(song.model_copy(update={"self_labelled": []})).sum() > 1000
