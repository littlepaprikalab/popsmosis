"""The renderer on the sample job: a few frames, and a two-second video."""

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

skia = pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")

from studio import jobs, media
from studio.render import primitives as p
from studio.render.encode import EncodeError, audio_filter, encode, encode_all, poster_time, ranges, write_poster
from studio.render.layouts import LAYOUTS
from studio.render.renderer import Renderer, RenderError, SceneArt
from studio.schemas import (
    AudioClip,
    Beat,
    Box,
    Converting,
    Crowd,
    Locations,
    Particles,
    Point,
    Reveal,
    Route,
    Timeline,
    TimelineCamera,
    Timing,
    Turning,
    Word,
)
from studio.render import renderer as renderer_module
from studio.steps import render as render_step
from studio.steps import timeline as timeline_step
from studio.tests import sample_media

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    return sample_media.write_job(tmp_path_factory.mktemp("jobs") / "sample", art=True)


@pytest.fixture(scope="module")
def song(job):
    return Renderer(job, jobs.read(job / media.timeline_path("song"), Timeline))


def pixel(frame, x, y):
    b, g, r, _ = frame[round(y), round(x)]
    return int(r), int(g), int(b)


def located(job, scene, name):
    return jobs.read(job / media.LOCATIONS, Locations).scene(scene).get(name)


def test_a_frame_depends_only_on_its_time(song):
    first = song.frame(14.0).copy()
    song.frame(3.0)
    song.frame(40.0)
    assert (song.frame(14.0) == first).all()
    assert first.shape == (media.VIDEO_HEIGHT, media.VIDEO_WIDTH, 4)
    assert (first[..., 3] == 255).all()


def test_a_structure_is_a_pale_ghost_until_its_name_is_sung(job, song):
    # The hand-written song reveals the thylakoid on the hook at "thylakoid" in v1-1.
    reveal = song.timeline.beats[0].reveal[0]
    centroid = located(job, "hook", "Thylakoid").centroid
    green = p.rgb("#2E8B57")
    before = pixel(song.frame(reveal.t - 0.5), *song.art_to_frame(reveal.t - 0.5, centroid.x, centroid.y))
    after = pixel(song.frame(reveal.t + 0.6), *song.art_to_frame(reveal.t + 0.6, centroid.x, centroid.y))
    assert all(c > 200 for c in before)  # mostly white
    assert after == pytest.approx(green, abs=3)


def test_a_structure_never_named_on_its_scene_is_drawn_as_it_is_not_as_a_ghost(job, song):
    # Only what the video names on a scene builds up from a ghost; the rest is drawn plainly, unlabelled.
    reveals = {"Thylakoid": 5.0}  # as if the video never named Cytochrome b6f on the overview
    assert song._ghosts("overview", reveals, 4.0) == {"Thylakoid": 1.0, "Cytochrome b6f": 0.0}
    assert song._ghosts("overview", reveals, 6.0) == {"Thylakoid": 0.0, "Cytochrome b6f": 0.0}


def test_a_spotlight_washes_the_others_and_leaves_its_structure_alone(job):
    art = SceneArt(job, jobs.read(job / media.LOCATIONS, Locations).scene("overview"))
    _, plain = art.compose({}, {})
    _, lit = art.compose({}, {"Cytochrome b6f": 1.0})
    plain_px, lit_px = plain.toarray(), lit.toarray()
    thylakoid = located(job, "overview", "Thylakoid").label_anchor
    b6f = located(job, "overview", "Cytochrome b6f").centroid
    assert (lit_px[int(b6f.y), int(b6f.x)] == plain_px[int(b6f.y), int(b6f.x)]).all()
    assert lit_px[int(thylakoid.y), int(thylakoid.x)][:3].min() > plain_px[int(thylakoid.y), int(thylakoid.x)][:3].min() + 80
    assert art.compose({}, {"Cytochrome b6f": 1.0})[1] is lit  # composed once, then reused


def test_on_dark_art_the_ghost_and_the_spotlight_fade_toward_its_background(tmp_path):
    from studio import svg

    dark = sample_media.write_job(tmp_path / "dark", art=True)
    path = dark / media.art_path("overview")
    rgb = skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3].copy()
    rgb[(rgb > 250).all(axis=2)] = (40, 55, 75)  # the white behind the diagram, now dark slate
    path.write_bytes(svg.png(rgb))
    art = SceneArt(dark, jobs.read(dark / media.LOCATIONS, Locations).scene("overview"))
    _, plain = art.compose({}, {})
    _, ghosted = art.compose({"Cytochrome b6f": 1.0}, {})
    _, lit = art.compose({}, {"Cytochrome b6f": 1.0})
    b6f = located(dark, "overview", "Cytochrome b6f").centroid
    thylakoid = located(dark, "overview", "Thylakoid").label_anchor
    assert ghosted.toarray()[int(b6f.y), int(b6f.x)][:3].max() < 110  # dark, not a white blob
    before = plain.toarray()[int(thylakoid.y), int(thylakoid.x)][:3]
    after = lit.toarray()[int(thylakoid.y), int(thylakoid.x)][:3]
    assert after.max() < before.max() - 40  # the others dimmed into the background, not washed white


def test_the_lyric_band_is_white_and_marks_the_word_being_sung(song):
    beat = song.timeline.beats[0]
    word = beat.words[1]
    frame = song.frame((word.start + word.end) / 2)
    band = frame[media.VIDEO_HEIGHT - 100:, :, :3]
    assert (band[:, :40] == 255).all()  # the band's ends are plain white
    marker = p.rgb("#FDE68A")
    marked = ((band[..., 2] == marker[0]) & (band[..., 1] == marker[1]) & (band[..., 0] == marker[2])).sum()
    assert marked > 1000


def gapped(job, video: str) -> Timeline:
    """The sample's timeline with instrumental gaps: a 3 s intro, and 2 s more before each
    chorus (each sentence of the narration, for the lesson)."""
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    for kind, starts_gap in (("song", lambda line_id: line_id.startswith("c")), ("narration", lambda line_id: True)):
        data = timed[kind].model_dump()
        for i, line in enumerate(data["lines"]):
            shift = 3.0 + 2.0 * sum(1 for earlier in data["lines"][1:i + 1] if starts_gap(earlier["line_id"]))
            line["start"] += shift
            line["end"] += shift
            for w in line["words"]:
                w["start"] += shift
                w["end"] += shift
        data["duration"] = data["lines"][-1]["end"] + 4.0
        timed[kind] = Timing.model_validate(data)
    return timeline_step.build(video, bundle, timed, jobs.read(job / media.LOCATIONS, Locations))


def band_of(frame):
    return frame[media.VIDEO_HEIGHT - 100:, :, :3].copy()  # the frame is a view the next one draws over


def test_late_in_a_gap_the_song_shows_its_next_line_dimmed_and_unmarked(job, monkeypatch):
    monkeypatch.setattr(timeline_step, "INTRO_MAX_S", 99.0)  # keep the 3 s intro: it is the long gap here
    song = Renderer(job, gapped(job, "song"))
    beats = song.timeline.beats
    gaps = [i for i, b in enumerate(beats) if b.line_id is None]
    intro, before_chorus, outro = gaps[0], gaps[1], gaps[-1]
    assert beats[intro].t1 - beats[intro].t0 == pytest.approx(3.0, abs=0.05)
    lead_at = beats[intro].t1 - renderer_module.LEAD_S
    assert song._band_beat(intro, lead_at - 0.05) is beats[intro]  # a long gap: the band stays empty...
    assert song._band_beat(intro, lead_at + 0.05) is beats[intro + 1]  # ...until the next line leads
    # A gap too short to be worth an empty band shows the next line throughout.
    short = beats[before_chorus]
    assert short.t1 - short.t0 < renderer_module.LEAD_S + renderer_module.BLINK_S
    assert song._band_beat(before_chorus, short.t0 + 0.01) is beats[before_chorus + 1]
    # After the last line there is no next line to lead.
    assert outro == len(beats) - 1 and song._band_beat(outro, beats[outro].t1 - 0.01) is beats[outro]

    marker = p.rgb("#FDE68A")
    empty, leading = band_of(song.frame(lead_at - 0.5)), band_of(song.frame(lead_at + 0.5))
    assert (empty == 255).all()
    assert (leading != 255).any(axis=-1).sum() > 1000  # the next line's words are drawn...
    marked = ((leading[..., 2] == marker[0]) & (leading[..., 1] == marker[1]) & (leading[..., 0] == marker[2])).sum()
    assert marked == 0  # ...none of them marked, since none is sung yet
    song.frame(beats[intro + 1].t0 + 0.5)  # the line itself, sung
    assert (band_of(song.frame(lead_at + 0.5)) == leading).all()  # a frame still depends only on its time


def test_the_lesson_keeps_an_empty_band_in_its_gaps(job):
    lesson = Renderer(job, gapped(job, "lesson"))
    beats = lesson.timeline.beats
    gaps = [i for i, b in enumerate(beats) if b.line_id is None]
    assert gaps and all(lesson._band_beat(i, beats[i].t1 - 0.01) is beats[i] for i in gaps)


def test_labels_stay_in_the_frame_and_off_each_other():
    bounds = (20, 20, 1900, 950)
    labels = [p.Label(name=f"Structure {i}", color="#3366CC", anchor=(960 + 3 * i, 480 + 2 * i),
                      box=(900, 420, 1020, 540), alpha=1) for i in range(6)]
    labels.append(p.Label(name="Near the edge", color="#E07A2F", anchor=(1910, 10), box=(1880, 0, 1920, 40), alpha=1))
    p.place_labels(labels, bounds, (960, 485), 30)
    boxes = []
    for lb in labels:
        w, h = lb.size(30)
        box = (lb.x - w / 2, lb.y - h / 2, lb.x + w / 2, lb.y + h / 2)
        assert bounds[0] - 0.01 <= box[0] and box[2] <= bounds[2] + 0.01
        assert bounds[1] - 0.01 <= box[1] and box[3] <= bounds[3] + 0.01
        boxes.append(box)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
    again = [p.Label(name=lb.name, color=lb.color, anchor=lb.anchor, box=lb.box, alpha=1) for lb in labels]
    p.place_labels(again, bounds, (960, 485), 30)
    assert [(lb.x, lb.y) for lb in again] == [(lb.x, lb.y) for lb in labels]  # the same every time


