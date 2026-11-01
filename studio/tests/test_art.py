"""The art and locate steps, with a stand-in brain that answers with the SVGs in studio/tests/art/."""

import ast
import colorsys
import copy
import hashlib
import inspect
import math
import textwrap
import xml.etree.ElementTree as ET
import shutil
from pathlib import Path

import numpy as np
import pytest
import skia

from studio import checks, jobs, media, svg
from studio.schemas import Axes, Bundle, Locations, Structure, locations_problems
from studio.steps import art, locate
from studio.tests import sample_media
from studio.tests.sample import bundle_data

DRAWINGS = Path(__file__).resolve().parent / "art"
PAGE = tuple(int(svg.PAGE[i:i + 2], 16) for i in (1, 3, 5))


def drawing(scene: str) -> str:
    return (DRAWINGS / f"{scene}.svg").read_text(encoding="utf-8")


class FakeArtist:
    """Stands in for brain.ask: answers each scene with `answers[scene]`, one per try."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        assert role == "brain" and schema is None and system == art.PROMPT
        scene = label.split()[1]
        self.prompts.append((label, prompt))
        tries = sum(1 for lab, _ in self.prompts if lab.split()[1] == scene)
        answers = self.answers.get(scene, [drawing(scene)])
        return {"text": f"Here is the scene:\n```svg\n{answers[min(tries, len(answers)) - 1]}\n```"}

    def labels(self):
        return [label for label, _ in self.prompts]


@pytest.fixture(autouse=True)
def svg_art(monkeypatch):
    """These are the brain's SVG art, as an admin can set it for Claude's brain; studio/tests/test_muse.py and
    test_gemini.py have the painted art."""
    monkeypatch.setattr(art, "painter", lambda job: "svg")


@pytest.fixture
def job(tmp_path):
    path = sample_media.write_job(tmp_path / "sample")
    (path / media.LOCATIONS).unlink()  # the sample's own, made of ellipses
    return path


def image(path: Path, color_type=skia.kRGBA_8888_ColorType) -> np.ndarray:
    return skia.Image.open(str(path)).toarray(colorType=color_type)


def png_kind(path: Path) -> tuple[int, int, int, int]:
    """Width, height, bit depth and colour type (0 greyscale, 2 RGB) from a PNG's header."""
    data = path.read_bytes()
    return int.from_bytes(data[16:20]), int.from_bytes(data[20:24]), data[24], data[25]


def svg_text(*body: str, viewbox: str = "0 0 1792 1024") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{"".join(body)}</svg>'


def blob(name: str, color: str, x: int = 896, y: int = 512, r: int = 120) -> str:
    return f'<g data-structure="{name}"><circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/></g>'


# --- the whole step --------------------------------------------------------------------


def test_the_art_step_writes_the_art_the_masks_and_the_locations(job):
    artist = FakeArtist()
    result = art.run(job, artist)
    assert result == "drew 3 scenes, located 4 of 4 structures"
    assert artist.labels() == ["art hook", "art overview", "art synthase"]

    bundle = sample_media.bundle()
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert locations_problems(locations, bundle) == []
    assert art.is_done(job) and locate.is_done(job)
    assert locate.run(job) == "nothing to do: the art step located every structure"
    for scene in locations.scenes:
        assert png_kind(job / scene.art) == (media.ART_WIDTH, media.ART_HEIGHT, 8, 2)
        pixels = image(job / scene.art)
        assert media.paper(pixels) == PAGE == tuple(pixels[-1, -1, :3])  # the house page around it
        assert locations.scene(scene.scene).look == svg.LOOK
        assert (job / "art" / f"{scene.scene}.svg").exists()
        for s in scene.structures:
            assert s.found_by == "svg"
            assert png_kind(job / s.mask) == (media.ART_WIDTH, media.ART_HEIGHT, 8, 0)
            mask = image(job / s.mask, skia.kGray_8_ColorType)
            assert set(np.unique(mask)) == {0, 255}
            assert mask[int(s.label_anchor.y), int(s.label_anchor.x)] == 255, (scene.scene, s.name)

    # The structure is drawn in its own colour where its mask is.
    overview = locations.scene("overview")
    b6f = overview.get("Cytochrome b6f")
    red, green, blue = image(job / overview.art)[int(b6f.label_anchor.y), int(b6f.label_anchor.x), :3]
    assert red > 150 and green < red and blue < green  # orange, #E07A2F shaded

    # Cytochrome b6f sits in the thylakoid membrane, drawn after it: that part is not in the
    # thylakoid's mask, and its label anchor keeps clear of the protein.
    thylakoid_mask = image(job / overview.get("Thylakoid").mask, skia.kGray_8_ColorType)
    assert thylakoid_mask[int(b6f.centroid.y), int(b6f.centroid.x)] == 0
    anchor = overview.get("Thylakoid").label_anchor
    assert not b6f.box.holds(anchor)


def test_a_refused_drawing_is_asked_for_again_with_its_problems(job):
    with_text = drawing("hook").replace("</svg>", '<text x="10" y="20">Thylakoid</text></svg>')
    artist = FakeArtist({"hook": [with_text, drawing("hook")]})
    art.run(job, artist)
    assert artist.labels()[:3] == ["art hook", "art hook try 2", "art overview"]
    retry = artist.prompts[1][1]
    assert "<text>" in retry and "<last_drawing>" in retry and "Thylakoid</text>" in retry
    assert (job / "art" / "hook.try1.svg").exists()


