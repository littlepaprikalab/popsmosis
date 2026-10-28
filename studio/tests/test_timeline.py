import os

import pytest

from studio import jobs, media
from studio.media import StepFailed
from studio.schemas import AudioClip, Bundle, Locations, Timeline, Timing, timeline_problems
from studio.steps import timeline as step
from studio.tests import sample_media
from studio.tests.sample import bundle_data


@pytest.fixture
def job(tmp_path):
    return sample_media.write_job(tmp_path / "sample")


def lines(timeline):
    return {b.line_id: b for b in timeline.beats if b.line_id}


def write_bundle(job, data, timed=None):
    """Write a changed bundle and timings to match its lines (sample_media's even spacing, unless given)."""
    bundle = Bundle.model_validate(data)
    jobs.write(job / "bundle.json", bundle)
    for kind, timing in (timed or sample_media.timings(bundle)).items():
        jobs.write(job / media.timing_path(kind), timing)
    return bundle


def direct(data, line_id, **changes):
    d = next(d for d in data["direction"] if d["line_id"] == line_id)
    d.update(changes)


def make(job):
    step.run(job)
    return {video: jobs.read(job / media.timeline_path(video), Timeline) for video in media.VIDEOS}


def test_the_sample_job_gives_two_valid_timelines_that_open_on_the_hook(job):
    assert not step.is_done(job / "missing")
    timelines = make(job)
    assert step.is_done(job)
    # New timings (or art, or a bundle) make the timelines out of date.
    timing = job / media.timing_path("song")
    os.utime(timing, (timing.stat().st_mtime + 10,) * 2)
    assert not step.is_done(job)
    locations = jobs.read(job / media.LOCATIONS, Locations)
    for timeline in timelines.values():
        assert timeline_problems(timeline, locations) == []
        assert timeline.beats[0].scene == "hook"
        assert timeline.beats[0].t0 == 0
    song = lines(timelines["song"])
    # "Light lands on the thylakoid": the thylakoid reveals on its word, not before.
    word = next(w for w in song["v1-1"].words if w.word == "thylakoid")
    assert [(r.name, r.t) for r in song["v1-1"].reveal] == [("Thylakoid", word.start)]
    assert song["v1-1"].labels == ["Thylakoid"]
    # The overview draws the thylakoid, named on the hook: its label stays on, from the overview's first line.
    assert song["v1-2"].scene == "overview" and song["v1-2"].labels == ["Thylakoid"]
    assert [(r.name, r.t) for r in song["v1-2"].reveal] == [("Thylakoid", song["v1-2"].t0)]


def test_names_match_whatever_the_case_punctuation_or_alias_and_longer_names_win(job):
    data = sample_media.bundle().model_dump()
    data["scenes"][1]["structures"][1]["aliases"] = ["b6f"]
    data["scenes"][2]["structures"][0]["aliases"] = ["synthase"]
    for lyric in data["lyrics"]:
        if lyric["id"] == "v1-1":
            lyric.update(text="Light lands on the leaf", names=[])  # so the overview first hears the thylakoid
        if lyric["id"] == "v2-1":
            lyric["text"] = "Only B6F, the pump, pushes thylakoids full"  # an alias in capitals; a plural
        if lyric["id"] == "b-1":
            lyric["text"] = "Feel the ATP-synthase spin"  # two words, hyphenated
    direct(data, "b-1", scene="synthase")
    write_bundle(job, data)
    song = lines(make(job)["song"])
    reveals = {r.name: r.t for r in song["v2-1"].reveal}
    words = {w.word: w.start for w in song["v2-1"].words}
    assert reveals == {"Cytochrome b6f": words["B6F"], "Thylakoid": words["thylakoids"]}
    assert song["v2-1"].labels == ["Cytochrome b6f", "Thylakoid"]  # in the order they are heard
    # "ATP-synthase" is the whole name, so it reveals once, at "ATP", not again at its alias.
    assert [(r.name, r.t) for r in song["b-1"].reveal] == [("ATP synthase", song["b-1"].words[2].start)]