@pytest.mark.parametrize("dot_x, shown", [(-15, False), (3, False), (20, True)])
def test_a_label_is_drawn_only_while_its_dot_is_on_screen(job, monkeypatch, dot_x, shown):
    # The first live run: zoomed in on the brainstem, the cerebral cortex's dot sat just above the
    # frame, and its pill hung from the top edge at 70% with its leader running off-screen. A dot
    # wholly on screen, though near the edge, keeps its label at full strength.
    beat = Beat(t0=0, t1=5.0, scene="overview", camera=TimelineCamera(target="Cytochrome b6f", zoom=3.0),
                reveal=[], spotlight=None, labels=["Thylakoid", "Cytochrome b6f"], particles=[], caption=None,
                line_id="v1-1", line="b6f pumps", words=[Word(word="b6f", start=0.2, end=0.6)])
    timeline = Timeline(video="song", duration=5.0, fps=30, audio=[], beats=[beat],
                        palette={"Thylakoid": "#2E8B57", "Cytochrome b6f": "#E07A2F", "ATP synthase": "#3366CC"})
    settled = 4.0  # the camera has finished its move onto b6f
    camera = Renderer(job, timeline)
    x0 = camera.art_to_frame(settled, 0, 0)[0]
    scale = camera.art_to_frame(settled, 1, 0)[0] - x0
    locations = jobs.read(job / media.LOCATIONS, Locations)
    thylakoid = locations.scene("overview").get("Thylakoid")
    thylakoid.label_anchor.x = (dot_x - x0) / scale  # dot_x px from the frame's left edge
    assert thylakoid.box.holds(thylakoid.label_anchor)
    placed = []
    monkeypatch.setattr(p, "place_labels", lambda labels, *args, **kw: placed.extend(labels))
    Renderer(job, timeline, locations=locations).frame(settled)
    alphas = {lb.name: lb.alpha for lb in placed}
    assert alphas["Cytochrome b6f"] == pytest.approx(1)
    if shown:
        assert alphas["Thylakoid"] == pytest.approx(1)
    else:
        assert alphas.get("Thylakoid", 0) < 0.3  # its dot is (partly) cut off: it has faded out


PALETTE = {"Thylakoid": "#2E8B57", "Cytochrome b6f": "#E07A2F", "ATP synthase": "#3366CC"}
FLOW = ["Thylakoid", "Cytochrome b6f"]


def flowing(beats: list[Beat]) -> Timeline:
    """A song of these beats on the overview."""
    return Timeline(video="song", duration=beats[-1].t1, fps=30, audio=[], beats=beats, palette=PALETTE)


def flow_beat(t0, t1, path=FLOW, labels=(), reveal=(), label="", color="#F2C230"):
    particles = [Particles(path=list(path), color=color, trail=True, label=label)] if path else []
    return Beat(t0=t0, t1=t1, scene="overview", camera=TimelineCamera(target=None, zoom=1.0), reveal=list(reveal),
                spotlight=None, labels=list(labels), particles=particles, caption=None, line_id=f"v{t0}",
                line="protons flow", words=[Word(word="protons", start=t0 + 0.1, end=t0 + 0.5)])


def test_the_lead_particle_holds_at_each_stop_and_eases_between_them():
    points = [(0.0, 0.0), (520.0, 0.0), (520.0, 260.0)]
    pause = renderer_module.FLOW_PAUSE_S
    first = 520 / renderer_module.FLOW_SPEED
    second = max(260 / renderer_module.FLOW_SPEED, renderer_module.FLOW_MIN_LEG_S)
    assert renderer_module.lead_at(points, 0.1) == ((0.0, 0.0), 0)
    assert renderer_module.lead_at(points, pause + first / 2)[0] == pytest.approx((260, 0))  # halfway
    assert renderer_module.lead_at(points, pause + first + 0.1)[0] == pytest.approx((520, 0))  # holding at the second stop
    assert renderer_module.lead_at(points, 3 * pause + first + second + 0.1) == ((0.0, 0.0), 1)  # its next trip


def test_particles_stop_beside_the_label_dots():
    anchors = [(0.0, 0.0), (300.0, 0.0), (300.0, 60.0)]
    off = renderer_module.STOP_OFFSET
    stops = [c for point in renderer_module.stop_points(anchors) for c in point]
    assert stops == pytest.approx([off, 0, 300 - off, 0, 300, 40])  # the last is a third of its short leg in


def test_the_stream_spreads_evenly_and_fades_at_the_ends():
    dots = renderer_module.stream_at([(0.0, 0.0), (900.0, 0.0)], 0.0)
    assert [q[0] for q, _ in dots] == pytest.approx([90 * j for j in range(10)])
    assert dots[0][1] == 0 and dots[5][1] == 1
    assert renderer_module.stream_at([(5.0, 5.0), (5.0, 5.0)], 1.0) == []


def test_particles_keep_going_across_lines_and_wait_for_their_stops(job):
    reveal_b6f = Reveal(name="Cytochrome b6f", t=2.0)
    plans = Renderer(job, flowing([
        flow_beat(0, 4, labels=FLOW, reveal=[reveal_b6f]), flow_beat(4, 8, labels=FLOW),  # one run of two lines
        flow_beat(8, 10, path=[], labels=FLOW), flow_beat(10, 12, labels=FLOW),  # a break, then a new run
    ])).plans
    assert plans[0].runs == [(2.0, 8.0)]  # from the reveal of its last stop
    assert plans[1].runs == [(2.0, 8.0)]
    assert plans[3].runs == [(10.0, 12.0)]


def test_particles_travel_between_the_structures_and_fade_in(job):
    flow = Renderer(job, flowing([flow_beat(0, 8)]))  # no labels, so nothing is drawn over the particle
    still = Renderer(job, flowing([flow_beat(0, 8, path=[])]))
    stops = flow._path_points("overview", FLOW)  # a stop in a space sits beside its neighbour on the path
    leg = max(math.dist(*stops) / renderer_module.FLOW_SPEED, renderer_module.FLOW_MIN_LEG_S)
    held = renderer_module.FLOW_PAUSE_S + leg + 0.2  # the lead particle is holding at cytochrome b6f
    gold = p.rgb("#F2C230")
    at_b6f = flow.art_to_frame(held, *stops[1])
    frame = flow.frame(held).copy()
    assert pixel(frame, *at_b6f) == pytest.approx(gold, abs=6)
    assert pixel(still.frame(held), *at_b6f) != pytest.approx(gold, abs=40)
    assert (flow.frame(0.0) == still.frame(0.0)).all()  # they fade in: nothing yet at the start
    flow.frame(1.0)
    assert (flow.frame(held) == frame).all()  # a frame still depends only on its time


def test_a_marked_particle_is_a_bigger_badge_with_what_it_carries_on_it(job, monkeypatch):
    cyan = "#4CC9F0"
    marked = Renderer(job, flowing([flow_beat(0, 8, label="e⁻", color=cyan)]))
    plain = Renderer(job, flowing([flow_beat(0, 8, color=cyan)]))
    stops = marked._path_points("overview", FLOW)
    held = renderer_module.FLOW_PAUSE_S + 0.1  # holding at the thylakoid
    x, y = marked.art_to_frame(held, *stops[0])
    badge, dot = marked.frame(held).copy(), plain.frame(held).copy()

    def cyan_across(frame):  # how wide the cyan runs across the particle at its widest, near where it holds
        rows = frame[round(y) - 30:round(y) + 30, round(x) - 70:round(x) + 70, 2::-1]  # BGRA to RGB
        return int((np.abs(rows.astype(int) - p.rgb(cyan)).max(2) < 12).sum(1).max())

    assert cyan_across(badge) > cyan_across(dot) + 6  # bigger than the plain dot
    ink = badge[round(y) - 6:round(y) + 6, round(x) - 8:round(x) + 8, :3].reshape(-1, 3)
    assert (ink.max(1) < 60).any()  # the dark "e⁻" on it, cyan being light

    # A word never rides a moving particle: glucose moves as its ring, and is named, still, in the legend.
    word = Renderer(job, flowing([flow_beat(0, 8, label="glucose", color=cyan)]))
    shapes, legends = [], []
    monkeypatch.setattr(p, "draw_kind", lambda canvas, at, color, label, alpha, size=1.0: shapes.append((label, size)))
    monkeypatch.setattr(p, "draw_legend", lambda canvas, rect, entries, arrows=(): legends.append((rect, entries)))
    times = (held + 1.0, held + 2.0)  # the particle has moved on; the legend has not
    for t in times:
        word.frame(t)
    assert p.kind_of("glucose") == "sugar" and ("glucose", 1.0) in shapes  # the lead particle, at full size
    assert [[label for _, label, _ in entries] for _, entries in legends] == [["glucose"], ["glucose"]]
    assert legends[0][0] == legends[1][0]  # in one place
    assert [p.kind_of(label) for label in ("H⁺", "H₂O", "ATP", "urea", "water")] == [
        "symbol", "symbol", "symbol", "dot", "water"]  # a short symbol is printed on its particle


def test_a_space_filling_up_shows_more_dots_inside_it_as_it_fills(job):
    gold = "#F2C230"

    def crowded(level):
        beat = flow_beat(0, 8, path=[]).model_copy(update={"crowds": [
            Crowd(region="Thylakoid", label="H⁺", color=gold, start=level, end=level)]})
        return Renderer(job, flowing([beat]))

    mask = skia.Image.open(str(job / located(job, "overview", "Thylakoid").mask)).toarray(
        colorType=skia.kGray_8_ColorType) > 127
    full = crowded(1.0)
    scale, x0, y0 = renderer_module.to_frame(full.plans[0].cam(4.0, full.layout.view, full.scene("overview")),
                                             full.layout.view, full.scene("overview"))

    def gold_inside(renderer):  # gold pixels on the frame, and how many of them fall on the thylakoid
        frame = renderer.frame(4.0)
        rgb = frame[..., 2::-1].astype(int)
        ys, xs = np.nonzero(np.abs(rgb - p.rgb(gold)).max(-1) < 20)
        ax, ay = ((xs - x0) / scale).astype(int), ((ys - y0) / scale).astype(int)
        ok = (ax >= 0) & (ax < mask.shape[1]) & (ay >= 0) & (ay < mask.shape[0])
        return len(xs), int(mask[ay[ok], ax[ok]].sum())

    empty, low, lots = gold_inside(crowded(0.0)), gold_inside(crowded(0.2)), gold_inside(full)
    assert empty == (0, 0) and 0 < low[0] < lots[0]
    assert lots[1] > 0.9 * lots[0]  # the dots sit on the space
    assert (full.frame(4.0) == full.frame(4.0)).all()


