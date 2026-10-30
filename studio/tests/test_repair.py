import json

import numpy as np
import pytest

pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")

from studio import media, repair, svg
from studio.schemas import BuildUp
from studio.steps.art import ArtCheck
from studio.tests import sample_media

BLUE, GREY, PINK, GREEN, GOLD = (60, 120, 220), (150, 150, 150), (240, 180, 190), (40, 200, 90), (240, 200, 40)


def banded(h=100, w=240):
    """A membrane in cross-section: a blue space above, a grey membrane, a pink space below."""
    pixels = np.zeros((h, w, 3), np.uint8)
    pixels[:int(0.4 * h)], pixels[int(0.4 * h):int(0.6 * h)], pixels[int(0.6 * h):] = BLUE, GREY, PINK
    return pixels


def mushroom(h=100, w=240, s=1.0):
    """A green mushroom-shaped protein, its cap in the blue space and its stalk down through the membrane."""
    mask = np.zeros((h, w), bool)
    ys, xs = np.ogrid[:h, :w]
    cx = w / 2
    mask |= ((xs - cx) / (26 * s)) ** 2 + ((ys - 20 * s) / (12 * s)) ** 2 <= 1
    mask[int(32 * s):int(76 * s), int(cx - 4 * s):int(cx + 5 * s)] = True
    return mask


def test_specks_are_cleared_from_a_space_and_nothing_else():
    pixels = banded()
    space = np.zeros((100, 240), bool)
    space[:40] = True
    pixels[10:14, 30:34] = GOLD  # a painted ion: a speck
    pixels[20:24, 200:204] = GOLD  # touching another structure: kept
    pixels[2:38, 60:180] = GOLD  # far too big to be a speck: kept
    other = np.zeros_like(space)
    other[24:30, 198:206] = True
    assert repair.clear_space(pixels, space, [other]) == 1
    assert tuple(pixels[12, 32]) == BLUE
    assert tuple(pixels[22, 202]) == GOLD and tuple(pixels[20, 120]) == GOLD
    assert tuple(pixels[50, 32]) == GREY  # outside the space: untouched
    pixels[38:41, 100:104] = GOLD  # on the space's edge: its shading, not a speck
    assert repair.clear_space(pixels, space, [other]) == 0 and tuple(pixels[39, 101]) == GOLD


def test_a_mushroom_turns_over_about_its_stalk_and_its_old_place_is_filled_from_beside_it():
    pixels, mask = banded(), mushroom()
    pixels[mask] = GREEN
    assert 40 <= repair.waist(mask) <= 60  # the stalk's middle, in the membrane
    new = repair.flip(pixels, mask, [])
    ys = np.nonzero(new.any(1))[0]
    cap_rows = [y for y in ys if new[y].sum() > 20]
    assert min(cap_rows) >= 60  # the cap is in the pink space below now
    assert tuple(pixels[20, 102]) == BLUE  # where the cap was, beside the stalk, is the blue space again
    assert tuple(pixels[min(cap_rows) + 2, 120]) == GREEN
    assert (pixels[new] == GREEN).all()


INK = (30, 30, 30)


def test_writing_is_painted_out_where_it_is_boxed_and_the_part_beside_it_stays():
    h, w = 200, 400
    rows = np.linspace(0.0, 1.0, h)[:, None, None]  # a soft wash, light yellow to light blue down the picture
    pixels = np.broadcast_to((1 - rows) * np.array([240, 230, 160]) + rows * np.array([190, 225, 245]),
                             (h, w, 3)).round().astype(np.uint8).copy()
    wash = pixels.copy()
    ys, xs = np.ogrid[:h, :w]
    disc = (xs - 300) ** 2 + (ys - 100) ** 2 <= 40 ** 2  # a part, wider than any stroke, beside the writing
    pixels[disc] = GREEN
    letters = np.zeros((h, w), bool)
    letters[60:63, 196:226] = letters[60:90, 210:213] = True  # a T
    letters[60:116, 232:235] = letters[113:116, 232:246] = True  # an L, running on below its box
    pixels[letters] = INK
    painted = pixels.copy()
    box = repair.Written(text="TL", left=194 / w, top=58 / h, right=248 / w, bottom=92 / h)
    assert repair.unwrite(pixels, [box]) == 1
    assert np.abs(pixels[letters].astype(int) - wash[letters].astype(int)).max() <= 6  # the wash, all of both letters
    assert (pixels[disc] == GREEN).all()
    near = np.zeros((h, w), bool)
    near[40:130, 180:280] = True  # the box, grown, and the L's foot
    assert (pixels[~near] == painted[~near]).all()  # nothing else touched
    assert repair.unwrite(pixels, [repair.Written(text="", left=0, top=0, right=1, bottom=1)]) == 0  # not a line


