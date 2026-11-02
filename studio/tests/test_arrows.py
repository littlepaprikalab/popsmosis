"""The arrows step: each hop pointed on its painting by the helper, every arrow looked at as drawn (a fake ask)."""

import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from studio import jobs, media
from studio.schemas import Arrows, PlacedArrow, Timeline
from studio.steps import arrows
from studio.tests import sample_media

HOP = ["Thylakoid", "Cytochrome b6f"]  # the sample song's one flow, on the overview: a hop from a space into a part


@pytest.fixture
def job(tmp_path):
    job = sample_media.write_job(tmp_path / "sample", art=True)
    for video in media.VIDEOS:  # its overview painted: a picture that labels its own parts
        path = job / media.timeline_path(video)
        jobs.write(path, jobs.read(path, Timeline).model_copy(update={"self_labelled": ["overview"]}))
    return job


class FakeAsk:
    """The helper: points every hop at `start` -> `end` (pixels of the picture it is shown), or says it cannot be
    drawn; says each arrow it looks at shows the move, or not."""

    def __init__(self, drawable=True, shows=True, start=(400.0, 250.0), end=(470.0, 230.0), copies=()):
        self.drawable, self.shows, self.start, self.end, self.copies = drawable, shows, start, end, copies
        self.calls = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.calls.append({"role": role, "prompt": prompt, "schema": schema, "label": label, "system": system,
                           "sizes": [Image.open(p).size for p in images]})
        if schema is arrows.Pointing:
            (x0, y0), (x1, y1) = self.start, self.end
            copies = [{"start": {"x": a, "y": b}, "end": {"x": c, "y": d}} for (a, b), (c, d) in self.copies]
            return {"arrows": [{"id": "a1", "drawable": self.drawable, "start": {"x": x0, "y": y0},
                                "end": {"x": x1, "y": y1}, "copies": copies, "why": "from the space up into the part"}]}
        assert schema is arrows.Look
        return {"shows": self.shows, "why": "it runs from the thylakoid into b6f" if self.shows else "it runs sideways"}


def placed(job):
    return jobs.read(job / media.ARROWS, Arrows).arrows


def test_a_painted_hop_is_pointed_on_its_picture_and_its_grid_then_looked_at_drawn(job):
    ask = FakeAsk()
    line = arrows.run(job, ask=ask)
    point, look = ask.calls
    # One call for the painting: the picture and its grid, fitted into POINT_SIZE, and the hop with its line.
    assert point["role"] == "helper" and point["label"] == "arrows point overview"
    assert point["sizes"] == [(945, 540), (945, 540)]  # 1792 x 1024 fitted into 960 x 540
    assert "a1: something moves from “Thylakoid” to “Cytochrome b6f”" in point["prompt"]
    assert "945 x 540" in point["prompt"] and "Cytochrome b6f" in point["system"] + point["prompt"]
    # Its tail and head come back in the art's pixels, and the arrow is looked at in a still of it alone.
    [arrow] = placed(job)
    scale = 945 / 1792
    assert (arrow.scene, arrow.path, arrow.blocked) == ("overview", HOP, False)
    assert arrow.start.x == pytest.approx(400 / scale) and arrow.end.y == pytest.approx(230 / scale)
    assert look["label"].startswith("arrows look overview") and look["sizes"] == [arrows.LOOK_SIZE]
    assert "Does it show this: something moves from “Thylakoid” to “Cytochrome b6f”?" in look["prompt"]
    assert arrow.left_out == "" and arrow.seen == "it runs from the thylakoid into b6f"
    assert line.startswith("1 hop(s) pointed on 1 painting(s), 0 the picture cannot show; 1 arrow(s) looked at, 0")
    assert arrows.is_done(job)
    later = os.stat(job / media.ARROWS).st_mtime + 5
    os.utime(job / media.timeline_path("song"), (later, later))  # a timeline made since: placed again
    assert not arrows.is_done(job)