def test_a_round_part_seen_face_on_turns_in_place_and_nothing_outside_it_moves(job):
    def turned(turns):
        beat = flow_beat(0, 8, path=[]).model_copy(update={"turning": [
            Turning(structure="Cytochrome b6f", turns=turns, from_speed=0.0, speed=0.0)]})
        return Renderer(job, flowing([beat]))

    still, quarter = turned(0.0), turned(0.25)
    a, b = still.frame(4.0).copy(), quarter.frame(4.0).copy()
    cx, cy, r = quarter.scene("overview").spin_disc("Cytochrome b6f")
    scale, dx, dy = renderer_module.to_frame(quarter.plans[0].cam(4.0, quarter.layout.view, quarter.scene("overview")),
                                             quarter.layout.view, quarter.scene("overview"))
    ys, xs = np.nonzero((a != b).any(-1))
    assert len(xs) > 500  # it turned
    assert (np.hypot(xs - (cx * scale + dx), ys - (cy * scale + dy)) <= r * scale + 2).all()  # inside its disc only


def test_a_part_that_is_not_round_gets_an_arrow_round_it_while_it_turns(job):
    def turning(speed):  # the overview's thylakoid is a long band: no disc to turn in place
        beat = flow_beat(0, 8, path=[]).model_copy(update={"turning": [
            Turning(structure="Thylakoid", turns=0.1, from_speed=speed, speed=speed)]})
        return Renderer(job, flowing([beat])).frame(4.0).copy()

    plain = Renderer(job, flowing([flow_beat(0, 8, path=[])])).frame(4.0).copy()
    drawn = lambda frame: int((frame != plain).any(-1).sum())  # the sample art is on white: count what changed
    assert drawn(turning(0.45)) > 1500  # the arrow
    assert drawn(turning(0.0)) == 0  # stopped: no arrow


def test_blocked_particles_pile_up_at_their_last_stop_under_a_cross(job):
    blocked = flowing([flow_beat(0, 20, label="e⁻", color="#4CC9F0").model_copy(update={"particles": [
        Particles(path=FLOW, color="#4CC9F0", trail=True, label="e⁻", blocked=True)]})])
    free = Renderer(job, flowing([flow_beat(0, 20, label="e⁻", color="#4CC9F0")]))
    stuck = Renderer(job, blocked)
    red = lambda frame: int((np.abs(frame[..., 2::-1].astype(int) - p.rgb("#E5484D")).max(-1) < 25).sum())
    stops = renderer_module.stop_points([(a.x, a.y) for a in (located(job, "overview", n).label_anchor for n in FLOW)])
    arrived = renderer_module.trip_s(stops)
    assert red(stuck.frame(arrived - 0.2).copy()) < 30  # no cross before the first one gets there
    assert red(stuck.frame(arrived + 6).copy()) > 400 and red(free.frame(arrived + 6).copy()) < 30
    # The dots queue up behind the last stop, and stay: the queue is the same a little later.
    late = renderer_module.pile_at(stops, 60.0)
    assert len(late) == renderer_module.PILE_MAX or len(late) > 3
    assert renderer_module.pile_at(stops, 60.0) == renderer_module.pile_at(stops, 61.0)
    end = stops[-1]
    assert all(math.dist(q, end) <= renderer_module.PILE_SPACING * (j + 1) + 1 for j, (q, _) in enumerate(late))


def test_a_glowing_part_is_lit_and_pulses(job):
    lit = Renderer(job, flowing([flow_beat(0, 8, path=[]).model_copy(update={"glow": "Cytochrome b6f"})]))
    plain = Renderer(job, flowing([flow_beat(0, 8, path=[])]))
    changed = [int((lit.frame(t).copy() != plain.frame(t)).any(-1).sum()) for t in (1.0, 1.4, 1.8)]
    assert all(c > 500 for c in changed) and len(set(changed)) > 1  # lit throughout, and pulsing


def test_the_camera_drifts_on_while_a_line_holds_and_lines_on_one_shot_run_on(job):
    close = TimelineCamera(target="Cytochrome b6f", zoom=1.8)
    renderer = Renderer(job, flowing([flow_beat(0, 8, path=[], labels=FLOW), flow_beat(8, 16, path=[], labels=FLOW),
                                      flow_beat(16, 30, path=[], labels=FLOW).model_copy(update={"camera": close})]))
    view, art = renderer.layout.view, renderer.scene("overview")
    zoom = lambda i, t: renderer.plans[i].cam(t, view, art).zoom
    # The opening pulls back from close in, and pulls on back, ever more slowly, across both lines of the shot.
    settled = renderer.plans[0].move_at + renderer.plans[0].move_s
    assert renderer.plans[0].drift == -1 and zoom(0, settled) > zoom(0, 7.9) > zoom(1, 15.9)
    assert zoom(0, settled) - zoom(0, 7.9) > zoom(1, 8.1) - zoom(1, 15.9) > 0
    assert renderer.plans[1].cam(8.0, view, art) == renderer.plans[0].cam(8.0, view, art)  # no jump at the line
    assert (renderer.frame(6.0).copy() != renderer.frame(7.0)).any()  # no frame stands still
    # A close-up pushes on in after its move, never past DRIFT.
    held = renderer.plans[2].move_at + renderer.plans[2].move_s
    assert renderer.plans[2].drift == 1 and zoom(2, held) < zoom(2, 29.9) < renderer.plans[2].target.zoom * (
        1 + renderer_module.DRIFT)
    # Pulled back to a picture that just fills the view, it pushes in instead: further back, its edge would show.
    assert Renderer._drift(renderer_module.Cam(0, 0, 1.06), renderer_module.Cam(0, 0, 1.0)) == 1.0
    assert Renderer._drift(renderer_module.Cam(0, 0, 1.6), renderer_module.Cam(0, 0, 1.3)) == -1.0


def test_the_songs_following_shot_counts_from_the_whole_scene_and_keeps_the_part_in_view(job):
    # The whole scene frames what is drawn, here closer than the whole art: a follow at 1.4 of the whole art would only
    # have slid across (timeline._follow).
    renderer = Renderer(job, flowing([flow_beat(0, 8, path=[], labels=FLOW)]))
    view = renderer.layout.view
    whole = renderer._whole("overview", view).zoom
    assert whole > timeline_step.FOLLOW_ZOOM  # the sample's overview is a strip across the canvas
    followed = renderer._aim("overview", "Cytochrome b6f", timeline_step.FOLLOW_ZOOM, from_whole=True)
    assert followed.zoom == pytest.approx(whole * timeline_step.FOLLOW_ZOOM)
    # A part too big to be that much closer is framed as close as it fits, never wider than the whole scene.
    assert renderer._aim("overview", "Thylakoid", timeline_step.FOLLOW_ZOOM, from_whole=True).zoom == whole
    # The direction's own zoom still counts from the whole art.
    assert renderer._aim("overview", "Cytochrome b6f", 1.4).zoom == 1.4


def test_a_flow_runs_on_when_another_joins_it(job):
    back = ["Cytochrome b6f", "Thylakoid"]
    joined = flow_beat(4, 8, labels=FLOW)
    joined = joined.model_copy(update={"particles": joined.particles + [
        Particles(path=back, color="#4CC9F0", trail=True, label="e⁻")]})
    plans = Renderer(job, flowing([flow_beat(0, 4, labels=FLOW), joined, flow_beat(8, 12, path=back, labels=FLOW,
                                                                                    label="e⁻", color="#4CC9F0")])).plans
    assert plans[0].runs == [(0.0, 8.0)] and plans[1].runs == [(0.0, 8.0), (4.0, 12.0)]  # it keeps going, unrestarted
    assert plans[2].runs == [(4.0, 12.0)]


def reacting_off(monkeypatch):
    monkeypatch.setattr(Renderer, "_reacting", lambda *args, **kwargs: None)


def changed_box(a, b):
    ys, xs = np.nonzero((a != b).any(-1))
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


def part_on_frame(renderer, t, name, grow=0.0):
    """The part's box on the frame, grown by `grow` of its size each side, and 8 px for its soft edge and the sample's
    boxes, which are read off every 4th column."""
    box = located(renderer.job, "overview", name).box
    (x0, y0), (x1, y1) = renderer.art_to_frame(t, box.x0, box.y0), renderer.art_to_frame(t, box.x1, box.y1)
    w, h = (x1 - x0) * grow, (y1 - y0) * grow
    return x0 - w - 8, y0 - h - 8, x1 + w + 8, y1 + h + 8


def test_a_part_pops_as_its_name_is_first_heard_and_not_when_a_later_scene_carries_its_label(job, monkeypatch):
    def heard(t):
        return [Reveal(name="Cytochrome b6f", t=t)]

    beats = [flow_beat(0, 4, path=[], labels=["Thylakoid"]).model_copy(update={"scene": "hook", "labels": ["Thylakoid"],
                                                                                "reveal": [Reveal(name="Thylakoid", t=1.0)]}),
             flow_beat(4, 12, path=[], labels=FLOW, reveal=[Reveal(name="Thylakoid", t=4.0)] + heard(6.0))]
    peak, after = 6.0 + renderer_module.POP_S / 2, 6.0 + renderer_module.POP_S + 0.1
    popping = Renderer(job, flowing(beats))
    frames = {t: popping.frame(t).copy() for t in (4.2, peak, after)}
    reacting_off(monkeypatch)
    plain = Renderer(job, flowing(beats))
    x0, y0, x1, y1 = changed_box(frames[peak], plain.frame(peak))
    bx0, by0, bx1, by1 = part_on_frame(plain, peak, "Cytochrome b6f", grow=renderer_module.POP / 2)
    assert bx0 <= x0 and by0 <= y0 and x1 <= bx1 and y1 <= by1  # only the part, grown about its middle
    assert (frames[after] == plain.frame(after)).all()  # and back as it was
    # The overview's thylakoid was named on the hook: its label is carried over, and it does not pop there.
    assert (frames[4.2] == plain.frame(4.2)).all()


