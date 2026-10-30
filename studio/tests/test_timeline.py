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
    # Not on the overview: too few stops left. Spotlit, so it is a line about one part, which runs only its own.
    direct(data, "c1-1", particles=["Thylakoid", "ATP synthase"], spotlight="Thylakoid")
    write_bundle(job, data)
    song = lines(make(job)["song"])
    assert [(p.path, p.trail) for p in song["v1-2"].particles] == [(["Thylakoid", "Cytochrome b6f"], True)]
    assert song["c1-1"].particles == []


def test_a_line_that_sums_up_runs_every_flow_its_scene_has_shown_so_far(monkeypatch):
    from studio.schemas import Particles, TimelineCamera
    from studio.steps.timeline import _run_all

    def flow(*path, label="H⁺", blocked=False):
        return Particles(path=list(path), color="#F2C230", trail=True, label=label, blocked=blocked)

    def beat(scene, *flows, spotlight=None, target=None):
        return {"scene": scene, "particles": list(flows), "spotlight": spotlight,
                "camera": TimelineCamera(target=target, zoom=1.6 if target else 1.0)}

    hand_off, chain = flow("PSII", "b6f", label="e⁻"), flow("PSII", "b6f", "PSI", label="e⁻")
    pump, back = flow("Stroma", "b6f", "Lumen"), flow("Lumen", "ATP synthase", "Stroma")
    beats = [beat("hook", flow("Sun", "Leaf")),
             beat("membrane", hand_off, spotlight="PSII"),
             beat("membrane", pump, spotlight="b6f", target="b6f"),
             beat("membrane", chain, spotlight="PSI"),
             beat("membrane", back),  # sums up: what the scene has shown runs beside its own
             beat("membrane", back, spotlight="ATP synthase", target="ATP synthase"),  # about one part: its own only
             beat("membrane"),  # sums up with nothing of its own
             beat("hook")]  # the hook never does
    _run_all(beats, "hook")
    moving = [[(f.path[0], f.path[-1], f.label) for f in b["particles"]] for b in beats]
    # The hand-off runs within the whole chain, which takes its place, rather than as a second dot on its leg.
    assert moving[4] == [("Lumen", "Stroma", "H⁺"), ("Stroma", "Lumen", "H⁺"), ("PSII", "PSI", "e⁻")]
    assert moving[5] == [("Lumen", "Stroma", "H⁺")]
    assert moving[6] == [("Stroma", "Lumen", "H⁺"), ("PSII", "PSI", "e⁻"), ("Lumen", "Stroma", "H⁺")]
    assert moving[7] == []
    assert [len(b["particles"]) for b in beats[:4]] == [1, 1, 1, 1]  # lines that teach a step move only that step

    # At most RUN_MAX at once, the beat's own first; and once a flow is blocked on a scene, it stays still.
    monkeypatch.setattr(step, "RUN_MAX", 2)
    beats = [beat("membrane", pump, spotlight="b6f"), beat("membrane", chain, spotlight="PSI"), beat("membrane", back),
             beat("membrane", flow("PSII", "Herbicide", label="e⁻", blocked=True)), beat("membrane")]
    _run_all(beats, "hook")
    moving = [[(f.path[0], f.path[-1]) for f in b["particles"]] for b in beats]
    assert moving[2] == [("Lumen", "Stroma"), ("Stroma", "Lumen")]
    assert moving[3] == [("PSII", "Herbicide")] and moving[4] == []


