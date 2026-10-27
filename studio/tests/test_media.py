import copy
import json
import zlib

import pytest
from pydantic import ValidationError

from studio import jobs, media
from studio.__main__ import main
from studio.schemas import (Box, Locations, Timeline, Timing, locations_problems, timeline_problems,
                            timing_problems)
from studio.tests import sample_media


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    return sample_media.write_job(tmp_path_factory.mktemp("jobs") / "sample", art=True)


def read_png(path):
    """Width, height and rows of the PNGs sample_media writes (8-bit, filter 0)."""
    data = path.read_bytes()
    width, height, _, color_type = int.from_bytes(data[16:20]), int.from_bytes(data[20:24]), data[24], data[25]
    channels = {0: 1, 2: 3}[color_type]
    raw, i = b"", 8
    while i < len(data):
        size, kind = int.from_bytes(data[i:i + 4]), data[i + 4:i + 8]
        if kind == b"IDAT":
            raw += data[i + 8:i + 8 + size]
        i += size + 12
    pixels = zlib.decompress(raw)
    stride = width * channels + 1
    return width, height, [pixels[y * stride + 1:(y + 1) * stride] for y in range(height)]


def test_the_sample_job_validates_and_its_parts_agree(job):
    bundle = sample_media.bundle()
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert locations_problems(locations, bundle) == []
    timed = {kind: jobs.read(job / media.timing_path(kind), Timing) for kind in media.TIMED}
    assert timing_problems(timed["song"], [line.id for line in bundle.lyrics]) == []
    assert timing_problems(timed["narration"], [n.id for n in bundle.narration]) == []
    assert timing_problems(timed["frq"], [i for i, _ in media.frq_lines(bundle)]) == []
    for video in media.VIDEOS:
        timeline = jobs.read(job / media.timeline_path(video), Timeline)
        assert timeline_problems(timeline, locations) == []
        assert timeline.beats[0].scene == "hook"


def test_the_sample_masks_match_the_art_and_the_locations(job):
    locations = jobs.read(job / media.LOCATIONS, Locations)
    for scene in locations.scenes:
        width, height, _ = read_png(job / scene.art)
        assert (width, height) == (media.ART_WIDTH, media.ART_HEIGHT)
        for s in scene.structures:
            _, _, rows = read_png(job / s.mask)
            assert rows[int(s.label_anchor.y)][int(s.label_anchor.x)] == 255, (scene.scene, s.name)
    # Cytochrome b6f covers the middle of the thylakoid on the overview, so that part is not in
    # its mask, and its centroid (where the camera aims) falls in the gap.
    _, _, thylakoid = read_png(job / media.mask_path("overview", "Thylakoid"))
    b6f = locations.scene("overview").get("Cytochrome b6f").centroid
    assert thylakoid[int(b6f.y)][int(b6f.x)] == 0


def test_frq_lines():
    bundle = sample_media.bundle()
    assert [i for i, _ in media.frq_lines(bundle)] == ["frq-prompt", "frq-predict", "frq-step-1", "frq-step-2"]


def test_names_the_model_wrote_become_safe_distinct_paths_the_api_takes():
    assert media.art_path("limbic-detail") == "art/limbic-detail.png"  # already a slug: kept as it is
    assert media.mask_path("overview", "Broca's area").startswith("masks/overview/broca-s-area-")
    paths = [media.art_path(s) for s in ("Brain Overview", "../etc", "", "Ω")]
    paths += [media.mask_path("overview", n) for n in ("α subunit", "β subunit", "Na+/K+ pump", "x" * 300)]
    assert all(media.SAFE_PATH.fullmatch(p) and len(p) <= 300 for p in paths), paths
    assert len(set(paths)) == len(paths)
    assert media.mask_path("overview", "α subunit") == media.mask_path("overview", "α subunit")


def test_restart_removes_a_steps_index_files(tmp_path):
    (tmp_path / "locations.json").write_text("{}")
    media.restart(tmp_path, "locations.json", "timeline_song.json")
    assert not (tmp_path / "locations.json").exists()


def song() -> dict:
    return sample_media.timeline("song").model_dump()


