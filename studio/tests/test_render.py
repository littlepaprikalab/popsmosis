"""The renderer on the sample job: a few frames, and a two-second video."""

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

skia = pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")

from studio import jobs, media
from studio.render import primitives as p
from studio.render.encode import EncodeError, audio_filter, encode, poster_time, ranges, write_poster
from studio.render.renderer import Renderer, RenderError, SceneArt
from studio.schemas import (
    AudioClip,
    Beat,
    FrqSegment,
    FrqStep,
    Locations,
    Particles,
    Reveal,
    Timeline,
    TimelineCamera,
    Timing,
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
    frq = FrqSegment(t0=5.0, t1=6.0, scene="overview", labels=["Thylakoid"], prompt="Explain it.",
                     predict_question="Which pumps?", predict_t=5.1, predict_pause_s=0.4,
                     steps=[FrqStep(t=5.5, text="b6f pumps.", spotlight=None)])
    timeline = Timeline(video="song", duration=6.0, fps=30, audio=[], beats=[beat], frq=frq,
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
    """A song of these beats on the overview, then a short exam question."""
    end = beats[-1].t1
    frq = FrqSegment(t0=end, t1=end + 1, scene="overview", labels=["Thylakoid"], prompt="Explain it.",
                     predict_question="Which pumps?", predict_t=end + 0.1, predict_pause_s=0.4,
                     steps=[FrqStep(t=end + 0.5, text="b6f pumps.", spotlight=None)])
    return Timeline(video="song", duration=end + 1, fps=30, audio=[], beats=beats, frq=frq, palette=PALETTE)


def flow_beat(t0, t1, path=FLOW, labels=(), reveal=()):
    particles = [Particles(path=list(path), color="#F2C230", trail=True)] if path else []
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
    assert (plans[0].flow_in, plans[0].flow_out) == (2.0, 8.0)  # from the reveal of its last stop
    assert (plans[1].flow_in, plans[1].flow_out) == (2.0, 8.0)
    assert (plans[3].flow_in, plans[3].flow_out) == (10.0, 12.0)


def test_particles_travel_between_the_structures_and_fade_in(job):
    flow = Renderer(job, flowing([flow_beat(0, 8)]))  # no labels, so nothing is drawn over the particle
    still = Renderer(job, flowing([flow_beat(0, 8, path=[])]))
    anchors = [(a.x, a.y) for a in (located(job, "overview", name).label_anchor for name in FLOW)]
    stops = renderer_module.stop_points(anchors)
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


def test_label_text_is_dark_enough_to_read_on_white():
    for color in ("#F2C230", "#2E8B57", "#E07A2F", "#3366CC", "#FFFFFF"):
        r, g, b = p.rgb(p.darken(color))
        assert 1.05 / (p._luminance(r, g, b) + 0.05) >= 4.5


def test_the_exam_question_counts_down_then_shows_its_steps(song):
    frq = song.timeline.frq
    countdown = song.frame(frq.predict_t + 0.5).copy()
    ring = p.rgb("#F59E0B")
    amber = ((countdown[..., 2] == ring[0]) & (countdown[..., 1] == ring[1]) & (countdown[..., 0] == ring[2])).sum()
    assert amber > 2000
    after = song.frame(frq.steps[0].t + 1.0)
    orange = p.rgb("#E07A2F")  # step 1's number circle, in its spotlight's colour
    near = (abs(after[..., 2].astype(int) - orange[0]) < 4) & (abs(after[..., 1].astype(int) - orange[1]) < 4)
    assert near[:, :800].sum() > 500


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
    """Two seconds: a line on the hook, then the exam question with a short pause."""
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
    frq = FrqSegment(t0=1.0, t1=2.0, scene="overview", labels=["Thylakoid"], prompt="Explain it.", predict_question="Which pumps?",
                     predict_t=1.1, predict_pause_s=0.4, steps=[FrqStep(t=1.5, text="b6f pumps.", spotlight=None)])
    return Timeline(video=video, duration=2.0, fps=30,
                    audio=[AudioClip(path="audio/tone.mp3", at=0, end=1.0),
                           AudioClip(path="audio/tone.mp3", at=1.5, start=1.0)],
                    palette={"Thylakoid": "#2E8B57", "Cytochrome b6f": "#E07A2F", "ATP synthase": "#3366CC"},
                    beats=[beat], frq=frq)


def test_a_clip_that_starts_partway_into_music_fades_in(job):
    timeline = short_timeline(job, "song")
    timeline.audio[0] = AudioClip(path="audio/tone.mp3", at=0, start=0.5, fade_in=0.5)
    _, mix = audio_filter(job, timeline)
    first, second = mix.split(";")[:2]
    assert "atrim=start=0.500" in first and "afade=t=in:st=0:d=0.500" in first
    assert "afade" not in second  # the exam question's answer starts as it was read


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