def test_a_structure_still_missing_after_the_last_try_is_left_out(job):
    no_b6f = drawing("overview").split('<g data-structure="Cytochrome b6f">')[0] + "</svg>"
    artist = FakeArtist({"overview": [no_b6f]})
    result = art.run(job, artist)
    assert artist.labels().count("art overview") == 1 and "art overview try 3" in artist.labels()
    # The two tries not kept are named once, with what they were refused for: here, the same structure.
    assert result.endswith('located 3 of 4 structures; 2 drawings refused: overview: Cytochrome b6f is not drawn: '
                           'no element has data-structure="Cytochrome b6f" (×2); left out Cytochrome b6f on overview')
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert [s.name for s in locations.scene("overview").structures] == ["Thylakoid"]
    assert locations_problems(locations, sample_media.bundle()) == []
    assert not (job / media.mask_path("overview", "Cytochrome b6f")).exists()


def test_a_scene_with_too_little_located_fails_the_step_in_one_line(job):
    artist = FakeArtist({"synthase": [svg_text(blob("ATP synthase", "#FF0000"))]})
    with pytest.raises(media.StepFailed) as failed:
        art.run(job, artist)
    message = str(failed.value)
    assert message.startswith("scene synthase: no usable drawing in 3 tries: ATP synthase is not drawn in its colour")
    assert "\n" not in message
    assert not (job / media.LOCATIONS).exists()

    shutil.rmtree(job / "art")  # else the hook drawn above is kept
    artist = FakeArtist({"hook": ["<svg>not closed"]})
    with pytest.raises(media.StepFailed, match="scene hook: .*not well-formed XML"):
        art.run(job, artist)


def test_a_structure_covered_by_what_is_drawn_after_it_is_left_out(job):
    covered = svg_text(blob("ATP synthase", "#3366CC", r=60), '<rect x="700" y="300" width="400" height="400" fill="#DDDDDD"/>')
    artist = FakeArtist({"synthase": [covered]})
    with pytest.raises(media.StepFailed, match="ATP synthase shows only 0 px"):
        art.run(job, artist)


def test_a_stopped_run_keeps_the_scenes_it_drew_and_a_new_run_redraws_them(job):
    with pytest.raises(media.StepFailed):
        art.run(job, FakeArtist({"synthase": ["no svg here"]}))
    artist = FakeArtist()
    art.run(job, artist)
    assert artist.labels() == ["art synthase"]  # hook and overview were kept

    artist = FakeArtist()
    art.run(job, artist)  # done: running it again redraws everything
    assert artist.labels() == ["art hook", "art overview", "art synthase"]

    # A saved drawing of a scene that has changed since is not reused.
    (job / media.LOCATIONS).unlink()
    data = sample_media.bundle().model_dump()
    data["scenes"][0]["image_prompt"] = "A chloroplast, cut open."
    jobs.write(job / "bundle.json", data)
    artist = FakeArtist()
    art.run(job, artist)
    assert artist.labels() == ["art hook"]


def test_locate_without_locations_says_what_to_do(job):
    assert not locate.is_done(job)
    with pytest.raises(media.StepFailed, match="run the art step first"):
        locate.run(job)


def test_the_media_steps_are_found():
    assert media.step_module("art") is art and media.step_module("locate") is locate


# --- checking a drawing -------------------------------------------------------------------

THYLAKOID = Structure(name="Thylakoid", aliases=[], sam_phrase="the thylakoid", color_hex="#2E8B57")
B6F = Structure(name="Cytochrome b6f", aliases=[], sam_phrase="cytochrome b6f", color_hex="#E07A2F")


def problems_of(text: str, structures=(THYLAKOID,)) -> list[str]:
    return svg.check(text, list(structures)).all()


@pytest.mark.parametrize("extra, refused", [
    ('<text x="1" y="1">ATP</text>', "<text>"),
    ('<image href="#a" width="1" height="1"/>', "<image>"),
    ("<script>alert(1)</script>", "<script>"),
    ("<foreignObject/>", "<foreignObject>"),
    ("<style>.a{fill:red}</style>", "<style>"),
    ('<use href="#t"/>', "<use>"),
    ('<rect width="9" height="9" fill="url(https://example.com/x.svg#g)"/>', "an external reference"),
    ('<linearGradient id="g" href="https://example.com/g.svg"/>', "an external link"),
    ('<rect width="9" height="9" onclick="go()"/>', "the onclick attribute"),
    ('<x:thing xmlns:x="http://example.com/x"/>', "<{http://example.com/x}thing>"),
])
def test_text_images_scripts_and_external_references_are_refused(extra, refused):
    checked = svg.check(svg_text(blob("Thylakoid", "#2E8B57"), extra), [THYLAKOID])
    assert len(checked.problems) == 1 and refused in checked.problems[0]


def test_a_drawing_must_be_safe_xml_on_the_right_canvas():
    entity = '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]>' + svg_text(blob("Thylakoid", "#2E8B57"))
    assert problems_of(entity) == ["the SVG must not have a DOCTYPE or entities"]
    assert "not well-formed XML" in problems_of("<svg><g></svg>")[0]
    assert "the root must be" in problems_of('<html xmlns="http://www.w3.org/2000/svg"/>')[0]
    assert "viewBox" in problems_of(svg_text(blob("Thylakoid", "#2E8B57"), viewbox="0 0 800 600"))[0]
    assert problems_of(svg_text(blob("Thylakoid", "#2E8B57"), viewbox="0,0,1792,1024")) == []


def test_every_structure_is_one_group_in_its_colour():
    both = [THYLAKOID, B6F]
    checked = svg.check(svg_text(blob("Thylakoid", "#2e8b57"), blob("Thylakoid", "#2E8B57"), blob("Mystery", "#123456")),
                        both)
    assert not checked.problems
    assert "marked 2 times" in checked.structures["Thylakoid"]
    assert "not drawn" in checked.structures["Cytochrome b6f"]
    assert checked.notes == ["Mystery is marked but not in this scene's list; mark only Cytochrome b6f, Thylakoid"]
    # Its colour may be a stop of its gradient, found through the gradient's href too.
    gradient = ('<defs><linearGradient id="base"><stop offset="0" style="stop-color:#2E8B57"/></linearGradient>'
                '<linearGradient id="shade" href="#base"/></defs>')
    shaded = '<g data-structure="Thylakoid"><rect width="99" height="99" fill="url(#shade)"/></g>'
    assert problems_of(svg_text(gradient, shaded)) == []
    assert "not drawn in its colour #2E8B57" in problems_of(svg_text(blob("Thylakoid", "#2E8B58")))[0]