@pytest.mark.parametrize("break_it, message", [
    (lambda t: t["beats"][1].update(t0=t["beats"][1]["t0"] + 1), "not where the beat before it ends"),
    (lambda t: t["beats"][1].update(t0=t["beats"][1]["t0"] + 0.01), "not where the beat before it ends"),
    (lambda t: t["beats"][1]["camera"].update(zoom=0.5), "shows past it"),
    (lambda t: t["beats"][1].update(t1=float("nan")), "not a finite number"),
    (lambda t: t["frq"].update(labels=["Thylakoid"]), "which it does not label"),
    (lambda t: t["beats"][0].update(t0=0.5), "not 0"),
    (lambda t: t["beats"][0].update(labels=[]), "without labelling it"),
    (lambda t: t["beats"][1].update(spotlight="Cytochrome b6f"), "before it is revealed"),
    (lambda t: t["beats"][4].update(labels=["Thylakoid"]), "drops labels"),
    (lambda t: t["beats"][3]["reveal"][0].update(t=0.0), "outside the beat"),
    (lambda t: t["beats"][0]["words"][0].update(start=99.0, end=99.5), "outside the beat"),
    (lambda t: t["palette"].pop("Thylakoid"), "no colour in the palette"),
    (lambda t: t["frq"].update(t0=t["frq"]["t0"] + 2), "not where the exam question starts"),
    (lambda t: t["frq"]["steps"][0].update(t=t["frq"]["predict_t"] + 1), "follow the predict pause"),
    (lambda t: t["audio"][1].update(end=0.0), "audio clip"),
])
def test_a_broken_timeline_is_refused(break_it, message):
    data = song()
    break_it(data)
    with pytest.raises(ValidationError, match=message):
        Timeline.model_validate(data)


def test_every_frame_finds_its_beat_or_the_exam_question():
    timeline = sample_media.timeline("song")
    assert timeline.frame_count == round(timeline.duration * timeline.fps)
    held = [timeline.beat_at(n / timeline.fps) for n in range(timeline.frame_count)]
    assert all((beat is None) == (n / timeline.fps >= timeline.frq.t0) for n, beat in enumerate(held))
    second = timeline.beats[1]
    assert timeline.beat_at(second.t0) is second  # a beat holds its own start
    assert timeline.beat_at(second.t0 - 1e-9) is timeline.beats[0]


def test_paths_outside_the_job_or_that_the_api_refuses_are_refused():
    locations = sample_media.locations().model_dump()
    for bad in ("../art/hook.png", "art/brain overview.png", "/tmp/x.png"):
        broken = copy.deepcopy(locations)
        broken["scenes"][0]["art"] = bad
        with pytest.raises(ValidationError, match="not a path in the job folder"):
            Locations.model_validate(broken)


def test_a_timeline_may_only_name_what_its_scene_has_located():
    timeline = sample_media.timeline("song")
    locations = sample_media.locations().model_dump()
    overview = next(s for s in locations["scenes"] if s["scene"] == "overview")
    overview["structures"] = [s for s in overview["structures"] if s["name"] != "Cytochrome b6f"]
    problems = timeline_problems(timeline, Locations.model_validate(locations))
    assert problems and all("Cytochrome b6f" in p for p in problems)


def test_broken_timings_and_locations_are_refused():
    timing = sample_media.timings(sample_media.bundle())["song"].model_dump()
    overlapping = copy.deepcopy(timing)
    overlapping["lines"][1]["start"] = 1.0
    overlapping["lines"][1]["words"][0]["start"] = 1.0
    with pytest.raises(ValidationError, match="starts before the line ahead of it ends"):
        Timing.model_validate(overlapping)
    stray = copy.deepcopy(timing)
    stray["lines"][0]["words"][0].update(start=20.0, end=20.5)
    with pytest.raises(ValidationError, match="outside its line"):
        Timing.model_validate(stray)
    with pytest.raises(ValidationError, match="empty"):
        Box(x0=10, y0=10, x1=10, y1=20)
    locations = sample_media.locations().model_dump()
    locations["scenes"][0]["structures"][0]["label_anchor"] = {"x": 0, "y": 0}
    with pytest.raises(ValidationError, match="label_anchor outside its box"):
        Locations.model_validate(locations)
    assert timing_problems(Timing.model_validate(timing), ["v1-1"])[0].endswith("is not one of the lines")


def test_a_media_step_that_is_not_built_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    sample_media.write_job(tmp_path / "sample")
    for name in media.MEDIA_STEPS:
        try:
            media.step_module(name)
        except LookupError:
            assert main(["step", name, "sample"]) == 1
            assert f"the {name} step is not built yet" in capsys.readouterr().err
    with pytest.raises(LookupError):
        media.step_module("bundle")


def test_the_sample_job_round_trips_through_json(job):
    for video in media.VIDEOS:
        text = (job / media.timeline_path(video)).read_text(encoding="utf-8")
        assert Timeline.model_validate(json.loads(text)).model_dump_json(indent=2) + "\n" == text
