import pytest

from studio import jobs, media
from studio.media import StepFailed
from studio.schemas import Bundle, Locations, Timeline, Timing, timeline_problems
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
    # The overview has not heard "thylakoid" yet, so nothing is revealed or labelled there.
    assert song["v1-2"].scene == "overview" and song["v1-2"].labels == []


def test_names_match_whatever_the_case_punctuation_or_alias_and_longer_names_win(job):
    data = sample_media.bundle().model_dump()
    data["scenes"][1]["structures"][1]["aliases"] = ["b6f"]
    data["scenes"][2]["structures"][0]["aliases"] = ["synthase"]
    for lyric in data["lyrics"]:
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
    assert all(s.spotlight != "Cytochrome b6f" for s in timelines["song"].frq.steps)
    assert "Cytochrome b6f" not in timelines["song"].frq.labels


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


def test_instrumental_gaps_get_beats_of_their_own(job):
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    song = timed["song"].model_dump()
    for i, line in enumerate(song["lines"]):  # a 3 s intro, and 2 s more before each chorus
        shift = 3.0 + 2.0 * sum(1 for earlier in song["lines"][1:i + 1] if earlier["line_id"].startswith("c"))
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
    assert timeline.frq.t0 == pytest.approx(timed["song"].duration)
    # Beats meet exactly, from 0 to the exam question, so every frame has one.
    ends = [0.0] + [b.t1 for b in timeline.beats]
    assert [b.t0 for b in timeline.beats] == ends[:-1] and ends[-1] == timeline.frq.t0


def test_the_exam_question_pauses_to_predict_then_spotlights_each_step(job):
    timelines = make(job)
    bundle = sample_media.bundle()
    frq_timing = sample_media.timings(bundle)["frq"]
    asked = frq_timing.line("frq-predict").end
    for video, timeline in timelines.items():
        frq = timeline.frq
        assert frq.t0 == timeline.beats[-1].t1
        assert frq.predict_t == pytest.approx(frq.t0 + asked)
        assert frq.predict_pause_s == media.PREDICT_PAUSE_S
        answer_at = frq.predict_t + media.PREDICT_PAUSE_S
        assert [(c.path, c.at, c.start, c.end) for c in timeline.audio[1:]] == [
            ("audio/frq.mp3", frq.t0, 0, asked), ("audio/frq.mp3", pytest.approx(answer_at), asked, None)]
        first_step = frq_timing.line("frq-step-1").start
        assert frq.steps[0].t == pytest.approx(answer_at + first_step - asked)
        # Behind the card: the scene shown in this video that can spotlight the most steps.
        # The song shows the overview ("Cytochrome b6f pumps protons."); the lesson never does.
        expected = {"song": ("overview", ["Cytochrome b6f", None]), "lesson": ("synthase", [None, "ATP synthase"])}
        assert (frq.scene, [s.spotlight for s in frq.steps]) == expected[video]
        # The card reviews the whole diagram: what the video revealed there first, then the rest.
        card = {"song": ["Thylakoid", "Cytochrome b6f"], "lesson": ["ATP synthase"]}
        assert frq.labels == card[video]
        assert timeline.duration == pytest.approx(frq.t1)


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