def test_the_answer_is_cut_out_of_any_talk_around_it():
    assert svg.extract("Sure!\n```svg\n<svg a='1'>x</svg>\n```\nDone.") == "<svg a='1'>x</svg>"


# --- drawing and finding -------------------------------------------------------------------


def test_skia_draws_gradients_on_ellipses_and_filters_on_groups():
    # skia gets an ellipse's bounding box wrong: without svg._prepared both come out black or empty.
    text = svg_text(
        '<defs><radialGradient id="r"><stop offset="0" stop-color="#3366CC"/><stop offset="1" stop-color="#3366CC"/>'
        '</radialGradient><filter id="f"><feGaussianBlur stdDeviation="4"/></filter></defs>',
        '<ellipse cx="300" cy="300" rx="120" ry="80" fill="url(#r)"/>',
        '<g filter="url(#f)"><ellipse cx="900" cy="300" rx="120" ry="80" fill="#000000"/></g>')
    pixels = svg.rasterise(svg.check(text, []).root)
    assert tuple(pixels[300, 300]) == (0x33, 0x66, 0xCC)
    assert pixels[300, 900].max() < 40


def test_see_through_context_does_not_hide_a_structure_but_solid_context_does():
    glow = '<rect x="700" y="400" width="400" height="300" fill="#FFFF00" opacity="0.3"/>'
    solid = '<rect x="896" y="300" width="400" height="500" style="fill:#EEEEEE"/>'
    root = svg.check(svg_text(blob("Thylakoid", "#2E8B57"), glow, solid), [THYLAKOID]).root
    mask = svg.masks(root, ["Thylakoid"])["Thylakoid"]
    assert mask[512, 850] and not mask[512, 950]
    assert svg.locate(mask).box.x1 == 896