def test_a_hop_the_picture_cannot_show_is_left_out_and_not_looked_at(job):
    ask = FakeAsk(drawable=False)
    arrows.run(job, ask=ask)
    assert [c["schema"] for c in ask.calls] == [arrows.Pointing]
    [arrow] = placed(job)
    assert arrow.start is None and arrow.left_out == "the picture cannot show it: from the space up into the part"


def test_an_arrow_that_does_not_show_the_move_as_drawn_is_left_out(job):
    arrows.run(job, ask=FakeAsk(shows=False))
    [arrow] = placed(job)
    assert arrow.start is not None and arrow.left_out == "as drawn it does not show it: it runs sideways"


def test_a_drawing_has_no_hop_pointed_but_each_arrow_is_still_looked_at(job):
    for video in media.VIDEOS:
        path = job / media.timeline_path(video)
        jobs.write(path, jobs.read(path, Timeline).model_copy(update={"self_labelled": []}))
    ask = FakeAsk()
    arrows.run(job, ask=ask)
    assert [c["schema"] for c in ask.calls] == [arrows.Look]
    [arrow] = placed(job)
    assert arrow.start is None and arrow.left_out == ""  # code places it, as before


def test_the_grid_is_numbered_along_its_edges_every_second_line():
    picture = Image.new("RGB", (300, 200), "white")
    grid = arrows.gridded(picture)
    assert grid.size == picture.size and grid.getpixel((50, 125)) != (255, 255, 255)  # a line every GRID pixels
    assert grid.getpixel((75, 125)) == (255, 255, 255)


def test_a_move_from_a_place_drawn_more_than_once_gets_an_arrow_from_each_copy(job):
    # Nov 2: the GPS overview drew its four satellites as one part, and the video showed one signal reaching the phone
    # where the lesson needs four.
    ask = FakeAsk(copies=[((300.0, 100.0), (470.0, 230.0)), ((600.0, 100.0), (470.0, 230.0))])
    arrows.run(job, ask=ask)
    point, look = ask.calls
    assert "place one arrow for each copy" in point["system"]
    [arrow] = placed(job)
    scale = 945 / 1792
    assert [(c.start.x, c.end.y) for c in arrow.copies] == [(pytest.approx(300 / scale), pytest.approx(230 / scale)),
                                                            (pytest.approx(600 / scale), pytest.approx(230 / scale))]
    assert "It is drawn 3 times, once for each copy" in look["prompt"]


def test_a_move_with_copies_draws_an_arrow_from_each_copy():
    from studio.render.renderer import Renderer
    from studio.schemas import Locations, Point, Way

    job = sample_media.write_job(Path(tempfile.mkdtemp()) / "sample", art=True)
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    locations = jobs.read(job / media.LOCATIONS, Locations)
    one = PlacedArrow(scene="overview", path=HOP, carries="", start=Point(x=700, y=500), end=Point(x=900, y=450))
    many = one.model_copy(update={"copies": [Way(start=Point(x=300, y=200), end=Point(x=900, y=450))]})
    beat = next(b for b in timeline.beats if b.particles and list(b.particles[0].path) == HOP)
    flow = beat.particles[0]
    alone = Renderer(job, timeline, locations=locations, arrows=Arrows(arrows=[one.model_copy(update={
        "carries": flow.label})]))
    both = Renderer(job, timeline, locations=locations, arrows=Arrows(arrows=[many.model_copy(update={
        "carries": flow.label})]))
    assert alone._copies("overview", flow) == []
    assert both._copies("overview", flow) == [[(300.0, 200.0), (900.0, 450.0)]]
    key = arrows.key("overview", flow)
    frames = []
    for renderer in (alone, both):
        renderer.only = key
        frames.append(renderer.frame(renderer.arrow_time("overview", flow)).astype(int))
    changed = (abs(frames[1] - frames[0]).sum(axis=2) > 30).sum()
    assert changed > 200  # the second arrow is drawn: a few hundred pixels or more differ