def test_a_part_bumps_as_a_particle_reaches_it_and_rocks_while_particles_pass_through_it(job, monkeypatch):
    into = Renderer(job, flowing([flow_beat(0, 12, labels=FLOW)]))
    trip, [(at, name)], through = into._arrivals("overview", into.timeline.beats[0].particles[0])
    assert name == "Cytochrome b6f" and through == []  # the thylakoid is a space: never bumped, nor rocked
    bump = at + renderer_module.BUMP_S / 2
    bumped = {t: into.frame(t).copy() for t in (bump, at + renderer_module.BUMP_S + 0.05)}
    across = ["Thylakoid", "Cytochrome b6f", "Thylakoid"]
    passing = Renderer(job, flowing([flow_beat(0, 12, path=across, labels=FLOW)]))
    assert passing._arrivals("overview", passing.timeline.beats[0].particles[0])[2] == ["Cytochrome b6f"]
    swing = (6 + 0.25 / renderer_module.ROCK_HZ, 6 + 0.75 / renderer_module.ROCK_HZ)  # one way, then the other
    rocked = [passing.frame(t).copy() for t in swing]
    reacting_off(monkeypatch)
    plain_into, plain_passing = Renderer(job, flowing([flow_beat(0, 12, labels=FLOW)])), Renderer(
        job, flowing([flow_beat(0, 12, path=across, labels=FLOW)]))
    x0, y0, x1, y1 = changed_box(bumped[bump], plain_into.frame(bump))
    bx0, by0, bx1, by1 = part_on_frame(plain_into, bump, "Cytochrome b6f", grow=renderer_module.BUMP / 2)
    assert bx0 <= x0 and by0 <= y0 and x1 <= bx1 and y1 <= by1
    late = at + renderer_module.BUMP_S + 0.05
    assert (bumped[late] == plain_into.frame(late)).all()
    for t, frame in zip(swing, rocked):
        x0, y0, x1, y1 = changed_box(frame, plain_passing.frame(t))
        bx0, by0, bx1, by1 = part_on_frame(plain_passing, t, "Cytochrome b6f", grow=0.08)
        assert bx0 <= x0 and by0 <= y0 and x1 <= bx1 and y1 <= by1
    assert (rocked[0] != rocked[1]).any()


def test_a_part_rocks_only_as_far_as_its_shape_still_covers_its_painted_copy(job, graph):
    art = SceneArt(job, jobs.read(job / media.LOCATIONS, Locations).scene("overview"))
    assert art.rock_deg("Cytochrome b6f") == max(renderer_module.ROCK_DEGS)  # an upright oval: the furthest
    assert graph_art(graph, "overview").rock_deg("Supply curve") == 0.0  # a long, thin line: its ends would swing clear


def test_a_part_named_as_its_stage_fades_in_pops_with_it(job, monkeypatch):
    b6f = Reveal(name="Cytochrome b6f", t=4.1)
    beats = [flow_beat(0, 4, path=[], labels=["Thylakoid"]).model_copy(update={"scene": "hook"}),
             flow_beat(4, 8, path=[], labels=FLOW, reveal=[b6f])]
    timeline = flowing(beats).model_copy(update={"stages": [["hook", "overview"]]})
    during = 4.1 + renderer_module.POP_S / 2  # inside the stage's fade
    assert during < 4 + renderer_module.STAGE_FADE_S
    popping = Renderer(job, timeline).frame(during).copy()
    reacting_off(monkeypatch)
    assert (popping != Renderer(job, timeline).frame(during)).any()  # not held back to jump in when the fade ends


def test_nothing_on_a_graph_reacts(graph, monkeypatch):
    timeline = jobs.read(graph / media.timeline_path("song"), Timeline)
    peak = Renderer(graph, timeline)._pops["overview"]
    times = [at + renderer_module.POP_S / 2 for _, at in peak]  # each curve and the equilibrium, as it is named
    frames = [Renderer(graph, timeline).frame(t).copy() for t in times]
    reacting_off(monkeypatch)
    assert all((frame == Renderer(graph, timeline).frame(t)).all() for frame, t in zip(frames, times))


def test_upright_a_line_that_sets_the_scenes_flows_running_keeps_looking_where_it_was(tmp_path):
    from types import SimpleNamespace

    shapes = {"overview": {"Left": (400, 512, 120, 120), "Middle": (896, 512, 120, 120), "Right": (1400, 512, 120, 120)}}
    palette = {"Left": "#E07A2F", "Middle": "#2E8B57", "Right": "#3366CC"}
    scene = SimpleNamespace(structures=[SimpleNamespace(name=n, color_hex=c) for n, c in palette.items()])
    sample_media.write_art(tmp_path, SimpleNamespace(scenes=[scene]), shapes)
    jobs.write(tmp_path / media.LOCATIONS, sample_media.locations(shapes))
    left, right = ["Left", "Middle"], ["Middle", "Right"]

    def beat(t0, *paths):
        return Beat(t0=t0, t1=t0 + 4, scene="overview", camera=TimelineCamera(target=None, zoom=1.0), reveal=[],
                    spotlight=None, labels=list(palette), caption=None, line_id=f"l{t0}", line="", words=[],
                    particles=[Particles(path=path, color="#F2C230", trail=True) for path in paths])

    beats = [beat(0, left), beat(4, left, right), beat(8, right, left), beat(12), beat(16, right)]
    timeline = Timeline(video="song", duration=20, fps=30, audio=[], beats=beats, palette=palette)
    aims = [plan.target.cx for plan in Renderer(tmp_path, timeline, "v").plans]
    assert aims[0] == aims[1] == aims[2]  # the left flow goes on, with the right one set going beside it
    assert aims[4] > aims[0] + 200  # a line that sets off a flow of its own looks at it


def test_label_text_is_dark_enough_to_read_on_white():
    for color in ("#F2C230", "#2E8B57", "#E07A2F", "#3366CC", "#FFFFFF"):
        r, g, b = p.rgb(p.darken(color))
        assert 1.05 / (p._luminance(r, g, b) + 0.05) >= 4.5


def test_a_missing_mask_is_a_render_error(job, tmp_path):
    shutil.copytree(job, tmp_path / "job")
    (tmp_path / "job" / media.mask_path("overview", "Thylakoid")).unlink()
    with pytest.raises(RenderError, match="no mask for Thylakoid"):
        Renderer(tmp_path / "job", jobs.read(job / media.timeline_path("song"), Timeline))


def test_frames_split_into_ranges_with_none_missed():
    assert ranges(10, 4) == [(0, 2), (2, 5), (5, 8), (8, 10)]
    assert ranges(3, 8) == [(0, 1), (1, 2), (2, 3)]


# --- encoding -------------------------------------------------------------------------------


def short_timeline(job, video: str) -> Timeline:
    """Two seconds: a line on the hook, then an instrumental gap on the overview."""
    tone = job / "audio" / "tone.mp3"
    if not tone.exists():
        tone.parent.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-q:a", "9", str(tone)], check=True)
    words = [Word(word=w, start=0.2 * i, end=0.2 * i + 0.18)
             for i, w in enumerate(["Light", "lands", "on", "the", "thylakoid"])]
    beat = Beat(t0=0, t1=1.0, scene="hook", camera=TimelineCamera(target=None, zoom=1.0),
                reveal=[Reveal(name="Thylakoid", t=0.8)], spotlight=None, labels=["Thylakoid"], particles=[],
                caption=None, line_id="v1-1", line="Light lands on the thylakoid", words=words)
    gap = Beat(t0=1.0, t1=2.0, scene="overview", camera=TimelineCamera(target=None, zoom=1.0), reveal=[],
               spotlight=None, labels=[], particles=[], caption=None, line_id=None, line="", words=[])
    return Timeline(video=video, duration=2.0, fps=30,
                    audio=[AudioClip(path="audio/tone.mp3", at=0, end=1.0),
                           AudioClip(path="audio/tone.mp3", at=1.5, start=1.0)],
                    palette={"Thylakoid": "#2E8B57", "Cytochrome b6f": "#E07A2F", "ATP synthase": "#3366CC"},
                    beats=[beat, gap])