def test_a_shadow_inside_a_structures_group_is_not_part_of_it():
    # The first live run: the hippocampus, a thin arch, carried its blurred shadow in its own
    # group; the shadow was the widest part of the mask, so the label pointed at the shadow.
    shadow = '<ellipse cx="896" cy="760" rx="300" ry="90" fill="#000000" opacity="0.18" filter="url(#s)"/>'
    arch = '<path d="M 700 500 C 760 300 1030 300 1090 500" fill="none" stroke="#17A589" stroke-width="44"/>'
    text = svg_text('<defs><filter id="s"><feGaussianBlur stdDeviation="10"/></filter></defs>',
                    f'<g data-structure="Thylakoid">{shadow}{arch}</g>')
    mask = svg.masks(svg.check(text, [THYLAKOID]).root, ["Thylakoid"])["Thylakoid"]
    assert not mask[760, 896] and mask[350, 896]
    found = svg.locate(mask)
    assert found.box.y1 < 520
    assert mask[int(found.label_anchor.y), int(found.label_anchor.x)]

    # Drawn see-through on purpose, without a blur, it is the structure: its fill counts, not just its outline.
    faint = ('<g data-structure="Thylakoid"><circle cx="896" cy="512" r="120" fill="#2E8B57" fill-opacity="0.4" '
             'stroke="#1B5E3A" stroke-width="4"/></g>')
    assert svg.masks(svg.check(svg_text(faint), [THYLAKOID]).root, ["Thylakoid"])["Thylakoid"][512, 896]
    # A drop shadow merges the shape back on top, crisp, so under one the fill still counts.
    drop = ('<defs><filter id="d"><feGaussianBlur in="SourceAlpha" stdDeviation="6"/><feOffset dx="4" dy="6"/>'
            '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    shadowed = svg_text(drop, faint.replace('<g data-structure="Thylakoid">', '<g data-structure="Thylakoid" filter="url(#d)">'))
    assert svg.masks(svg.check(shadowed, [THYLAKOID]).root, ["Thylakoid"])["Thylakoid"][512, 896]
    # A structure that is nothing but a soft glow still has a mask.
    glow = svg_text('<defs><filter id="s"><feGaussianBlur stdDeviation="10"/></filter></defs>',
                    '<g data-structure="Thylakoid" filter="url(#s)" opacity="0.3">'
                    '<circle cx="896" cy="512" r="120" fill="#2E8B57"/></g>')
    assert svg.masks(svg.check(glow, [THYLAKOID]).root, ["Thylakoid"])["Thylakoid"][512, 896]


def test_a_label_anchor_is_deep_inside_even_when_the_centroid_is_not():
    yy, xx = np.mgrid[0:media.ART_HEIGHT, 0:media.ART_WIDTH]
    distance = np.hypot(xx - 896, yy - 512)
    ring = (distance > 200) & (distance < 260)
    found = svg.locate(ring)
    assert not ring[int(found.centroid.y), int(found.centroid.x)]
    ax, ay = found.label_anchor.x, found.label_anchor.y
    assert ring[int(ay), int(ax)] and 215 < np.hypot(ax - 896, ay - 512) < 245
    assert svg.locate(np.zeros_like(ring)) is None


# --- review fixes: staleness, cut-off answers, lenient names and colours -------------------


def test_art_drawn_for_an_earlier_bundle_is_not_done(job):
    art.run(job, FakeArtist())
    assert art.is_done(job) and locate.is_done(job)
    data = sample_media.bundle().model_dump()
    data["scenes"][2]["structures"][0]["color_hex"] = "#AA0000"  # same scene ids, a new colour
    jobs.write(job / "bundle.json", data)
    assert not art.is_done(job) and not locate.is_done(job)
    data["scenes"][2]["structures"][0]["color_hex"] = "#3366CC"
    data["topic"] = "the Calvin cycle"
    jobs.write(job / "bundle.json", data)
    assert not art.is_done(job)


def test_an_answer_cut_off_is_a_refused_try_and_other_brain_errors_fail_the_step(job):
    from studio import brain

    class CutOffOnce(FakeArtist):
        def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
            if label == "art hook":
                self.prompts.append((label, prompt))
                raise brain.Truncated("hit max_tokens")
            return super().__call__(role, prompt, schema, images, system=system, label=label)

    artist = CutOffOnce()
    art.run(job, artist)
    assert artist.labels()[:2] == ["art hook", "art hook try 2"]
    assert "cut off" in artist.prompts[1][1] and "<last_drawing>" not in artist.prompts[1][1]

    def refuses(*args, **kwargs):
        raise brain.Refused("model")

    with pytest.raises(brain.Refused):
        art.run(job, refuses)


def test_names_in_another_case_and_rgb_colours_count():
    loose = '<g data-structure="  thylakoid "><rect width="300" height="300" fill="rgb(46, 139, 87)"/></g>'
    checked = svg.check(svg_text(loose), [THYLAKOID])
    assert checked.all() == []
    mask = svg.masks(checked.root, ["Thylakoid"])["Thylakoid"]
    assert mask[100, 100] and svg.locate(mask) is not None
    assert problems_of(svg_text(blob("Thylakoid", "#2E8B57CC"))) == []


def test_runaway_and_deeply_nested_drawings_are_refused():
    huge = svg_text(blob("Thylakoid", "#2E8B57"), "<g>" + '<rect width="1" height="1"/>' * 20000 + "</g>")
    assert "keep it under 40 KB" in problems_of(huge)[0]
    deep = svg_text("<g>" * 3000 + blob("Thylakoid", "#2E8B57") + "</g>" * 3000)
    scene = sample_media.bundle().scenes[0]
    drawing = art.judge(deep, scene)
    assert not drawing.usable and "nests groups too deeply" in drawing.problems[0]


def test_the_sample_timelines_fit_the_locations_the_art_step_writes(job):
    from studio.schemas import Timeline, timeline_problems

    art.run(job, FakeArtist())
    locations = jobs.read(job / media.LOCATIONS, Locations)
    for video in media.VIDEOS:
        assert timeline_problems(jobs.read(job / media.timeline_path(video), Timeline), locations) == []


def test_model_written_names_stay_inside_the_job_and_apart(job):
    from studio.schemas import Scene

    names = ["α subunit", "β subunit"]
    scene = Scene(id="../ATP synthase", image_prompt="x", structures=[
        Structure(name=n, aliases=[], sam_phrase=n, color_hex=c) for n, c in zip(names, ["#3366CC", "#CC6633"])])
    answer = svg_text(blob(names[0], "#3366CC", x=600), blob(names[1], "#CC6633", x=1200))
    (job / "art").mkdir(exist_ok=True)
    drawing = art.draw(job, sample_media.bundle(), scene, lambda *a, **k: {"text": answer}, resume=False)
    located = art.save(job, scene, drawing)  # validates every path against the API's upload rule
    masks = [s.mask for s in located.structures]
    assert len(set(masks)) == 2
    for path in [located.art, *masks]:
        assert (job / path).resolve().is_relative_to(job.resolve()) and (job / path).exists()
    assert list((job / "art").glob("*.svg")) == [job / "art" / f"{media.file_name(scene.id)}.svg"]


def test_the_illustrator_is_told_who_the_video_is_for():
    bundle = Bundle.model_validate({**bundle_data(), "audience": "about age 10"})
    assert "\nAudience: about age 10\n" in art.user_prompt(bundle, bundle.scenes[0])


def test_a_graph_is_asked_for_with_plain_axes_outside_every_structure():
    bundle = sample_media.graph_bundle()
    hook, graph = bundle.scenes[:2]
    # Painted, it labels its own axes, as it labels its parts; drawn, the video writes their names.
    axes = 'Its axes are two plain, thin lines, the horizontal one labelled "Quantity" and the vertical one labelled "Price".'
    assert axes in art.paint_prompt(bundle, graph) and "Its axes" not in art.paint_prompt(bundle, hook)
    assert art.paint_prompt(bundle, graph).endswith(axes + " " + art.labels_line(graph))
    assert art.axes_line(Axes(x="Time", y="")) == 'Its axes are two plain, thin lines, the horizontal one labelled "Time".'
    asked = art.user_prompt(bundle, graph)
    assert ('Axes: this scene is a graph. Draw its two axes as two plain <line>s in one <g data-role="axes">, outside '
            "every structure's group, with nothing written on or beside them: the video writes \"Quantity\" at the right "
            'end of the horizontal one and "Price" at the top of the vertical one. Draw the rest as "Graphs" in your '
            "instructions says.") in asked
    assert "Axes:" not in art.user_prompt(bundle, hook)
    # The helper checking the painting is told the axes were asked for, so it never counts them as stray lines.
    assert f"{axes} They are asked for" in art.check_prompt(bundle, graph)
    assert "Its axes" not in art.check_prompt(bundle, hook)


# --- the house look ----------------------------------------------------------------------------

CURVE = Structure(name="Curve", aliases=[], sam_phrase="blue curve", color_hex="#4FC3F7")
CORNER = Structure(name="Corner", aliases=[], sam_phrase="red dot", color_hex="#FF4D4D")
AXES_SVG = ('<g data-role="axes"><line x1="380" y1="880" x2="1470" y2="880" stroke="#5F6B73" stroke-width="5"/>'
            '<line x1="380" y1="880" x2="380" y2="120" style="stroke:#5F6B73;stroke-width:5"/></g>')


def a_graph(*extra: str) -> str:
    """A graph as the brain drew one on Oct 30, with its own shading: a page, a shadow, a curve as an outline under a
    gradient under a highlight, a point on it, and a marked corner shaded by a gradient."""
    return svg_text(
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#4FC3F7"/></linearGradient>'
        '<radialGradient id="r"><stop offset="0" stop-color="#FF4D4D"/></radialGradient>'
        '<filter id="s"><feGaussianBlur stdDeviation="4"/></filter></defs>',
        '<rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>', AXES_SVG,
        '<path d="M420 828 Q920 748 1420 168" fill="none" stroke="#000000" stroke-opacity="0.18" stroke-width="14" '
        'filter="url(#s)"/>',
        '<g data-structure="Curve">'
        '<path d="M420 820 Q920 740 1420 160" fill="none" stroke="#0277BD" stroke-width="16"/>'
        '<path d="M420 820 Q920 740 1420 160" fill="none" stroke="url(#g)" stroke-width="10"/>'
        '<path d="M420 818 Q920 738 1420 158" fill="none" stroke="#E1F5FE" stroke-opacity="0.6" stroke-width="2.5"/>'
        '<circle data-role="point" cx="770" cy="702.75" r="12" fill="url(#g)" stroke="#0277BD"/></g>',
        '<g data-structure="Corner"><circle cx="1100" cy="300" r="30" fill="url(#r)" stroke="#A51C1C"/>'
        '<ellipse cx="1090" cy="290" rx="10" ry="7" fill="#FFFFFF" opacity="0.45"/></g>', *extra)


def styled(text: str, graph: bool = True):
    return svg.style(svg.check(text, [CURVE, CORNER]).root, [CURVE, CORNER], graph)


def shapes(root, structure: str | None = None):
    groups = [g for g in root.iter() if g.get(svg.MARK) == structure] if structure else [root]
    return [el for g in groups for el in g.iter() if svg._local(el.tag) in svg.SHAPES]


def test_a_graph_is_drawn_in_the_house_look():
    root = styled(a_graph())
    colours = lambda els, key: {el.get(key) for el in els}
    curve = shapes(root, "Curve")
    line = [el for el in curve if el.get(svg.ROLE) != "point"]
    # The brain's outline, gradient and highlight are one flat line now, in its colour made readable on the paper,
    # at the house weight.
    blue = svg.readable("#4FC3F7")
    assert len(line) == 2 and colours(line, "stroke") == {blue} and colours(line, "stroke-width") == {"8"}
    point = [el for el in curve if el.get(svg.ROLE) == "point"][0]
    assert (point.get("fill"), point.get("stroke")) == (svg.PAGE, blue)  # a ring on the page
    [corner] = shapes(root, "Corner")  # its highlight is gone
    # Its outline a shade darker, so blocks of one structure stacked on one another stay apart.
    red = svg.readable("#FF4D4D")
    assert corner.get("fill") == red and corner.get("stroke") == svg._mix(red, svg.EDGE_SHADE, svg.PAGE)
    # The page and the shadow are gone.
    assert not [el for el in root.iter() if el.get("width") == "100%"]
    assert not [el for el in shapes(root) if el.get("stroke") == "#000000"]
    # The axes: dark, thin, an arrowhead at the right end and the top; no grid, and no glow or filter of any kind.
    axes = [g for g in root.iter() if g.get(svg.ROLE) == "axes"][0]
    assert colours(axes.findall(f"{{{svg.NS}}}line"), "stroke") == {svg.AXIS_INK}
    tips = [el.get("points") for el in axes.findall(f"{{{svg.NS}}}polygon")]
    assert tips[0].startswith("1486,880 ") and tips[1].startswith("380,104 ")
    assert [el for el in root if svg._local(el.tag) != "defs"] == [axes, *(g for g in root if g.get(svg.MARK))]
    assert not [el for el in root.iter() if el.get("filter")]


def test_a_graphs_colours_are_darkened_until_they_read_on_the_paper():
    # The bundle's colours were picked to stand out on a dark page: a yellow line on paper all but vanished.
    for colour in ("#FFD23F", "#4FC3F7", "#7CFC00", "#FF4D4D", "#B388FF"):
        made = svg.readable(colour)
        assert svg.contrast(made, svg.PAGE) >= svg.CONTRAST_MIN, colour
        hue = lambda c: colorsys.rgb_to_hls(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]
        assert abs(hue(made) - hue(colour)) < 0.02, colour  # the same hue, only darker
    assert svg.readable("#1565C0") == "#1565C0"  # a colour that reads already is kept
    assert svg.contrast(svg.AXIS_INK, svg.PAGE) >= 4.5


def test_a_shadow_in_a_structures_own_group_is_not_the_structure():
    shaded = svg_text('<defs><filter id="s"><feGaussianBlur stdDeviation="4"/></filter></defs>',
                      '<g data-structure="Curve"><ellipse cx="896" cy="760" rx="300" ry="90" fill="#000000" '
                      'opacity="0.18" filter="url(#s)"/><path d="M700 500 L1090 500" fill="none" stroke="#4FC3F7" '
                      'stroke-width="44"/></g>')
    mask = svg.masks(svg.style(svg.check(shaded, [CURVE]).root, [CURVE], graph=False), ["Curve"])["Curve"]
    assert not mask[760, 896] and mask[500, 896]


def test_what_a_line_means_is_kept():
    # A dashed line, an earlier secant drawn faded, a band drawn as one wide stroke and a curve cut by a mask keep
    # their dash, their fading, their width and their mask; the paint inside the mask is left alone.
    root = styled(svg_text(
        '<defs><mask id="m"><rect x="0" y="0" width="900" height="1024" fill="#FFFFFF"/></mask></defs>', AXES_SVG,
        '<g data-structure="Curve">'
        '<path d="M420 820 L1400 200" fill="none" stroke="#4FC3F7" stroke-width="16" stroke-dasharray="20 12"/>'
        '<path d="M420 820 L1400 400" fill="none" stroke="#4FC3F7" stroke-width="16" opacity="0.4"/>'
        '<path d="M420 600 L1400 600" fill="none" stroke="#4FC3F7" stroke-width="60"/>'
        '<path d="M420 700 Q900 500 1400 300" fill="none" stroke="url(#g)" stroke-width="12" mask="url(#m)"/></g>'))
    dashed, faded, band, cut = shapes(root, "Curve")
    assert dashed.get("stroke-dasharray") == "20 12" and dashed.get("stroke-width") == "8"
    assert faded.get("opacity") == "0.4"
    assert band.get("stroke-width") == "60" and band.get("stroke") == svg.readable("#4FC3F7")
    assert cut.get("mask") == "url(#m)" and cut.get("stroke") == svg.readable("#4FC3F7")
    assert root.find(f".//{{{svg.NS}}}mask/{{{svg.NS}}}rect").get("fill") == "#FFFFFF"


def test_axes_drawn_as_a_path_get_arrowheads_and_ticks_do_not():
    axes = ('<g data-role="axes"><path d="M380 120 V880 H1470" fill="none" stroke="#5F6B73" stroke-width="5"/>'
            '<line x1="452" y1="872" x2="452" y2="888" stroke="#5F6B73"/>'  # a tick, against the prompt
            '<polyline points="300,880 380,880" stroke="#5F6B73"/></g>')
    root = styled(svg_text(axes, '<g data-structure="Curve"><path d="M420 820 L1400 200" fill="none" '
                                 'stroke="#4FC3F7"/></g>'))
    tips = [el.get("points") for el in root.iter(f"{{{svg.NS}}}polygon")]
    assert len(tips) == 2 and tips[0].startswith("1486,880 ") and tips[1].startswith("380,104 ")
    assert svg._segments(ET.fromstring('<path d="m10 10 h100 v50 z"/>')) == [(10, 10, 110, 10), (110, 10, 110, 60),
                                                                            (110, 60, 10, 10)]
    assert svg._segments(ET.fromstring('<path d="M10 10 C20 20 30 30 40 40"/>')) == []  # a curve is no axis


def test_the_renderer_finds_a_styled_graphs_axes():
    from studio.render import axes as axes_finder
    root = styled(a_graph())
    pixels = svg.rasterise(root)
    covered = np.logical_or.reduce(list(svg.masks(root, ["Curve", "Corner"]).values()))
    across, up = axes_finder.find(axes_finder.distance(pixels, PAGE), covered)
    assert abs(across.at - 880) <= 3 and abs(up.at - 380) <= 3
    assert across.end - across.start > 1000 and up.end - up.start > 700


def test_a_scene_that_is_no_graph_keeps_its_drawing_on_the_house_page():
    text = a_graph()
    root = styled(text, graph=False)
    assert not [el for el in root.iter() if el.get("width") == "100%"]  # only its page is gone
    kept = svg.check(text, [CURVE, CORNER]).root
    page = [el for el in kept.iter() if el.get("width") == "100%"][0]
    [parent] = [el for el in kept.iter() if page in list(el)]
    parent.remove(page)
    assert svg.to_text(root) == svg.to_text(kept)
    assert tuple(svg.rasterise(root)[5, 5]) == PAGE


def test_paint_set_on_a_group_is_its_lines_paint():
    root = styled(svg_text('<g data-structure="Curve" fill="none" stroke="url(#g)">'
                           '<path d="M420 820 Q920 740 1420 160"/></g>'))
    path = shapes(root, "Curve")[-1]
    assert (path.get("fill"), path.get("stroke"), path.get("stroke-width")) == ("none", svg.readable("#4FC3F7"), "8")


def test_a_new_look_styles_the_kept_drawings_again_without_the_brain(job, monkeypatch):
    art.run(job, FakeArtist())
    assert art.is_done(job)
    monkeypatch.setattr(svg, "LOOK", "next")
    assert not art.is_done(job)

    def no_brain(*args, **kwargs):
        raise AssertionError("a new look needs no new drawing")

    assert art.run(job, no_brain) == "drew 3 scenes, located 4 of 4 structures"
    assert {s.look for s in jobs.read(job / media.LOCATIONS, Locations).scenes} == {"next"}
    assert art.is_done(job)


def test_the_illustrator_is_told_the_page_it_draws_on():
    assert svg.PAGE in art.PROMPT and 'data-role="axes"' in art.PROMPT and 'data-role="point"' in art.PROMPT


# What the house look draws with, in studio/svg.py: its functions (as code: docstrings and comments left out) and its
# numbers. A finished job keeps its art until the look id it was styled with (svg.LOOK) changes, so a change here that
# changes what is drawn must come with a new LOOK, or finished jobs keep the old look. A change that draws the same (a
# rename, a refactor) pins the new fingerprint and keeps LOOK.
LOOK_CODE = ("style", "_style_graph", "_style_axes", "_style_structure", "readable", "contrast", "_luminance", "_segments",
             "_prop", "_inherited", "_repaint", "_drawn", "_owner", "_moved", "_shading", "_covers_page", "_number",
             "_mix", "rasterise", "masks", "_painted", "_prepared", "_soft_filters", "without_points", "locate",
             "_deepest")
LOOK_NUMBERS = ("PAGE", "LINE_WIDTH", "BAND", "AXIS_WIDTH", "AXIS_SHARE", "POINT_WIDTH", "ARROW", "CONTRAST_MIN",
                "REPAINTED", "AXIS_INK", "ROLE", "NAMED", "EDGE_SHADE")
LOOK_PINNED = ("4", "10c235ccbe46")  # 4: a textbook page: paper, dark axes, readable colours, no glow or grid


def _as_code(function) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, annotate_fields=False, include_attributes=False)