def test_names_must_be_whole_words():
    names = step.Names([(("cortex",), "Cortex"), (("motor", "cortex"), "Motor cortex")])
    names.entries.sort(key=lambda e: len(e[0]), reverse=True)
    assert names.heard(["The", "motor", "cortex", "and", "the", "cortex"]) == {"Motor cortex": 1, "Cortex": 5}
    assert names.heard(["corticospinal", "Cortexes"]) == {"Cortex": 1}
    assert names.heard(["vortex"]) == {}


def test_spotlights_and_targets_fall_back_until_their_structure_is_heard_on_the_scene(job):
    data = sample_media.bundle().model_dump()
    camera = {"target": "Cytochrome b6f", "zoom": 9.0}
    direct(data, "v1-2", spotlight="Cytochrome b6f", camera=camera)  # not named yet
    direct(data, "c1-1", spotlight="ATP synthase", camera={"target": "ATP synthase", "zoom": 2})  # not on the scene
    for lyric in data["lyrics"]:
        if lyric["id"] == "v2-1":
            lyric["text"] = "Only cytochrome b6f pumps them in"
    direct(data, "v2-1", spotlight="cytochrome B6F", camera=camera)  # named in this line, in other case
    direct(data, "c2-1", spotlight="Cytochrome b6f", camera={"target": "Cytochrome b6f", "zoom": 0.5})
    write_bundle(job, data)
    song = lines(make(job)["song"])
    for line_id in ("v1-2", "c1-1"):
        assert song[line_id].spotlight is None
        assert (song[line_id].camera.target, song[line_id].camera.zoom) == (None, 1.0)
    assert song["v2-1"].spotlight == "Cytochrome b6f"
    assert (song["v2-1"].camera.target, song["v2-1"].camera.zoom) == ("Cytochrome b6f", step.MAX_ZOOM)
    assert song["c2-1"].camera.zoom == 1.0  # never wider than the whole scene


def test_a_structure_the_art_could_not_locate_is_never_revealed_or_spotlit(job):
    locations = jobs.read(job / media.LOCATIONS, Locations).model_dump()
    overview = next(s for s in locations["scenes"] if s["scene"] == "overview")
    overview["structures"] = [s for s in overview["structures"] if s["name"] != "Cytochrome b6f"]
    jobs.write(job / media.LOCATIONS, Locations.model_validate(locations))
    data = sample_media.bundle().model_dump()
    for lyric in data["lyrics"]:
        if lyric["id"] == "v2-1":
            lyric["text"] = "Only cytochrome b6f pumps them in"
    direct(data, "v2-1", spotlight="Cytochrome b6f", camera={"target": "Cytochrome b6f", "zoom": 2.0})
    write_bundle(job, data)
    timelines = make(job)
    song = lines(timelines["song"])
    assert song["v2-1"].reveal == [] and song["v2-1"].spotlight is None and song["v2-1"].camera.target is None
    assert all("Cytochrome b6f" not in b.labels for b in timelines["song"].beats)


def test_both_videos_open_on_the_hook_even_when_the_direction_does_not(job):
    data = bundle_data()  # the plain sample: every line over the overview
    write_bundle(job, data)
    for timeline in make(job).values():
        assert timeline.beats[0].scene == "hook"
        assert timeline.beats[1].scene != "hook"


def test_at_most_one_caption_per_section_held_for_four_seconds(job):
    data = sample_media.bundle().model_dump()
    for d in data["direction"]:
        d["caption"] = f"caption {d['line_id']}"
    bundle = Bundle.model_validate(data)
    timed = sample_media.timings(bundle)
    # Short lines, so a caption has to carry over to the next line to be held 4 s.
    timed["song"] = sample_media._timing("song", [(line.id, line.text) for line in bundle.lyrics], 2.5)
    write_bundle(job, data, timed)
    timelines = make(job)
    song = timelines["song"]
    shown = [b.caption for b in song.beats]
    assert shown == ["caption v1-1", "caption v1-1", "caption c1-1", "caption c1-1", "caption c2-1",
                     "caption c2-1", "caption c3-1"]
    for caption in set(shown):
        held = [b for b in song.beats if b.caption == caption]
        assert held[-1].t1 - held[0].t0 >= step.CAPTION_HOLD_S or held[-1] is song.beats[-1]
    lesson = lines(timelines["lesson"])
    # The narration has no sections: n1's caption is held its 4 s, and n2's comes too soon after it.
    assert lesson["n1"].caption == "caption n1" and lesson["n2"].caption is None