def test_a_clip_that_starts_partway_into_music_fades_in(job):
    timeline = short_timeline(job, "song")
    timeline.audio[0] = AudioClip(path="audio/tone.mp3", at=0, start=0.5, fade_in=0.5)
    _, mix = audio_filter(job, timeline)
    first, second = mix.split(";")[:2]
    assert "atrim=start=0.500" in first and "afade=t=in:st=0:d=0.500" in first
    assert "afade" not in second  # a clip that starts partway on purpose is not faded


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                          "stream=codec_type,codec_name,width,height,r_frame_rate,nb_read_frames:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_ffmpeg
def test_a_video_encodes_in_parts_with_its_audio_added_once(job, tmp_path):
    timeline = short_timeline(job, "song")
    rendered = encode(job, timeline, tmp_path / "song_h.mp4", parts=2)
    info = probe(rendered.path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"], video["r_frame_rate"]) == ("h264", 1920, 1080, "30/1")
    assert int(video["nb_read_frames"]) == rendered.frames == 60
    assert audio["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.05)
    data = rendered.path.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")  # +faststart: it plays before it has downloaded
    assert not list(tmp_path.glob(".*partial"))


@needs_ffmpeg
def test_videos_made_at_once_on_one_set_of_workers_are_the_files_made_one_by_one(job, tmp_path):
    song, lesson = short_timeline(job, "song"), short_timeline(job, "lesson")
    # A video of another length, so its ranges fall elsewhere: the line alone.
    lesson = Timeline.model_validate({**lesson.model_dump(), "duration": 1.0, "beats": [lesson.beats[0].model_dump()],
                                      "audio": [lesson.audio[0].model_dump()]})
    alone = [encode(job, timeline, tmp_path / f"alone_{timeline.video}.mp4", parts=2) for timeline in (song, lesson)]
    together = encode_all(job, [(song, tmp_path / "song.mp4", "h"), (lesson, tmp_path / "lesson.mp4", "h")], parts=2)
    assert [r.path.name for r in together] == ["song.mp4", "lesson.mp4"]
    assert [r.frames for r in together] == [r.frames for r in alone] == [60, 30]
    for one, both in zip(alone, together):
        assert both.path.read_bytes() == one.path.read_bytes()


@needs_ffmpeg
def test_an_upright_hook_cut_with_a_notice_card_made_beside_the_song_is_the_file_made_alone(job, tmp_path):
    # Each worker builds its own Renderer, and an upright one keeps room above the picture for the tallest card:
    # every worker must keep the same room, so the hook cut made beside the song is the one made alone, card and all.
    notices = {"overview": ["Cytochrome b6f should sit across the membrane, not on it."]}
    song = short_timeline(job, "song").model_copy(update={"notices": notices})
    hook = short_timeline(job, "hook").model_copy(update={"notices": notices, "title": "the light reactions"})
    alone = encode(job, hook, tmp_path / "alone_hook.mp4", media.HOOK_LAYOUT, parts=2)
    together = encode_all(job, [(song, tmp_path / "song.mp4", "h"), (hook, tmp_path / "hook.mp4", media.HOOK_LAYOUT)],
                          parts=2)
    assert together[1].path.read_bytes() == alone.path.read_bytes()
    # The workers drew the card: the frame decoded from the video is the one a Renderer draws, not the one without.
    n, size = 45, LAYOUTS[media.HOOK_LAYOUT]
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(together[1].path), "-vf", f"select=eq(n\\,{n})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True, check=True)
    decoded = np.frombuffer(raw.stdout, np.uint8).reshape(size.height, size.width, 3).astype(int)
    carded = Renderer(job, hook, media.HOOK_LAYOUT)
    assert carded._card_h > 0
    rows = slice(int(carded.view("overview")[1] - carded._card_h), int(carded.view("overview")[1]))
    plain = Renderer(job, hook.model_copy(update={"notices": {}}), media.HOOK_LAYOUT)

    def off(renderer):
        return np.abs(decoded[rows] - renderer.frame(n / hook.fps)[rows, :, :3].astype(int)).mean()

    assert off(carded) < 4 < off(plain)


@needs_ffmpeg
def test_a_video_that_cannot_be_made_is_named_by_its_place_in_the_list(job, tmp_path):
    broken = short_timeline(job, "lesson")
    broken.audio[0] = AudioClip(path="audio/missing.mp3", at=0)
    with pytest.raises(EncodeError, match="no audio file audio/missing.mp3") as raised:
        encode_all(job, [(short_timeline(job, "song"), tmp_path / "song.mp4", "h"), (broken, tmp_path / "lesson.mp4", "h")],
                   parts=2)
    assert raised.value.index == 1


@needs_ffmpeg
def test_a_video_whose_audio_cannot_be_added_is_named_by_its_place_and_the_others_are_kept(job, tmp_path):
    work = tmp_path / "job"
    shutil.copytree(job, work)
    song, lesson = short_timeline(work, "song"), short_timeline(work, "lesson")
    (work / "audio" / "broken.mp3").write_bytes(b"not audio")
    lesson.audio[0] = AudioClip(path="audio/broken.mp3", at=0, end=1.0)  # fails in its join, the last to finish
    with pytest.raises(EncodeError) as raised:
        encode_all(work, [(song, tmp_path / "song.mp4", "h"), (lesson, tmp_path / "lesson.mp4", "h")], parts=2)
    assert raised.value.index == 1
    assert (tmp_path / "song.mp4").stat().st_size and not (tmp_path / "lesson.mp4").exists()


@needs_ffmpeg
def test_the_render_step_writes_both_videos_and_posters(job, tmp_path, monkeypatch):
    work = tmp_path / "job"
    shutil.copytree(job, work)
    for video in media.VIDEOS:
        jobs.write(work / media.timeline_path(video), short_timeline(work, video))
    monkeypatch.setenv("STUDIO_RENDER_WORKERS", "2")
    assert not render_step.is_done(work)
    assert render_step.run(work).startswith("rendered song 2 s")
    assert render_step.is_done(work)
    for video in media.VIDEOS:
        assert (work / media.poster_path(video)).read_bytes()[:2] == b"\xff\xd8"  # a JPEG
    # A newer timeline makes the videos out of date.
    timeline = work / media.timeline_path("song")
    os.utime(timeline, (timeline.stat().st_mtime + 10,) * 2)
    assert not render_step.is_done(work)


def test_the_poster_is_late_in_the_fullest_beat(job, tmp_path):
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    t = poster_time(timeline)
    beat = max(timeline.beats, key=lambda b: (len(b.labels), -b.t0))
    assert beat.t0 <= t < beat.t1
    assert write_poster(job, timeline, tmp_path / "poster.jpg") == t
    assert (tmp_path / "poster.jpg").stat().st_size > 10_000


def test_the_render_step_is_found_by_name():
    assert media.step_module("render") is render_step


@needs_ffmpeg
def test_a_render_started_from_a_plain_script_does_not_run_the_script_again(job, tmp_path):
    # Workers are their own processes: a caller without `if __name__ == "__main__"` is not re-run.
    jobs.write(tmp_path / "timeline.json", short_timeline(job, "song"))
    script = tmp_path / "render_it.py"
    marker = tmp_path / "runs.txt"
    script.write_text(
        "from pathlib import Path\n"
        "from studio import jobs\n"
        "from studio.render.encode import encode\n"
        "from studio.schemas import Timeline\n"
        f"with open({str(marker)!r}, 'a') as f: f.write('run\\n')\n"
        f"timeline = jobs.read(Path({str(tmp_path / 'timeline.json')!r}), Timeline)\n"
        f"encode(Path({str(job)!r}), timeline, Path({str(tmp_path / 'out.mp4')!r}), parts=2)\n")
    root = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ, PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    assert marker.read_text() == "run\n"
    assert (tmp_path / "out.mp4").stat().st_size > 0


@needs_ffmpeg
def test_a_worker_that_fails_stops_the_render_with_one_clear_error(job, tmp_path):
    work = tmp_path / "job"
    shutil.copytree(job, work)
    timeline = short_timeline(work, "song")
    (work / media.art_path("overview")).write_bytes(b"not a png")  # fails in the workers, not before
    with pytest.raises(EncodeError, match=r"RenderError: art/overview.png is not an image"):
        encode(work, timeline, tmp_path / "out.mp4", parts=2)
    assert not (tmp_path / "out.mp4").exists()


@needs_ffmpeg
def test_a_render_that_hangs_is_stopped_at_its_time_limit(job, tmp_path, monkeypatch):
    from studio.render import encode as encode_module

    monkeypatch.setattr(encode_module, "RENDER_LIMIT_BASE_S", 0.5)
    monkeypatch.setattr(encode_module, "RENDER_LIMIT_PER_S", 0.0)
    started = time.monotonic()
    with pytest.raises(EncodeError, match="took over 0 s; its workers were stopped"):
        encode(job, short_timeline(job, "song"), tmp_path / "out.mp4", parts=2)
    assert time.monotonic() - started < 10
    assert not (tmp_path / "out.mp4").exists()


def test_one_stage_of_a_process_fades_into_the_next_framed_alike(job):
    def beat(t0, t1, scene, reveal):
        return Beat(t0=t0, t1=t1, scene=scene, camera=TimelineCamera(target=None, zoom=1.0),
                    reveal=[Reveal(name="Thylakoid", t=reveal)], spotlight=None, labels=["Thylakoid"], particles=[],
                    caption=None, line_id=f"v{t0}", line="the stages", words=[])

    def video(stages):
        return Renderer(job, Timeline(video="song", duration=8, fps=30, audio=[], palette=PALETTE, stages=stages,
                                      beats=[beat(0, 4, "hook", 1.0), beat(4, 8, "overview", 4.0)]))

    staged, cut = video([["hook", "overview"]]), video([])
    fade = renderer_module.STAGE_FADE_S
    # The camera goes on from where it was, rather than cutting in and settling.
    assert staged.plans[1].start == renderer_module.clamp_cam(
        staged.plans[0].cam(4.0, staged.layout.view, staged.scene("hook")), staged.layout.view, staged.scene("overview"))
    assert cut.plans[1].start.zoom > cut.plans[1].target.zoom  # a cut settles in
    # A spot that is the hook's thylakoid and the overview's white background: green, then fading to white.
    def at(renderer, t):
        return pixel(renderer.frame(t), *renderer.art_to_frame(t, 896, 300))

    green, halfway, white = at(staged, 3.9), at(staged, 4 + fade / 2), at(staged, 4 + fade + 0.1)
    assert green[1] > green[0] + 40 and min(white) > 245
    assert all(g + 25 < h < w - 25 for g, h, w in zip(green, halfway, white) if w - g > 100)
    assert min(at(cut, 4 + fade / 2)) > 245  # without stages, a cut
    # The thylakoid, shown on the stage before, is on in full from the next stage's first frame.
    assert staged.revealed["overview"]["Thylakoid"] < 4.0 - renderer_module.REVEAL_S + 1e-9
    assert cut.revealed["overview"]["Thylakoid"] == 4.0
    backwards = video([["overview", "hook"]])  # back to the earlier stage: a cut
    assert backwards.plans[1].fade_from is None and min(at(backwards, 4 + fade / 2)) > 245


def test_a_picture_with_mistakes_found_moves_over_for_a_card_that_discloses_them(job):
    beat = flow_beat(0, 4, path=[], labels=["Thylakoid"], reveal=[Reveal(name="Thylakoid", t=0.0)])
    plain = Renderer(job, flowing([beat]))
    notices = {"overview": ["Cytochrome b6f should sit across the membrane, not on it."]}
    noted = Renderer(job, flowing([beat]).model_copy(update={"notices": notices}))
    width = noted.layout.width
    edge = width - renderer_module.NOTICE_W
    assert plain.view("overview")[2] == width and noted.view("overview")[2] == edge
    assert noted.art_to_frame(2.0, 896, 560)[0] < plain.art_to_frame(2.0, 896, 560)[0]  # the picture moved left
    amber = p.rgb("#F59E0B")
    assert np.allclose(pixel(noted.frame(2.0), edge + 3, 120), amber, atol=12)  # the card's side
    assert not np.allclose(pixel(plain.frame(2.0), edge + 3, 120), amber, atol=12)
    # Every stage of a process with notices keeps the same framing, card or not.
    staged = Renderer(job, flowing([beat]).model_copy(update={"notices": notices, "stages": [["hook", "overview"]]}))
    assert staged.view("hook") == staged.view("overview") and plain.view("hook")[2] == width


def test_a_notice_card_sits_below_the_key_band_and_upright_above_the_picture(job):
    beat = flow_beat(0, 4, path=[], labels=["Thylakoid"], reveal=[Reveal(name="Thylakoid", t=0.0)])
    notices = {"overview": ["Cytochrome b6f should sit across the membrane, not on it."]}
    keyed = flowing([beat.model_copy(update={"key_line": "Light pumps H⁺ into the lumen"})])
    amber = p.rgb("#F59E0B")
    wide = Renderer(job, keyed.model_copy(update={"notices": notices}))
    view = wide.view("overview")
    edge, top = view[2], view[1] + wide.layout.margin + 8
    assert view[1] == wide.layout.key_h
    # The card's amber side starts below the key band, which no longer hides the card's top.
    assert np.allclose(pixel(wide.frame(2.0), edge + 3, top + 40), amber, atol=12)
    assert not np.allclose(pixel(wide.frame(2.0), edge + 3, top - 12), amber, atol=12)
    # Upright, the card goes across the frame above the picture, which keeps the frame's whole width.
    upright = Renderer(job, keyed.model_copy(update={"notices": notices, "title": "the light reactions"}), "v")
    plain = Renderer(job, keyed.model_copy(update={"title": "the light reactions"}), "v")
    tall, short = plain.view("overview"), upright.view("overview")
    assert short[0] == tall[0] and short[2] == tall[2] == upright.layout.width  # nothing moves over
    assert short[1] > tall[1] + 100 and short[3] == tall[3]  # it starts below the card
    frame = upright.frame(2.0)
    card_top = tall[1] + renderer_module.NOTICE_GAP
    assert np.allclose(pixel(frame, upright.layout.margin + 3, card_top + 40), amber, atol=12)
    # The hook cut carries the song's notices; one for a scene it never shows keeps no room above the picture.
    elsewhere = Renderer(job, keyed.model_copy(update={"notices": {"synthase": notices["overview"] * 3},
                                                       "title": "the light reactions"}), "v")
    assert elsewhere.view("overview") == tall
    assert np.allclose(pixel(frame, upright.layout.width - upright.layout.margin - 20, card_top + 40),
                       (255, 255, 255), atol=12)  # white across the frame


def test_every_ai_made_picture_is_tagged_unless_its_notice_card_says_so(job):
    beat = flow_beat(0, 4, path=[])
    plain = Renderer(job, flowing([beat]))
    tagged = Renderer(job, flowing([beat]).model_copy(update={"ai_pictures": ["overview"]}))
    carded = Renderer(job, flowing([beat]).model_copy(update={"ai_pictures": ["overview"],
                                                               "notices": {"overview": ["A note."]}}))
    view, margin = tagged.view("overview"), tagged.layout.margin
    spot = (view[0] + margin + 6, view[3] - margin - renderer_module.AI_TAG_FONT * 0.85)  # inside the tag

    def dark(renderer):
        return max(pixel(renderer.frame(2.0), *spot)) < 120

    assert dark(tagged) and not dark(plain)
    carded_view = carded.view("overview")
    assert not max(pixel(carded.frame(2.0), carded_view[0] + margin + 6, spot[1])) < 120  # the card instead


def test_every_frame_carries_the_brand_in_one_place_and_upright_in_the_title_band(job):
    beat = flow_beat(0, 4, path=[])
    chip = p.rgb("#0B7499")
    wide = Renderer(job, flowing([beat]))
    carded = Renderer(job, flowing([beat]).model_copy(update={"notices": {"overview": ["A note."]}}))
    layout, (w, h) = wide.layout, wide.brand
    spot = (layout.width - layout.margin - w + 3, layout.height - layout.band_h - layout.margin - h / 2)  # in the chip
    assert pixel(wide.frame(0.0), *spot) == pixel(wide.frame(3.9), *spot) == chip
    assert pixel(carded.frame(2.0), *spot) == chip and carded.view("overview")[2] < spot[0]  # under the card
    titled = flowing([beat]).model_copy(update={"title": "the light reactions"})
    keyed = titled.model_copy(update={"beats": [beat.model_copy(update={"key_line": "Light pumps H⁺ into the lumen"})]})
    for timeline in (titled, keyed):  # upright, in the title band's corner, with a key line or without
        upright = Renderer(job, timeline, "v")
        v, (w, h) = upright.layout, upright.brand
        frame = upright.frame(2.0)
        assert pixel(frame, v.width - v.margin - w + 3, v.title_h - renderer_module.BRAND_GAP - h / 2) == chip
        assert pixel(frame, v.width - v.margin - w + 3, v.height - v.band_h - v.margin - h / 2) != chip  # not twice


def test_a_stage_fading_into_the_next_keeps_its_tag_and_card_on(job):
    def beat(t0, t1, scene):
        return Beat(t0=t0, t1=t1, scene=scene, camera=TimelineCamera(target=None, zoom=1.0), reveal=[],
                    spotlight=None, labels=[], particles=[], caption=None, line_id=f"v{t0}", line="x", words=[])

    def video(**extra):
        return Renderer(job, Timeline(video="song", duration=8, fps=30, audio=[], palette=PALETTE,
                                      stages=[["hook", "overview"]], beats=[beat(0, 4, "hook"), beat(4, 8, "overview")],
                                      ai_pictures=["hook", "overview"], **extra))

    tagged = video()
    view, margin = tagged.view("overview"), tagged.layout.margin
    spot = (view[0] + margin + 6, view[3] - margin - renderer_module.AI_TAG_FONT * 0.85)
    assert max(pixel(tagged.frame(4.05), *spot)) < 120  # the tag, not fading in again
    carded = video(notices={"hook": ["A note."], "overview": ["Another note."]})
    edge = carded.view("overview")[2]
    assert np.allclose(pixel(carded.frame(4.05), edge + 3, 120), p.rgb("#F59E0B"), atol=12)  # the card's side


def test_a_long_note_is_cut_so_the_card_stays_above_the_lyric_band():
    surface = skia.Surface(1920, 1080)
    long = " ".join(["word"] * 400)
    bottom = p.draw_notice(surface.getCanvas(), 1480, 28, 1900, [long] * 3, 22)
    assert bottom < 1080 - 110


def test_key_terms_are_found_whole_ignoring_case_longest_first():
    text = "H⁺ flowing back spins ATP synthase → ATP"
    spans = p.key_term_spans(text, ["ATP", "atp synthase", "back"])
    assert [text[a:b] for a, b in spans] == ["back", "ATP synthase", "ATP"]
    assert p.key_term_spans("synthases", ["synthase"]) == []  # whole words only


def test_a_song_with_key_lines_shows_them_in_a_band_over_the_picture(job):
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    beats = [b.model_copy(update={"key_line": "Light pumps H⁺ into the lumen"}) for b in timeline.beats]
    keyed = Renderer(job, timeline.model_copy(update={"beats": beats, "key_terms": ["lumen"]}))
    plain = Renderer(job, timeline)
    assert keyed.view("overview")[1] == plain.view("overview")[1] + keyed.layout.key_h > 0
    frame = keyed.frame(14.0)
    band = frame[:keyed.layout.key_h, :, :3]
    amber = p.rgb("#FCD34D")
    assert ((band[..., 2] == amber[0]) & (band[..., 1] == amber[1]) & (band[..., 0] == amber[2])).sum() > 50
    assert (band[..., :] > 200).all(axis=-1).sum() > 500  # white text
    upright = Renderer(job, keyed.timeline.model_copy(update={"title": "the light reactions"}), "v")
    assert upright.view("overview")[1] == upright.layout.title_h  # in the title band, which keeps its height
    top = upright.frame(14.0)[:upright.layout.title_h, :, :3]
    assert ((top[..., 2] == amber[0]) & (top[..., 1] == amber[1]) & (top[..., 0] == amber[2])).sum() > 50


def test_two_things_on_the_same_stops_run_side_by_side_and_leave_a_part_at_its_edge(job):
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    i = next(i for i, b in enumerate(timeline.beats) if b.scene == "overview")
    path = ["Cytochrome b6f", "Thylakoid"]
    flows = [Particles(path=path, color="#F2C230", label="H⁺", trail=True),
             Particles(path=list(reversed(path)), color="#7FD1FF", label="H₂O", trail=True)]
    beats = list(timeline.beats)
    beats[i] = beats[i].model_copy(update={"particles": flows})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    assert renderer._lanes[("overview", tuple(path), "H⁺")] == 0
    assert renderer._lanes[("overview", tuple(reversed(path)), "H₂O")] == 1  # the same stops, the other way: beside
    first = renderer._path_points("overview", path)
    beside = renderer._path_points("overview", path, lane=1)
    assert all(math.dist(a, b) == pytest.approx(renderer_module.LANE_GAP) for a, b in zip(first, beside))
    b6f = located(job, "overview", "Cytochrome b6f")
    assert renderer._is_space("overview", located(job, "overview", "Thylakoid"))
    x, y = first[0]  # leaving a part for a space: from its edge, where the arrow starts, not from inside it
    box = b6f.box
    on_edge = min(abs(x - box.x0), abs(x - box.x1), abs(y - box.y0), abs(y - box.y1))
    assert on_edge == pytest.approx(0, abs=1e-6) and box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1
    x, y = renderer._path_points("overview", list(reversed(path)))[-1]  # and arriving from a space, at its edge too
    assert min(abs(x - box.x0), abs(x - box.x1), abs(y - box.y0), abs(y - box.y1)) == pytest.approx(0, abs=1e-6)


# --- a graph's axis names -------------------------------------------------------------------


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    return sample_media.write_graph_job(tmp_path_factory.mktemp("jobs") / "graph")


def graph_art(job, scene):
    return SceneArt(job, jobs.read(job / media.LOCATIONS, Locations).scene(scene))


def test_the_axes_are_found_as_the_long_thin_lines_that_are_no_structures(graph, job):
    from studio.render import axes

    (x0, y0, x1, _, width), (_, top, _, bottom, _) = sample_media.AXES["overview"]
    for scene in ("overview", "ceiling"):  # the price ceiling is as long and thin, but a structure
        across, up = graph_art(graph, scene).axis_lines()
        # Each to within its line's width: its ends are round.
        assert np.allclose((across.at, across.start, across.end), (y0, x0, x1), atol=width / 2 + 1)
        assert np.allclose((up.at, up.start, up.end), (x0, top, bottom), atol=width / 2 + 1)
    # A picture with no such line (the sample's ellipses) has none: its axes fall back to the edges of what is drawn.
    art = graph_art(job, "overview")
    pixels = art.art.toarray(colorType=skia.kBGRA_8888_ColorType)
    assert axes.find(axes.distance(pixels, (255, 255, 255)), np.zeros(pixels.shape[:2], bool)) == (None, None)
    across, up = art.axis_lines()
    x0, y0, x1, y1 = art.content
    assert (across.at, across.start, across.end) == (y1, x0, x1) and (up.at, up.start, up.end) == (x0, y0, y1)


def test_a_painted_axis_a_little_off_level_is_found_whole():
    # Level rows only, an axis half a degree off was found 264 pixels short, which put its name mid-axis.
    from studio.render import axes

    pixels = np.full((media.ART_HEIGHT, media.ART_WIDTH, 3), 40, np.uint8)
    for degrees in (0.5, 1.0):
        pixels[...] = 40
        for x in range(300, 1500):  # the x axis, rising to its right end
            y = round(880 - (x - 300) * math.tan(math.radians(degrees)))
            pixels[y - 3:y + 3, x] = 240
        for y in range(140, 880):  # the y axis, leaning right to its top
            x = round(300 + (880 - y) * math.tan(math.radians(degrees)))
            pixels[y, x - 3:x + 3] = 240
        across, up = axes.find(axes.distance(pixels, (40, 40, 40)), np.zeros(pixels.shape[:2], bool))
        assert abs(across.start - 300) <= 4 and abs(across.end - 1500) <= 4 and abs(across.at - 880) <= 15
        assert abs(up.start - 140) <= 4 and abs(up.end - 880) <= 4 and abs(up.at - 300) <= 15


def test_a_mask_grows_by_a_square_and_stops_at_the_edges():
    from studio.render import axes

    mask = np.zeros((9, 12), bool)
    mask[4, 5] = mask[0, 11] = True
    grown = axes.grow(mask, 2)
    expected = np.zeros_like(mask)
    expected[2:7, 3:8] = expected[0:3, 9:12] = True
    assert (grown == expected).all() and mask.sum() == 2  # the mask itself is left as it was


def names_drawn(job, video, t, layout="h"):
    """The frame at t, and where it differs from the same frame with no axis names: the names' pixels."""
    timeline = jobs.read(job / media.timeline_path(video), Timeline)
    renderer = Renderer(job, timeline, layout)
    plain = Renderer(job, timeline.model_copy(update={"axes": {}}), layout).frame(t).copy()
    frame = renderer.frame(t).copy()
    changed = np.abs(frame[..., :3].astype(int) - plain[..., :3]).max(axis=2) > 40
    return renderer, frame, changed


def test_a_graph_has_its_axis_names_at_their_ends_from_its_first_frame(graph):
    t = 5.0  # the song's first line on the graph: nothing on it is named yet
    renderer, frame, changed = names_drawn(graph, "song", t)
    (x0, y0, x1, _, _), (_, top, _, _, _) = sample_media.AXES["overview"]
    ends = [renderer.art_to_frame(t, x1, y0), renderer.art_to_frame(t, x0, top)]  # Quantity's, then Price's
    ys, xs = np.nonzero(changed)
    near = [np.hypot(xs - ex, ys - ey) < 200 for ex, ey in ends]
    assert near[0].sum() > 300 and near[1].sum() > 300  # both written, each beside its own end
    assert (near[0] | near[1]).all()  # and nothing else changed
    # Plain writing in dark ink on a light picture, with no pill.
    assert frame[changed][..., :3].min() < 90 and frame[changed][..., :3].max() > 200


def test_in_a_close_up_the_axis_names_stay_at_the_edges_of_the_frame(graph):
    t = 11.5  # the lesson zoomed on the equilibrium, both axes out of the view
    renderer, _, changed = names_drawn(graph, "lesson", t)
    assert renderer.art_to_frame(t, 300, 880)[1] > renderer.layout.height  # the x axis is below the frame
    ys, xs = np.nonzero(changed)
    margin, right = renderer.layout.margin, renderer.layout.width - renderer.layout.margin
    left_top = (xs < 300) & (ys < 200)  # "Price", up the left edge
    right_bottom = (xs > right - 300) & (ys > renderer.layout.height - renderer.layout.band_h - 200)  # "Quantity"
    assert left_top.sum() > 300 and right_bottom.sum() > 300 and (left_top | right_bottom).all()
    assert xs.min() >= margin - 4 and xs.max() <= right + 4


def test_on_a_dark_picture_the_axis_names_are_light_and_upright_frames_keep_them(tmp_path):
    dark = sample_media.write_graph_job(tmp_path / "dark", dark=True)
    _, frame, changed = names_drawn(dark, "hook", 6.0, "v")  # the hook cut crops the y axis off its left
    ys, xs = np.nonzero(changed)
    assert (xs < 200).any() and (xs > 800).any()  # "Price" kept at the left edge, "Quantity" at the right
    assert frame[changed][..., :3].max() > 220


def test_a_graphs_frame_is_the_same_whichever_frame_a_worker_draws_first(graph):
    # Each render worker starts at its own range: one that starts on the second graph picks its names' spots alone.
    timeline = jobs.read(graph / media.timeline_path("song"), Timeline)
    late = Renderer(graph, timeline).frame(24.0).copy()
    early = Renderer(graph, timeline)
    early.frame(5.0), early.frame(15.0)
    assert (early.frame(24.0) == late).all()


def test_axis_names_stay_on_through_a_stage_fade_when_the_next_stage_has_the_same(graph):
    timeline = jobs.read(graph / media.timeline_path("song"), Timeline)
    cut = next(b.t0 for b in timeline.beats if b.scene == "ceiling")
    mid, settled = cut + renderer_module.STAGE_FADE_S / 2, cut + 2 * renderer_module.STAGE_FADE_S

    def drawn(tl, t):  # how strongly the names show: the whole of their difference from the frame without them
        frame = Renderer(graph, tl).frame(t).copy()
        plain = Renderer(graph, tl.model_copy(update={"axes": {}})).frame(t).copy()
        return np.abs(frame[..., :3].astype(int) - plain[..., :3]).sum()

    staged = timeline.model_copy(update={"stages": [["overview", "ceiling"]]})
    assert drawn(staged, mid) > 0.9 * drawn(staged, settled)  # the same axes: on in full mid-fade
    renamed = staged.model_copy(update={"axes": {**staged.axes, "ceiling": staged.axes["ceiling"].model_copy(
        update={"x": "Bread sold"})}})
    assert drawn(renamed, mid) < 0.8 * drawn(renamed, settled)  # new names fade in with their stage


def test_a_cut_to_a_close_up_pushes_in_on_its_part_and_a_cut_back_pulls_out_of_it(job):
    # Round 18's kidney lesson cut from the body to a nephron, and from the nephron to its tubule, with nothing to say
    # where the new picture was: a cut between two scenes that draw the same part far apart in size goes through it.
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    located = jobs.read(job / media.LOCATIONS, Locations)
    hook = located.scene("hook")
    small = hook.structures[0].model_copy(update={
        "box": Box(x0=850, y0=480, x1=950, y1=560), "centroid": Point(x=900, y=520),
        "label_anchor": Point(x=900, y=520)})
    located = located.model_copy(update={"scenes": [s.model_copy(update={"structures": [small]})
                                                    if s.scene == "hook" else s for s in located.scenes]})
    first = [b for b in timeline.beats if b.scene == "hook"][-1]
    overview = next(b for b in timeline.beats if b.scene == "overview")
    back = overview.model_copy(update={"scene": "hook", "t0": overview.t1, "t1": overview.t1 + 4.0, "particles": [],
                                       "routes": [], "crowds": [], "reveal": [], "labels": [], "spotlight": None,
                                       "camera": first.camera})
    beats = [first, overview, back]
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats, "duration": back.t1}), locations=located)
    # The hook draws the thylakoid small and the overview large: the overview is the close-up here.
    assert renderer._through_part("hook", "overview") == ("Thylakoid", True)
    assert renderer._through_part("overview", "hook") == ("Thylakoid", False)
    assert renderer._through_part("overview", "synthase") is None  # nothing drawn on both
    # Into the close-up: the hook's last moments push in on its thylakoid, up to the cut.
    plan = renderer.plans[0]
    assert plan.push_at == pytest.approx(first.t1 - renderer_module.ZOOM_THROUGH_S)
    view, art = renderer.view("hook"), renderer.scene("hook")
    before, at_cut = plan.cam(plan.push_at, view, art), plan.cam(first.t1 - 0.01, view, art)
    assert at_cut.zoom > before.zoom * 1.3 and 850 <= at_cut.cx <= 950
    # Back out: the hook opens close on the thylakoid and pulls back to the whole picture.
    out = renderer.plans[2]
    assert out.start.zoom > out.target.zoom * 1.3 and 850 <= out.start.cx <= 950
    assert renderer.plans[1].push_at is None  # the overview, cut back out of, does not push in