def test_the_look_cannot_change_without_a_new_look_id():
    parts = [_as_code(getattr(svg, name)) for name in LOOK_CODE] + [repr(getattr(svg, name)) for name in LOOK_NUMBERS]
    fingerprint = hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]
    assert (svg.LOOK, fingerprint) == LOOK_PINNED, (
        f"the house look's code or numbers changed (now {fingerprint}): if it draws differently, bump svg.LOOK so "
        "finished jobs are styled again; either way, pin the new LOOK and fingerprint in LOOK_PINNED")


def test_a_lines_label_is_anchored_on_the_line_not_on_a_point_marked_on_it():
    # A point is wider than its line (its ring and glow), so the deepest place in the secant's mask was a point, and
    # the pill drawn beside the anchor hid the very point the song sings about. The point stays in the mask.
    secant = Structure(name="Secant line", aliases=[], sam_phrase="orange line", color_hex="#FF9500")
    text = svg_text(AXES_SVG, '<g data-structure="Secant line">'
                              '<line x1="570" y1="838" x2="1350" y2="245" stroke="#FF9500" stroke-width="6"/>'
                              '<circle data-role="point" cx="650" cy="777" r="12" stroke="#FF9500"/>'
                              '<circle data-role="point" cx="1250" cy="321" r="12" stroke="#FF9500"/></g>')
    job_scene = sample_media.graph_bundle().scenes[1].model_copy(update={"structures": [secant]})
    drawing = art.judge(text, job_scene)
    found, mask = drawing.found["Secant line"], drawing.masks["Secant line"]
    anchor = (found.label_anchor.x, found.label_anchor.y)
    assert all(math.dist(anchor, point) > 25 for point in ((650, 777), (1250, 321)))
    assert mask[777, 650] and mask[321, 1250] and mask[int(anchor[1]), int(anchor[0])]
    # A structure that is only a point keeps its anchor on it.
    dot = svg_text(AXES_SVG, '<g data-structure="Secant line"><circle data-role="point" cx="900" cy="500" r="30" '
                             'stroke="#FF9500"/></g>')
    alone = art.judge(dot, job_scene).found["Secant line"]
    assert math.dist((alone.label_anchor.x, alone.label_anchor.y), (900, 500)) < 30