def test_particles_come_from_the_direction(job):
    data = sample_media.bundle().model_dump()
    for lyric in data["lyrics"]:
        if lyric["id"] == "v1-2":
            lyric["text"] = "From the thylakoid to b6f"
    data["scenes"][1]["structures"][1]["aliases"] = ["b6f"]
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f", "Cytochrome b6f"])
    direct(data, "c1-1", particles=["Thylakoid", "ATP synthase"])  # not on the overview: too few stops left
    write_bundle(job, data)
    song = lines(make(job)["song"])
    assert [(p.path, p.trail) for p in song["v1-2"].particles] == [(["Thylakoid", "Cytochrome b6f"], True)]
    assert song["c1-1"].particles == []


def test_the_particles_are_marked_with_what_they_carry_each_kind_in_its_colour(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="e-")
    direct(data, "v2-1", particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
    direct(data, "b-1", particles=["Thylakoid", "Cytochrome b6f"], carries="")
    write_bundle(job, data)
    song = lines(make(job)["song"])
    marked = {line: [(f.label, f.color) for f in song[line].particles] for line in ("v1-2", "v2-1", "b-1")}
    assert marked == {"v1-2": [("e⁻", "#4CC9F0")], "v2-1": [("H⁺", "#F2C230")], "b-1": [("", step.PARTICLE_COLOR)]}


def test_a_space_fills_while_particles_arrive_and_empties_while_they_leave(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="H+")  # into the space
    direct(data, "v2-1", particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺")  # out of it
    direct(data, "b-1", particles=["Thylakoid", "Cytochrome b6f"], carries="e-")  # something else
    data["builds_up"] = [{"carries": "H+", "region": "Cytochrome b6f"}]
    write_bundle(job, data)
    song = make(job)["song"]
    level = step.CROWD_START
    for beat in song.beats:
        crowds = beat.crowds
        if beat.scene != "overview":  # the hook does not draw cytochrome b6f
            assert crowds == []
            continue
        [crowd] = crowds
        assert (crowd.region, crowd.label, crowd.color) == ("Cytochrome b6f", "H⁺", "#F2C230")
        assert crowd.start == pytest.approx(level, abs=1e-3)  # it runs on from where it was
        rise = (crowd.end - crowd.start) / (beat.t1 - beat.t0)
        if beat.line_id == "v1-2":
            assert rise == pytest.approx(1 / step.FILL_S, rel=0.01)
        elif beat.line_id == "v2-1":
            assert rise < 0 and crowd.end >= step.CROWD_MIN
        else:
            assert rise == 0
        level = crowd.end


def test_what_particles_carry_is_written_as_a_textbook_prints_it():
    printed = {"H+": "H⁺", "e-": "e⁻", "O2": "O₂", "H2O": "H₂O", "CO2": "CO₂", "Ca2+": "Ca²⁺", "Cl-": "Cl⁻",
               "NAD+": "NAD⁺", "2H+": "2H⁺", "H +": "H⁺", "H⁺": "H⁺", "HCO₃⁻": "HCO₃⁻", "glucose": "glucose", "": ""}
    assert {text: step.notation(text) for text in printed} == printed
    # Another kind keeps one colour of its own, whatever the lesson.
    assert step.marker_colour("pyruvate") == step.marker_colour("pyruvate") in step.OTHER_MARKERS


def test_instrumental_gaps_get_beats_of_their_own(job):
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    song = timed["song"].model_dump()
    for i, line in enumerate(song["lines"]):  # a 1.8 s intro (kept: under INTRO_MAX_S), 2 s more before each chorus
        shift = 1.8 + 2.0 * sum(1 for earlier in song["lines"][1:i + 1] if earlier["line_id"].startswith("c"))
        line["start"] += shift
        line["end"] += shift
        for w in line["words"]:
            w["start"] += shift
            w["end"] += shift
    song["duration"] = song["lines"][-1]["end"] + 4.0
    timed["song"] = Timing.model_validate(song)
    write_bundle(job, bundle.model_dump(), timed)
    timeline = make(job)["song"]
    gaps = [b for b in timeline.beats if b.line_id is None]
    assert len(gaps) == 1 + 3 + 1  # the intro, before each chorus, the outro
    assert gaps[0].t0 == 0 and gaps[0].scene == "hook" and gaps[0].line == "" and gaps[0].words == []
    after = timeline.beats.index(gaps[1]) - 1
    assert gaps[1].scene == timeline.beats[after].scene and gaps[1].camera == timeline.beats[after].camera
    assert timeline.duration == pytest.approx(timed["song"].duration + step.TAIL_S)
    # Beats meet exactly, from 0 to the end, so every frame has one.
    ends = [0.0] + [b.t1 for b in timeline.beats]
    assert [b.t0 for b in timeline.beats] == ends[:-1] and ends[-1] == timeline.duration


def test_a_long_intro_is_cut_so_the_hook_is_sung_within_two_seconds(job):
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    song = timed["song"].model_dump()
    for line in song["lines"]:  # Lyria's 10 s intro
        line["start"] += 10.0
        line["end"] += 10.0
        for w in line["words"]:
            w["start"] += 10.0
            w["end"] += 10.0
    song["duration"] += 10.0
    timed["song"] = Timing.model_validate(song)
    write_bundle(job, bundle.model_dump(), timed)
    timelines = make(job)
    timeline, first = timelines["song"], timed["song"].lines[0]
    skip = round(first.start - step.INTRO_KEEP_S, 3)
    assert timeline.audio[0] == AudioClip(path="audio/song.mp3", at=0, start=skip, fade_in=step.INTRO_FADE_S)
    assert timeline.beats[0].line_id == first.line_id  # no intro beat: the hook is sung at once
    assert timeline.beats[0].words[0].start == pytest.approx(step.INTRO_KEEP_S)
    assert timeline.duration == pytest.approx(timed["song"].duration - skip + step.TAIL_S)
    assert timelines["lesson"].audio[0] == AudioClip(path="audio/narration.mp3", at=0)  # the narration is not cut
    assert timeline_problems(timeline, jobs.read(job / media.LOCATIONS, Locations)) == []


def test_each_video_ends_a_moment_after_its_voice_on_its_last_picture(job):
    timelines = make(job)
    timed = sample_media.timings(sample_media.bundle())
    for video, timeline in timelines.items():
        voice = timed["song" if video == "song" else "narration"]
        assert timeline.duration == pytest.approx(voice.duration + step.TAIL_S)
        assert timeline.beats[-1].t1 == timeline.duration  # the last beat holds its picture to the end
        assert timeline.audio == [AudioClip(path=voice.audio, at=0)]


def test_timings_that_do_not_match_the_bundle_fail_the_step(job):
    data = sample_media.bundle().model_dump()
    data["lyrics"][0]["id"] = "v1-0"
    data["direction"][0]["line_id"] = "v1-0"
    jobs.write(job / "bundle.json", Bundle.model_validate(data))
    with pytest.raises(StepFailed, match="v1-0 is not timed"):
        step.run(job)
    assert not step.is_done(job / "nothing")


def test_the_step_is_found_by_name():
    assert media.step_module("timeline") is step


def test_lines_the_aligner_put_at_one_instant_still_make_a_timeline(job):
    timed = sample_media.timings(sample_media.bundle())
    song = timed["song"].model_dump()
    at = song["lines"][1]["start"]
    for line in song["lines"][1:4]:  # three lines it could not place, all where the one before ended
        line["start"] = line["end"] = at
        for w in line["words"]:
            w["start"] = w["end"] = at
    timed["song"] = Timing.model_validate(song)
    write_bundle(job, sample_media.bundle().model_dump(), timed)
    timeline = make(job)["song"]
    beats = lines(timeline)
    assert [beats[i].t0 for i in ("v1-2", "c1-1", "v2-1")] == sorted({beats[i].t0 for i in ("v1-2", "c1-1", "v2-1")})
    assert all(b.t0 <= w.start <= w.end <= b.t1 for b in timeline.beats for w in b.words)