def test_past_four_labels_those_of_parts_the_line_is_not_about_rest(job):
    # Round 21's digestion overview showed seven labels at once, and the one the voice meant was lost among them.
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    on = [i for i, b in enumerate(timeline.beats) if b.scene == "overview"]
    many = ["Thylakoid", "Cytochrome b6f", "Stroma", "Lumen", "Photosystem II"]
    beats = list(timeline.beats)
    for k in on:
        beats[k] = beats[k].model_copy(update={"labels": many, "named": [], "particles": [], "spotlight": None,
                                               "camera": TimelineCamera(target=None, zoom=1.0)})
    first, second = on[0], on[1]
    beats[second] = beats[second].model_copy(update={"named": ["Thylakoid"], "spotlight": "Lumen"})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    rest = renderer_module.LABEL_REST
    settled = renderer._resting(second, beats[second].t1)
    assert settled["Thylakoid"] == 1.0 and settled["Lumen"] == 1.0  # said on the line; spotlit
    assert all(settled[n] == pytest.approx(rest) for n in ("Cytochrome b6f", "Stroma", "Photosystem II"))
    assert renderer._resting(second, beats[second].t0)["Thylakoid"] == pytest.approx(rest)  # eases up as it starts
    assert renderer._resting(on[2], beats[on[2]].t1)["Thylakoid"] == 1.0  # still in play on the line after
    few = beats[first].model_copy(update={"labels": many[:4]})
    calm = Renderer(job, timeline.model_copy(update={"beats": [*beats[:first], few, *beats[first + 1:]]}))
    assert set(calm._resting(first, few.t1).values()) == {1.0}  # four or fewer: every label in full