# --- values a chart's drawing marks for the video to write ---------------------------------------------------------


def test_a_drawing_marks_its_values_for_the_video_and_draws_none_of_them():
    text = svg_text(blob("Interest", "#FFC845", r=80),
                    '<circle data-value="$1,210" data-side="above" cx="896" cy="420" r="0"/>',
                    '<circle data-tick="Year 2" data-side="below" cx="896" cy="900" r="0"/>',
                    '<circle data-value="$9" data-side="sideways" cx="300" cy="300" r="0"/>',  # a side it lacks: above
                    '<g><circle data-value="$5" cx="10" cy="10" r="0"/></g>',  # in a group, which may move it
                    '<circle data-value="$7" cx="5000" cy="10" r="0"/>')  # off the canvas
    root = svg.check(text, [Structure(name="Interest", aliases=[], sam_phrase="gold block", color_hex="#FFC845")]).root
    found = svg.values(root)
    assert [(v.text, v.at.x, v.at.y, v.side, v.tick) for v in found] == [
        ("$1,210", 896, 420, "above", False), ("Year 2", 896, 900, "below", True), ("$9", 300, 300, "above", False)]
    styled = svg.style(root, [], graph=False)
    assert not [el for el in styled.iter() if el.get(svg.VALUE) or el.get(svg.TICK)]