def scene_job(tmp_path, misplaced, builds_up=()):
    job = sample_media.write_job(tmp_path / "sample", art=True)
    bundle = sample_media.bundle().model_copy(update={"builds_up": [BuildUp(carries="H⁺", region=r) for r in builds_up]})
    scene = bundle.scenes[2]  # the synthase close-up: ATP synthase alone
    pixels = banded(media.ART_HEIGHT, media.ART_WIDTH)
    mask = np.zeros((media.ART_HEIGHT, media.ART_WIDTH), bool)
    mask[:, 600:1200] = mushroom(media.ART_HEIGHT, 600, s=media.ART_HEIGHT / 100)
    pixels[mask] = GREEN
    (job / media.art_path(scene.id)).write_bytes(svg.png(pixels))
    (job / "art" / f"{scene.id}.json").write_text(json.dumps({"check": {"text": [], "misplaced": misplaced, "wrong": []}}))
    return job, bundle, scene, {"ATP synthase": (mask, svg.locate(mask))}


class Helper:
    def __init__(self, upside_down, after):
        self.upside_down, self.after, self.asked = upside_down, after, []

    def __call__(self, role, prompt, schema, images, *, system, label):
        self.asked.append(label)
        assert schema is repair.UpsideDown and system == repair.FLIP_PROMPT and "ATP synthase" in prompt
        return {"upside_down": self.upside_down}

    def check(self, job, bundle, scene):
        self.asked.append("check")
        return ArtCheck(text=[], misplaced=self.after, wrong=[])


def test_a_part_found_upside_down_is_turned_over_when_the_check_then_finds_it_right(tmp_path):
    job, bundle, scene, located = scene_job(tmp_path, ["ATP synthase head above the membrane"])
    helper = Helper(["ATP synthase"], after=[])
    before = (job / media.art_path(scene.id)).read_bytes()
    assert repair.repair(job, bundle, scene, located, helper, helper.check) == ["turned ATP synthase over"]
    assert helper.asked == ["repair synthase", "check"]
    assert (job / media.art_path(scene.id)).read_bytes() != before
    made = json.loads((job / "art" / f"{scene.id}.json").read_text())
    assert made["repaired"] == ["turned ATP synthase over"] and made["check"]["misplaced"] == []
    _, found = located["ATP synthase"]
    assert found.centroid.y > 0.6 * media.ART_HEIGHT  # its mask turned over with it: the cap is below now


def test_a_flip_the_check_finds_no_better_is_undone(tmp_path):
    job, bundle, scene, located = scene_job(tmp_path, ["ATP synthase head above the membrane"])
    helper = Helper(["ATP synthase"], after=["ATP synthase head below the membrane"])
    before = (job / media.art_path(scene.id)).read_bytes()
    assert repair.repair(job, bundle, scene, located, helper, helper.check) == []
    assert (job / media.art_path(scene.id)).read_bytes() == before


def test_nothing_out_of_place_asks_nothing_and_a_space_that_fills_is_cleared_for_free(tmp_path):
    job, bundle, scene, located = scene_job(tmp_path, [], builds_up=["Stroma"])
    space = np.zeros((media.ART_HEIGHT, media.ART_WIDTH), bool)
    space[:380] = True
    pixels = repair.read_rgb(job / media.art_path(scene.id))
    pixels[100:106, 100:106] = GOLD
    (job / media.art_path(scene.id)).write_bytes(svg.png(pixels))
    located["Stroma"] = (space, svg.locate(space))
    helper = Helper([], after=[])
    assert repair.repair(job, bundle, scene, located, helper, helper.check) == ["cleared 1 speck from Stroma"]
    assert helper.asked == []
    assert tuple(repair.read_rgb(job / media.art_path(scene.id))[103, 103]) == BLUE