def test_the_particles_are_marked_with_what_they_carry_each_kind_in_its_colour(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="e-")
    direct(data, "v2-1", particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
    direct(data, "b-1", particles=["Thylakoid", "Cytochrome b6f"], carries="")
    direct(data, "c2-1", particles=["Thylakoid", "Cytochrome b6f"], carries="price")  # outside science, a plain word
    write_bundle(job, data)
    song = lines(make(job)["song"])
    # Each line's own flow, first (a line that sums up runs the scene's others after it).
    marked = {line: [(f.label, f.color) for f in song[line].particles[:1]] for line in ("v1-2", "v2-1", "b-1", "c2-1")}
    assert marked == {"v1-2": [("e⁻", "#4CC9F0")], "v2-1": [("H⁺", "#F2C230")], "b-1": [("", step.PARTICLE_COLOR)],
                      "c2-1": [("price", step.marker_colour("price"))]}
    assert step.marker_colour("price") in step.OTHER_MARKERS


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


def test_a_part_turns_while_particles_flow_into_it_and_eases_to_a_stop_after(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
    direct(data, "v2-1", spotlight="Thylakoid")  # a line about one part: nothing flows on it, so it slows to a stop
    data["spins"] = [{"structure": "Cytochrome b6f"}]
    write_bundle(job, data)
    song = make(job)["song"]
    before = None
    for beat in song.beats:
        if beat.scene != "overview":  # the hook does not draw cytochrome b6f
            assert beat.turning == []
            continue
        [turn] = beat.turning
        driven = any("Cytochrome b6f" in f.path[1:] for f in beat.particles)  # its own flow, or the scene running
        assert driven == (beat.line_id != "v2-1" and beat.t0 >= lines(song)["v1-2"].t0)
        assert turn.speed == (step.SPIN_SPEED if driven else 0.0)
        if before is not None:  # it runs on from where the beat before left it, never jumping
            turns, speed = before[0].at(before[1])
            assert turn.from_speed == pytest.approx(speed) and turn.turns == pytest.approx(turns % 1, abs=1e-5)
        before = (turn, beat.t1 - beat.t0)
    assert any(b.turning and b.turning[0].turns > 0 for b in song.beats)


def test_particles_that_end_at_a_blocker_are_blocked_and_a_glow_shows_once_named(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻", glow="Thylakoid")
    direct(data, "v2-1", particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺", glow="ATP synthase")  # not drawn here
    data["blocks"] = [{"structure": "Cytochrome b6f"}]
    write_bundle(job, data)
    song = lines(make(job)["song"])
    assert [(f.path, f.blocked) for f in song["v1-2"].particles] == [(["Thylakoid", "Cytochrome b6f"], True)]
    assert [f.blocked for f in song["v2-1"].particles] == [False]  # it ends at the thylakoid, which blocks nothing
    assert (song["v1-2"].glow, song["v2-1"].glow) == ("Thylakoid", None)


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


def test_a_line_the_song_never_sings_has_no_beat_and_the_intro_is_cut_to_the_first_sung_one(job):
    """Eleven Music left the first chunk instrumental: step 6 timed its line at no length where the chunk ends."""
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    song = timed["song"].model_dump()
    for line in song["lines"]:
        line["start"] += 5.0
        line["end"] += 5.0
        for w in line["words"]:
            w["start"] += 5.0
            w["end"] += 5.0
    song["duration"] += 5.0
    first, at = song["lines"][0], song["lines"][1]["start"]
    first.update(start=at, end=at, sung=False, words=[{**w, "start": at, "end": at} for w in first["words"]])
    timed["song"] = Timing.model_validate(song)
    write_bundle(job, bundle.model_dump(), timed)
    timeline = make(job)["song"]
    assert "v1-1" not in [beat.line_id for beat in timeline.beats]
    skip = round(at - step.INTRO_KEEP_S, 3)
    assert timeline.audio[0] == AudioClip(path="audio/song.mp3", at=0, start=skip, fade_in=step.INTRO_FADE_S)
    assert timeline.beats[0].line_id == "v1-2" and timeline.beats[0].scene == step.hook_scene(bundle)
    assert timeline.beats[0].words[0].start == pytest.approx(step.INTRO_KEEP_S)
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


def test_a_processs_stages_are_listed_for_the_renderer_to_fade_between(job):
    data = bundle_data()
    for scene in data["scenes"][1:]:
        scene["stage_of"] = "photosynthesis"
    write_bundle(job, data)
    timelines = make(job)
    assert [t.stages for t in timelines.values()] == [[["overview", "synthase"]]] * 2


def test_what_the_check_still_finds_wrong_in_a_muse_picture_is_disclosed_where_it_is_shown(job):
    import json

    from studio.steps import art

    bundle = jobs.read(job / "bundle.json", Bundle)
    synthase = next(s for s in bundle.scenes if s.id == "synthase")
    note = "The head of ATP synthase should face the stroma."
    (job / "art").mkdir(exist_ok=True)
    (job / media.art_path("synthase")).touch()  # Muse Image's picture, as far as the timeline step looks
    (job / "art" / "synthase.json").write_text(json.dumps(
        {"key": art.key(bundle, synthase), "model": art.painter_of(job).model, "check": {"notes": [note]}}))
    timelines = make(job)
    assert timelines["lesson"].notices == {"synthase": [note]}  # the lesson shows the close-up
    assert timelines["song"].notices == {}  # the song never does
    # Every picture is tagged AI-made, painted by Muse Image (the close-up) or not (the sample's others).
    assert timelines["lesson"].ai_pictures == ["hook", "synthase"]
    assert timelines["song"].ai_pictures == ["hook", "overview"]
    os.utime(job / "art" / "synthase.json")  # a new check makes the timelines out of date
    assert not step.is_done(job)


def test_a_path_stays_drawn_on_its_scene_from_when_its_particles_set_off_and_a_blocked_one_never_is():
    from studio.schemas import Particles, TimelineCamera
    from studio.steps.timeline import _route

    def beat(scene, t0, *flows):
        return {"scene": scene, "t0": t0, "particles": list(flows), "camera": TimelineCamera(target=None, zoom=1.0)}

    chain = Particles(path=["PSII", "b6f"], color="#33CCFF", trail=True, label="e⁻")
    stopped = Particles(path=["PSII", "Herbicide"], color="#33CCFF", trail=True, label="e⁻", blocked=True)
    beats = [beat("membrane", 0.0), beat("membrane", 2.0, chain), beat("membrane", 4.0),
             beat("hook", 6.0), beat("membrane", 8.0, stopped)]
    _route(beats)
    assert [[(r.path, r.since) for r in b["routes"]] for b in beats] == [
        [], [(["PSII", "b6f"], 2.0)], [(["PSII", "b6f"], 2.0)], [], [(["PSII", "b6f"], 2.0)]]


def test_the_hook_cut_is_the_pre_chorus_and_first_chorus_moved_to_start_at_zero(job):
    from studio import checks

    step.run(job)
    bundle = checks.load(job / "bundle.json")
    song = jobs.read(job / media.timeline_path("song"), Timeline)
    hook = step.hook_cut(bundle, song)
    if "chorus" not in [line.section for line in bundle.lyrics]:
        assert hook is None
        return
    assert hook.video == "hook" and hook.title == bundle.topic
    assert hook.beats[0].t0 == 0 and hook.beats[-1].t1 == hook.duration
    sung = [b.line_id for b in hook.beats if b.line_id]
    chorus = [line.id for line in bundle.lyrics if line.section == "chorus"]
    assert chorus[0] in sung  # the first chorus is in it...
    assert all(w.start >= 0 for b in hook.beats for w in b.words)  # ...moved to start at 0
    clip = hook.audio[0]
    assert clip.end - clip.start == pytest.approx(hook.duration, abs=0.01) and clip.fade_out > 0



def test_the_hook_cut_never_reaches_into_a_verse_the_song_never_sings(job):
    """A first chorus shorter than HOOK_MIN_S takes in the verse before it, but not one Eleven Music left
    instrumental: its lines have no beat (step 6)."""
    bundle = sample_media.bundle()
    timed = sample_media.timings(bundle)
    song = timed["song"].model_dump()
    at = song["lines"][2]["start"]  # verse 1 (v1-1, v1-2) not sung: timed where the chorus starts
    for line in song["lines"][:2]:
        line.update(start=at, end=at, sung=False, words=[{**w, "start": at, "end": at} for w in line["words"]])
    timed["song"] = Timing.model_validate(song)
    write_bundle(job, bundle.model_dump(), timed)
    make(job)
    hook = jobs.read(job / media.timeline_path(media.HOOK), Timeline)
    sung = [b.line_id for b in hook.beats if b.line_id]
    assert sung[0] == "c1-1" and not {"v1-1", "v1-2"} & set(sung)  # it opens on the chorus
    assert hook.beats[0].t0 == 0 and hook.beats[-1].t1 == hook.duration


def keyed(data):
    data["key_lines"] = [{"from_line": "v1-1", "text": "How does a leaf make fuel from light?", "from_narration": "n1"},
                         {"from_line": "v2-1", "text": "Light pumps H⁺ into the lumen", "from_narration": "n2"}]
    data["key_terms"] = ["ATP synthase", "lumen"]


def test_the_song_holds_each_key_line_until_the_next_and_the_core_idea_over_the_last_chorus(job):
    data = bundle_data()
    keyed(data)
    bundle = write_bundle(job, data)
    timelines = make(job)
    song = timelines["song"]
    shown = {b.line_id: b.key_line for b in song.beats if b.line_id}
    assert shown == {"v1-1": "How does a leaf make fuel from light?", "v1-2": "How does a leaf make fuel from light?",
                     "c1-1": "How does a leaf make fuel from light?", "v2-1": "Light pumps H⁺ into the lumen",
                     "c2-1": "Light pumps H⁺ into the lumen", "b-1": "Light pumps H⁺ into the lumen",
                     "c3-1": bundle.core_idea}
    for i, beat in enumerate(song.beats):  # a gap holds the line before it
        if not beat.line_id and i:
            assert beat.key_line == song.beats[i - 1].key_line
    assert song.key_terms == ["ATP synthase", "lumen"]
    lesson = {b.line_id: b.key_line for b in timelines["lesson"].beats if b.line_id}
    narration = [n.id for n in bundle.narration]
    assert lesson[narration[0]] == "How does a leaf make fuel from light?"
    assert lesson[narration[-1]] == bundle.core_idea  # the narration ends on the core idea
    assert all(lesson[n] == "Light pumps H⁺ into the lumen" for n in narration[1:-1])
    assert timelines["lesson"].key_terms == ["ATP synthase", "lumen"]
    hook = jobs.read(job / media.timeline_path(media.HOOK), Timeline)
    assert any(b.key_line for b in hook.beats) and hook.key_terms == song.key_terms


def test_a_song_from_a_bundle_without_key_lines_has_none(job):
    for timeline in make(job).values():
        assert all(b.key_line == "" for b in timeline.beats)  # not even the core idea: no band at all


def test_the_lesson_shows_the_core_idea_from_the_sentence_that_says_it():
    data = bundle_data()
    assert step._says_core_idea(Bundle.model_validate(data)) == len(data["narration"]) - 1  # none says it: the last
    data["narration"][0]["text"] = "So remember: " + data["core_idea"]
    assert step._says_core_idea(Bundle.model_validate(data)) == 0


def test_a_second_flow_moves_on_the_same_beat_in_its_own_colour(job):
    data = bundle_data()
    direct(data, "v2-1", particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺",
           also=[{"particles": ["Cytochrome b6f", "Thylakoid"], "carries": "O₂"}])
    write_bundle(job, data)
    beat = lines(make(job)["song"])["v2-1"]
    assert [(f.path, f.label) for f in beat.particles] == [(["Cytochrome b6f", "Thylakoid"], "H⁺"),
                                                           (["Cytochrome b6f", "Thylakoid"], "O₂")]
    assert beat.particles[0].color != beat.particles[1].color
    assert {r.label for r in beat.routes} >= {"H⁺", "O₂"}


def test_a_graphs_axis_names_go_to_the_videos_that_show_it(job):
    data = bundle_data()
    data["scenes"][2]["axes"] = {"x": "Time", "y": ""}  # only the lesson shows the synthase, and not the overview
    data["scenes"][1]["axes"] = {"x": "Quantity", "y": "Price"}
    write_bundle(job, data)
    timelines = make(job)
    assert {scene: (a.x, a.y) for scene, a in timelines["song"].axes.items()} == {"overview": ("Quantity", "Price")}
    assert {scene: (a.x, a.y) for scene, a in timelines["lesson"].axes.items()} == {"synthase": ("Time", "")}
    hook = jobs.read(job / media.timeline_path(media.HOOK), Timeline)
    assert hook.axes == timelines["song"].axes  # the hook cut is the song's, and shows the overview