def test_a_drawn_scenes_values_are_kept_with_its_locations(job):
    marks = '<circle data-value="$1,331" cx="896" cy="300" r="0"/><circle data-tick="Year 3" data-side="below" ' \
            'cx="896" cy="900" r="0"/>'
    overview = drawing("overview").replace("</svg>", marks + "</svg>")
    art.run(job, FakeArtist({"overview": [overview]}))
    located = jobs.read(job / media.LOCATIONS, Locations)
    assert [(v.text, v.tick) for v in located.scene("overview").values] == [("$1,331", False), ("Year 3", True)]
    assert located.scene("hook").values == []


def test_a_graph_marks_where_each_structures_name_is_written():
    # As a textbook prints a curve's name beside it: the video writes it there, in the structure's colour, no pill.
    text = svg_text(AXES_SVG, '<g data-structure="Curve"><path d="M420 820 L1400 200" fill="none" stroke="#4FC3F7"/></g>',
                    '<circle data-value="$5" cx="900" cy="500" r="0"/>',
                    '<circle data-label="curve" data-side="right" cx="1420" cy="200" r="0"/>',  # its case need not match
                    '<circle data-label="Curve" cx="300" cy="300" r="0"/>',  # a second for it: the first is kept
                    '<circle data-label="Demand" cx="600" cy="300" r="0"/>',  # no structure of the scene
                    '<circle data-label="Corner" cx="9000" cy="300" r="0"/>')  # off the canvas
    root = svg.check(text, [CURVE, CORNER]).root
    found = svg.values(root, ["Curve", "Corner"])
    assert [(v.text, v.at.x, v.at.y, v.side, v.tick, v.label) for v in found] == [
        ("$5", 900, 500, "above", False, False), ("Curve", 1420, 200, "right", False, True)]
    assert [v.text for v in svg.values(root)] == ["$5"]  # a name not asked for is no value either
    assert not [el for el in styled(text).iter() if el.get(svg.LABEL) is not None]  # drawn nowhere


def test_a_drawn_scenes_names_are_kept_with_its_locations(job):
    marks = ('<circle data-value="$1,331" cx="896" cy="300" r="0"/>'
             '<circle data-label="thylakoid" data-side="left" cx="400" cy="500" r="0"/>')
    art.run(job, FakeArtist({"overview": [drawing("overview").replace("</svg>", marks + "</svg>")]}))
    located = jobs.read(job / media.LOCATIONS, Locations)
    assert [(v.text, v.side, v.label) for v in located.scene("overview").values] == [
        ("$1,331", "above", False), ("Thylakoid", "left", True)]


