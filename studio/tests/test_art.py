"""The art and locate steps, with a stand-in brain that answers with the SVGs in studio/tests/art/."""

import shutil
from pathlib import Path

import numpy as np
import pytest
import skia

from studio import jobs, media, svg
from studio.schemas import Bundle, Locations, Structure, locations_problems
from studio.steps import art, locate
from studio.tests import sample_media
from studio.tests.sample import bundle_data

DRAWINGS = Path(__file__).resolve().parent / "art"


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
        assert (pixels[0, 0, :3] == 255).all() and (pixels[-1, -1, :3] == 255).all()  # white around it
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
    assert art.AXES in art.paint_prompt(bundle, graph) and art.AXES not in art.paint_prompt(bundle, hook)
    asked = art.user_prompt(bundle, graph)
    assert ("Axes: draw the graph's two axes as plain, thin dark lines outside every group, with nothing written on or "
            'beside them: the video writes "Quantity" at the right end of the horizontal one and "Price" at the top of '
            "the vertical one.") in asked
    assert "Axes:" not in art.user_prompt(bundle, hook)
    # The helper checking the painting is told the axes were asked for, so it never counts them as stray lines.
    assert f"{art.AXES} They are asked for" in art.check_prompt(bundle, graph)
    assert art.AXES not in art.check_prompt(bundle, hook)