def test_a_reaction_pill_rests_with_its_structures_label(job, monkeypatch):
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    on = [i for i, b in enumerate(timeline.beats) if b.scene == "overview"]
    many = ["Thylakoid", "Cytochrome b6f", "Stroma", "Lumen", "Photosystem II"]
    pill = Converting(structure="Cytochrome b6f", text="A → B", since=timeline.beats[on[0]].t0)
    beats = list(timeline.beats)
    for k in on:
        beats[k] = beats[k].model_copy(update={"labels": many, "named": [], "particles": [], "spotlight": None,
                                               "conversions": [pill], "camera": TimelineCamera(target=None, zoom=1.0)})
    beats[on[1]] = beats[on[1]].model_copy(update={"named": ["Cytochrome b6f"]})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    drawn = []
    monkeypatch.setattr(p, "draw_conversion", lambda canvas, at, text, color, alpha, bounds, font: drawn.append(alpha))
    renderer.frame(beats[on[1]].t1 - 0.01)  # named on this line: in full
    renderer.frame(beats[on[3]].t1 - 0.01)  # two lines on, about something else: resting
    assert drawn[0] == pytest.approx(1.0, abs=0.02) and drawn[1] == pytest.approx(renderer_module.LABEL_REST, abs=0.02)


def test_a_flow_across_an_upright_wall_crosses_level_from_inside_the_space(job, monkeypatch):
    # Round 21's lungs put the alveolus's and the capillary's points at the top of the picture, above and below the
    # membrane's, and the gases ran up and down the capillary instead of across the membrane.
    from studio.render import routing
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    renderer = Renderer(job, timeline)
    located = renderer.locations.scene("overview")
    sac, wall = located.get("Thylakoid"), located.get("Cytochrome b6f")
    tall = sac.model_copy(update={"box": Box(x0=100, y0=100, x1=700, y1=950), "centroid": Point(x=400, y=520),
                                  "label_anchor": Point(x=400, y=520)})
    beside = wall.model_copy(update={"box": Box(x0=740, y0=100, x1=800, y1=950), "centroid": Point(x=770, y=520),
                                     "label_anchor": Point(x=770, y=520)})
    monkeypatch.setattr(located, "structures", [tall, beside])
    art = renderer.scene("overview")
    grid = np.zeros_like(art.cells("Thylakoid"))
    grid[100 // routing.GRID:950 // routing.GRID, 100 // routing.GRID:700 // routing.GRID] = True
    monkeypatch.setattr(art, "_cells", {"Thylakoid": grid, "Cytochrome b6f": art.cells("Cytochrome b6f")})
    monkeypatch.setattr(renderer, "_is_space", lambda scene_id, loc: loc.name == "Thylakoid")
    (sx, sy), (wx, wy) = renderer._path_points("overview", ["Thylakoid", "Cytochrome b6f"])
    assert sy == pytest.approx(520) and wy == pytest.approx(520, abs=40)  # level with the wall, not at the top
    assert 100 <= sx <= 700 - renderer_module.SPACE_REACH  # inside the space, well past its edge


def test_a_drawn_value_is_written_beside_its_point_once_said(job, monkeypatch):
    from studio.schemas import Written
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    on = [i for i, b in enumerate(timeline.beats) if b.scene == "overview"]
    beats = list(timeline.beats)
    said = beats[on[1]].t0 + 0.5
    value = Written(text="$1,210", at=Point(x=900, y=400), side="above", since=said)
    for k in on[1:]:
        beats[k] = beats[k].model_copy(update={"values": [value]})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    drawn = []
    monkeypatch.setattr(p, "draw_value", lambda canvas, text, rect, size, paper, alpha, tick, pop=0.0:
                        drawn.append((text, rect, alpha, pop)))
    renderer.frame(said - 0.2)  # before it is said: not yet
    assert drawn == []
    renderer.frame(said + 0.05)  # as it is said: fading in, popping
    renderer.frame(beats[on[1]].t1 - 0.01)  # later: in full, settled
    (text, rect, alpha, pop), (_, later, full, settled) = drawn
    assert text == "$1,210" and alpha < 1.0 and pop > 0.5 and full == pytest.approx(1.0) and settled == 0.0
    x, y = renderer.art_to_frame(beats[on[1]].t1 - 0.01, 900, 400)
    assert later[3] < y and later[0] < x < later[2]  # above its point, across it


def test_what_travels_is_drawn_as_an_arrow_at_each_hand_off_and_a_crossing_as_particles(job, monkeypatch):
    # Round 21's filtrate cut across the nephron's bends and its magma rose through the volcano's rock: a journey is
    # drawn as an arrow where it passes from part to part. A crossing keeps its particles.
    arrows, dots = [], []
    monkeypatch.setattr(p, "draw_handoff", lambda canvas, tail, tip, color, alpha, grown=1.0, pulse=0.0, size=1.0:
                        arrows.append((tail, tip, grown, pulse)))
    monkeypatch.setattr(p, "draw_particles", lambda *a, **k: dots.append(a))

    def moving(label, moves):
        beat = flow_beat(0, 12, labels=FLOW, label=label)
        flow = beat.particles[0].model_copy(update={"moves": moves})
        return Renderer(job, flowing([beat.model_copy(update={"particles": [flow]})]))

    travelling = moving("filtrate", "travels")
    flow = travelling.timeline.beats[0].particles[0]
    assert travelling._travels("overview", flow)
    [(tail, tip, into)] = travelling._handoffs("overview", flow)  # one hand-off: from the thylakoid into b6f
    b6f = located(job, "overview", "Cytochrome b6f").box
    assert b6f.y0 - 60 <= tip[1] <= b6f.y1 + 60 and tip != tail and into.name == "Cytochrome b6f"
    start = travelling.plans[0].runs[0][0]  # when it sets off
    travelling.frame(start + renderer_module.HANDOFF_GROW_S / 2)  # drawing itself in
    travelling.frame(8.0)  # long since drawn in
    assert dots == [] and 0 < arrows[0][2] < 1 and arrows[-1][2] == 1.0
    assert "filtrate" in travelling._arrowed("overview")  # keyed with an arrow in the legend

    air = moving("air", "travels")  # a short word, not a symbol: it goes as the bundle says
    assert air._travels("overview", air.timeline.beats[0].particles[0])
    for label, moves in (("salt", "crosses"), ("H⁺", "travels")):  # said to cross; an ion is always a particle
        arrows.clear(), dots.clear()
        crossing = moving(label, moves)
        crossing.frame(3.0)
        assert arrows == [] and dots and not crossing._travels("overview", crossing.timeline.beats[0].particles[0])


def test_with_no_word_from_the_bundle_a_route_that_bends_or_runs_along_a_part_travels(job, monkeypatch):
    from types import SimpleNamespace

    from studio.render import routing
    renderer = Renderer(job, flowing([flow_beat(0, 12, labels=FLOW, label="magma")]))
    flow = renderer.timeline.beats[0].particles[0]
    grid = np.zeros((1024 // routing.GRID + 1, 1792 // routing.GRID + 1), bool)

    def through(points, middle_cols, names=("Magma", "Volcano", "Lava")):
        cells = grid.copy()
        cells[:, middle_cols[0] // routing.GRID:middle_cols[1] // routing.GRID] = True
        monkeypatch.setattr(renderer, "_path_points", lambda *a, **k: points)
        monkeypatch.setattr(renderer, "_bends", lambda *a, **k: [None] * (len(points) - 1))
        monkeypatch.setattr(renderer, "_path_stops", lambda *a, **k: [(pt, SimpleNamespace(name=n))
                                                                      for pt, n in zip(points, names)])
        monkeypatch.setattr(renderer, "scene", lambda scene_id: SimpleNamespace(cells=lambda name: cells))
        return renderer._journey("overview", flow)

    level = [(100.0, 500.0), (700.0, 500.0), (1300.0, 500.0)]
    assert through(level, (300, 1100))  # 1,200 px inside the volcano: up through it, a journey
    assert not through(level, (690, 712))  # through a thin wall: a crossing
    assert through([(100.0, 500.0), (700.0, 500.0), (100.0, 540.0)], (690, 712))  # out and nearly back: it bends
    assert not through([(100.0, 500.0), (700.0, 500.0), (100.0, 500.0)], (300, 1100),
                       ("Lumen", "Pump", "Lumen"))  # out through a part and back: it crosses the part


def test_a_value_the_camera_leaves_out_is_not_written_over_the_band(job, monkeypatch):
    from studio.schemas import Written
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    on = [i for i, b in enumerate(timeline.beats) if b.scene == "overview"]
    beats = list(timeline.beats)
    inside, edge = (Written(text="$1,210", at=Point(x=900, y=400), since=0.0),
                    Written(text="Year 0", at=Point(x=900, y=1020), side="below", since=0.0))  # below the picture
    beats[on[0]] = beats[on[0]].model_copy(update={"values": [inside, edge]})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    drawn = []
    monkeypatch.setattr(p, "draw_value", lambda canvas, text, rect, *a, **k: drawn.append((text, rect)))
    renderer.frame(beats[on[0]].t1 - 0.01)
    view = renderer.view("overview")
    assert [text for text, _ in drawn] == ["$1,210"]
    assert all(view[0] <= r[0] and view[1] <= r[1] and r[2] <= view[2] and r[3] <= view[3] for _, r in drawn)


def test_labels_keep_clear_of_a_value_before_it_is_said_and_one_said_before_the_cut_is_settled(job, monkeypatch):
    from studio.schemas import Written
    timeline = jobs.read(job / media.timeline_path("song"), Timeline)
    on = [i for i, b in enumerate(timeline.beats) if b.scene == "overview"]
    beats = list(timeline.beats)
    early = Written(text="$1,000", at=Point(x=700, y=400), since=-3.0)  # said before the hook cut began
    later = Written(text="$1,331", at=Point(x=1100, y=300), since=beats[on[-1]].t0 + 0.5)  # said on the last line
    beats[on[0]] = beats[on[0]].model_copy(update={"values": [early]})
    beats[on[-1]] = beats[on[-1]].model_copy(update={"values": [early, later]})
    renderer = Renderer(job, timeline.model_copy(update={"beats": beats}))
    t = beats[on[0]].t0 + 0.01
    cam = renderer.plans[on[0]].cam(t, renderer.view("overview"), renderer.scene("overview"))
    points = renderer._value_points("overview", cam, renderer.view("overview"))
    x, y = renderer.art_to_frame(t, 1100, 300)
    assert len(points) == 18 and any(abs(px - x) < 60 and py < y for px, py in points)  # the later one's place too
    drawn = []
    monkeypatch.setattr(p, "draw_value", lambda canvas, text, rect, size, paper, alpha, tick, pop=0.0:
                        drawn.append((text, alpha, pop)))
    renderer.frame(t)
    [(text, alpha, pop)] = drawn
    assert text == "$1,000" and alpha == pytest.approx(1.0, abs=0.02) and pop == 0.0  # on, settled: not said now