def test_a_value_mark_anywhere_else_is_ignored_and_one_without_a_point_is_left_out():
    bar = '<g data-structure="Interest"><rect data-value="$100" x="800" y="300" width="200" height="400" ' \
          'fill="#FFC845"/></g>'
    text = svg_text(bar, '<circle data-value="$999" r="0"/>', '<circle data-tick="Year 2" cx="50%" cy="900" r="0"/>')
    text = text.replace("<svg ", '<svg data-value="$1" ', 1)
    root = svg.check(text, [Structure(name="Interest", aliases=[], sam_phrase="gold bar", color_hex="#FFC845")]).root
    assert svg.values(root) == []  # neither has a point of its own; nothing is written at the corner
    styled = svg.style(root, [], graph=False)
    assert not [el for el in styled.iter() if el.get(svg.VALUE) or el.get(svg.TICK)]
    assert svg.masks(styled, ["Interest"])["Interest"].sum() > 50_000  # the bar marked by mistake is still drawn


def test_a_song_picture_drawn_to_scale_is_given_the_lines_sung_over_it():
    data = bundle_data()
    data["song_scenes"] = [{"id": "chorus", "image_prompt": "A bar chart.", "to_scale": True, "structures": [
        {"name": "ATP synthase", "aliases": [], "sam_phrase": "the atp synthase", "color_hex": "#3366CC"}]}]
    for d in data["direction"]:
        if d["line_id"].startswith("c"):
            d["scene"] = "chorus"
    bundle = Bundle.model_validate(data)
    assert art._said_over(bundle, bundle.scenes[-1]) == ["Protons flow and ATP grows"] * 3
    assert art._said_over(bundle, bundle.scenes[2]) == ["ATP synthase uses the returning protons to make ATP."]


def test_a_science_diagram_labels_its_nonobvious_structures_and_any_other_scene_only_its_parts():
    # Oct 31: "nonobvious" labelled a membrane's pump, channels, ATP and spaces, and a boat's oarlock and thwart.
    bundle = Bundle.model_validate(bundle_data())
    scene = bundle.scenes[1]
    names = ", ".join(s.name for s in scene.structures)
    science = scene.model_copy(update={"image_prompt": "Educational textbook illustration of a thylakoid membrane, "
                                                       "clean scientific style: the membrane across the middle."})
    everyday = scene.model_copy(update={"image_prompt": "Educational textbook illustration of a rowing boat, clean "
                                                        "textbook style: the boat in the centre."})
    assert art.labels_line(science) == art.LABELS == "Clearly label nonobvious structures. " + art.NO_SLIDE
    line = art.labels_line(everyday)
    assert line == (f"Label only these parts, each with exactly this name: {names}. Label nothing else: no room, "
                    f"furniture or background objects. {art.NO_SLIDE}")
    assert art.paint_prompt(bundle, everyday).endswith(line)


def test_the_hook_labels_nothing_whatever_its_style():
    # Nov 1: the pump's hook, a teenager on a couch, came back labelled "Body" and "Couch".
    bundle = Bundle.model_validate(bundle_data())
    hook = bundle.scenes[0]
    for prompt in ("A teenager on a couch, clean textbook style.", "A leaf in the sun, clean scientific style."):
        scene = hook.model_copy(update={"image_prompt": prompt})
        assert art.labels_line(scene) == art.NO_LABELS
        assert art.paint_prompt(bundle, scene).endswith("Colours: Thylakoid green. " + art.NO_LABELS)


def test_the_picture_draws_none_of_the_particles_the_video_moves_over_it():
    # Nov 1: the pump's painter added its own Na⁺ and K⁺ balls, brown and blue, beside the video's purple and green.
    data = bundle_data()
    data["scenes"][1]["structures"].append({"name": "Oxygen", "aliases": [], "sam_phrase": "white ball",
                                            "color_hex": "#E8EEF5"})
    moves = {"n1": ("H⁺", "O₂", "sugar"), "n2": ("H⁺",)}
    for d in data["direction"]:
        if d["line_id"] in moves:
            first, *also = moves[d["line_id"]]
            d.update(particles=["Thylakoid", "Cytochrome b6f"], carries=first,
                     also=[{"particles": ["Cytochrome b6f", "Thylakoid"], "carries": c} for c in also])
    bundle = Bundle.model_validate(data)
    line = "Draw no H⁺ particles anywhere: the video adds its own, moving."
    assert art.loose_line(bundle, bundle.scenes[1]) == line  # Oxygen is a part of it; sugar no common kind
    assert line in art.paint_prompt(bundle, bundle.scenes[1])
    assert art.loose_line(bundle, bundle.scenes[0]) == ""  # nothing moves on the hook
    assert [checks.is_kind(n, k) for n, k in (("Water molecules", "H₂O"), ("Carbon dioxide", "CO₂"),
                                              ("ATP synthase", "ATP"), ("Electron transport chain", "e⁻"))] == [
        True, True, False, False]


def test_the_picture_is_told_each_colour_in_one_everyday_word():
    # Nov 1: the writer's "brown ATP synthase" and "cyan water" were "orange" and "teal" in the Colours line.
    bundle = Bundle.model_validate(bundle_data())
    scene = bundle.scenes[1].model_copy(update={"structures": [
        Structure(name="ATP synthase", aliases=[], sam_phrase="brown mushroom", color_hex="#A0703C"),
        Structure(name="Water", aliases=[], sam_phrase="cyan ball", color_hex="#00ACC1"),
        Structure(name="Carbon dioxide", aliases=[], sam_phrase="teal ball", color_hex="#00897B")]})
    assert "Colours: ATP synthase brown, Water cyan, Carbon dioxide teal." in art.paint_prompt(bundle, scene)
