import os

import pytest

from studio import jobs, knobs, media
from studio.media import StepFailed
from studio.schemas import AudioClip, Bundle, Locations, TimedLine, Timeline, Timing, timeline_problems
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
        assert all(b.camera.from_whole for b in timeline.beats)  # every zoom counts from the whole scene's framing
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


def test_the_lesson_opens_on_the_hook_even_when_the_direction_does_not_and_the_song_on_its_first_lines_scene(job):
    data = bundle_data()  # the plain sample: every line over the overview
    write_bundle(job, data)
    timelines = make(job)
    assert timelines["lesson"].beats[0].scene == "hook" and timelines["lesson"].beats[1].scene != "hook"
    # The song's title line is the answer, not the lesson's question: it opens on the picture its first line sings.
    assert {b.scene for b in timelines["song"].beats} == {"overview"}


def test_the_song_opens_on_the_hook_when_its_first_lines_scene_is_not_drawn(job):
    data = bundle_data()
    direct(data, "v1-1", scene="synthase")
    write_bundle(job, data)
    locations = jobs.read(job / media.LOCATIONS, Locations)
    jobs.write(job / media.LOCATIONS, Locations(scenes=[s for s in locations.scenes if s.scene != "synthase"]))
    timelines = make(job)
    assert timelines["song"].beats[0].scene == "hook"


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
    # The hand-off runs within the whole chain, which takes its place, rather than as a second dot on its leg; at
    # most RUN_MAX (2) move, the beat's own first.
    assert moving[4] == [("Lumen", "Stroma", "H⁺"), ("PSII", "PSI", "e⁻")]
    assert moving[5] == [("Lumen", "Stroma", "H⁺")]
    assert moving[6] == [("Stroma", "Lumen", "H⁺"), ("PSII", "PSI", "e⁻")]
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
        if beat.scene != "overview":  # the hook does not draw cytochrome b6f (it does the thylakoid: a few there)
            assert all(c.thin for c in crowds)
            continue
        [crowd] = [c for c in crowds if not c.thin]
        [few] = [c for c in crowds if c.thin]  # where they come from and go: a few there, the same all along
        assert (few.region, few.label, few.start, few.end) == ("Thylakoid", "H⁺", step.THIN_SHARE, step.THIN_SHARE)
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



def test_a_held_difference_starts_full_and_only_dips_and_none_is_added_where_it_crowds_already(job):
    # Round 22's pump lesson filled the sodium outside the cell from a few dots only once the pump ran (Nov 1).
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")  # in
    direct(data, "v2-1", particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺")  # out: a leak
    data["builds_up"] = [{"carries": "H⁺", "region": "Cytochrome b6f", "held": True},
                         {"carries": "H⁺", "region": "Thylakoid"}]  # crowded at both ends: no thin side
    write_bundle(job, data)
    song = make(job)["song"]
    shown = [c for b in song.beats if b.scene == "overview" for c in b.crowds]
    assert not any(c.thin for c in shown)
    held = [c for c in shown if c.region == "Cytochrome b6f"]
    assert held[0].start == 1.0 and min(c.end for c in held) >= step.HELD_MIN
    assert all(c.held for c in held) and not any(c.held for c in shown if c.region == "Thylakoid")
    assert min(c.end for c in held) < 1.0  # the leak dips it
    built = [c for c in shown if c.region == "Thylakoid"]
    assert built[0].start == step.CROWD_START

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
    # The song opens on its first sung line's scene (the overview), not on its unsung first line's (the hook).
    assert timeline.beats[0].line_id == "v1-2" and timeline.beats[0].scene == "overview"
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


def test_the_song_holds_each_key_line_until_the_next_and_none_over_a_chorus(job):
    data = bundle_data()
    keyed(data)
    bundle = write_bundle(job, data)
    timelines = make(job)
    song = timelines["song"]
    shown = {b.line_id: b.key_line for b in song.beats if b.line_id}
    # Each chorus sings the core idea, written at the top of every frame: no step under it, until the next key line
    # (the bridge has none of its own).
    assert shown == {"v1-1": "How does a leaf make fuel from light?", "v1-2": "How does a leaf make fuel from light?",
                     "c1-1": "", "v2-1": "Light pumps H⁺ into the lumen", "c2-1": "", "b-1": "", "c3-1": ""}
    assert all(t.core_idea == " ".join(bundle.core_idea.split()) for t in timelines.values())
    for i, beat in enumerate(song.beats):  # a gap holds the line before it
        if not beat.line_id and i:
            assert beat.key_line == song.beats[i - 1].key_line
    assert song.key_terms == ["ATP synthase", "lumen"]
    lesson = {b.line_id: b.key_line for b in timelines["lesson"].beats if b.line_id}
    narration = [n.id for n in bundle.narration]
    assert lesson[narration[0]] == "How does a leaf make fuel from light?"
    assert lesson[narration[-1]] == ""  # the narration ends on the core idea, already at the top
    assert all(lesson[n] == "Light pumps H⁺ into the lumen" for n in narration[1:-1])
    assert timelines["lesson"].key_terms == ["ATP synthase", "lumen"]
    hook = jobs.read(job / media.timeline_path(media.HOOK), Timeline)
    assert hook.key_terms == song.key_terms and hook.core_idea == song.core_idea


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


def test_a_picture_shown_for_one_short_line_gives_way_to_the_one_before():
    # Round 18's kidney song cut to a scene for one two-second line six times.
    def line(line_id, start):
        return TimedLine(line_id=line_id, start=start, end=start + 1.8, words=[])

    sung = [line("a", 0), line("b", 2), line("c", 4), line("d", 6), line("e", 8), line("f", 10), line("g", 15)]
    scenes = {"a": "hook", "b": "overview", "c": "overview", "d": "detail", "e": "overview", "f": "detail",
              "g": "nowhere"}
    directions = {k: type("D", (), {"scene": v})() for k, v in scenes.items()}
    got = step._scenes(sung, directions, {"hook", "overview", "detail"}, "hook", 20.0)
    # d alone (2 s) holds the overview; f's run (10 to 15 s, then g, not drawn, stays on it) is long enough.
    assert got == ["hook", "overview", "overview", "overview", "overview", "detail", "detail"]


def test_the_hook_holds_still_whatever_its_direction_says(job, monkeypatch):
    # Round 20's rock cycle sent a dot of sand off its cliff in the opening picture; it read as a speck.
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"])
    write_bundle(job, data)
    assert lines(make(job)["song"])["v1-2"].particles  # on the overview, it runs
    monkeypatch.setattr(step, "hook_scene", lambda bundle: "overview")  # the same picture, opening the video
    timelines = make(job)
    assert lines(timelines["song"])["v1-2"].scene == "overview"
    for timeline in timelines.values():
        assert all(not b.particles for b in timeline.beats if b.scene == "overview")


def test_a_drawn_value_is_written_as_the_voice_says_it_and_a_tick_from_the_start():
    from types import SimpleNamespace

    from studio.schemas import Point, Value, Word
    from studio.steps.timeline import _write_values, says

    assert says("$1,210", "$1,210") and says("1,210", "$1,210") and says("12", "12 years")
    assert not says("10%", "$10") and not says("1,100", "$1,210")
    values = [Value(text="$1,210", at=Point(x=500, y=300)), Value(text="Year 2", at=Point(x=500, y=900), side="below",
                                                                     tick=True),
              Value(text="$1,331", at=Point(x=700, y=250)),  # never said: written from the start
              Value(text="Thylakoid", at=Point(x=300, y=250), label=True)]  # a name: the renderer's, with its label
    beats = [{"scene": "hook", "t0": 0.0, "t1": 4.0, "words": [Word(word="$1,210", start=1.0, end=1.5)]},
             {"scene": "overview", "t0": 4.0, "t1": 8.0, "words": [Word(word="So", start=4.2, end=4.4)]},
             {"scene": "overview", "t0": 8.0, "t1": 12.0, "words": [Word(word="reaches", start=8.5, end=9.0),
                                                                    Word(word="$1,210", start=9.2, end=9.9)]},
             {"scene": "overview", "t0": 12.0, "t1": 16.0, "words": []}]
    _write_values(beats, SimpleNamespace(scenes=[SimpleNamespace(scene="overview", values=values)]))
    assert beats[0]["values"] == []  # said on another scene: not there
    assert [(v.text, v.since, v.tick) for v in beats[1]["values"]] == [("Year 2", 4.0, True), ("$1,331", 4.0, False)]
    assert [(v.text, v.since) for v in beats[2]["values"]] == [("$1,210", 9.2), ("Year 2", 4.0), ("$1,331", 4.0)]
    assert [v.text for v in beats[3]["values"]] == ["$1,210", "Year 2", "$1,331"]  # and stays on


def test_how_a_flow_moves_is_kept_as_the_bundle_directs_it(job):
    data = sample_media.bundle().model_dump()
    direct(data, "v1-2", particles=["Thylakoid", "Cytochrome b6f"], carries="filtrate", moves="travels")
    direct(data, "v2-1", particles=["Cytochrome b6f", "Thylakoid"], carries="H⁺")  # written before moves: ""
    write_bundle(job, data)
    song = lines(make(job)["song"])
    assert [f.moves for f in song["v1-2"].particles] == ["travels"]
    assert [f.moves for f in song["v2-1"].particles if f.label == "H⁺"] == [""]


def test_what_moves_takes_the_colour_of_the_structure_it_names():
    from studio.steps.timeline import MARKER_COLOURS, marker_colour
    palette = {"Magma": "#E4572E", "Volcano": "#6B4F3A"}
    assert marker_colour("magma", palette) == "#E4572E"  # magma rises in magma's red
    assert marker_colour("H⁺", palette) == MARKER_COLOURS["H⁺"]  # a common kind keeps its own
    assert marker_colour("sediment", palette) == marker_colour("sediment")  # no such structure: by its name


def test_the_lesson_alone_has_only_the_lesson_timeline_and_no_hook_cut(job):
    """STUDIO_VIDEOS=lesson: the song is never timed, and a hook cut left from a run that made the song goes."""
    for path in (media.timing_path("song"), media.timeline_path("song")):
        (job / path).unlink()
    (job / media.timeline_path(media.HOOK)).write_text("{}")
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    line = step.run(job)
    assert line.startswith("lesson ") and "song" not in line and "hook" not in line
    assert step.is_done(job)
    lesson = jobs.read(job / media.timeline_path("lesson"), Timeline)
    assert timeline_problems(lesson, jobs.read(job / media.LOCATIONS, Locations)) == []
    assert not (job / media.timeline_path("song")).exists() and not (job / media.timeline_path(media.HOOK)).exists()
    knobs.use({"knobs": {}})
    assert not step.is_done(job)  # both videos again: the song is not timed


def test_on_a_long_hold_the_songs_camera_follows_the_part_each_line_names():
    # A whole picture held still for up to 32 s in the scorecard's songs, the camera only drifting. 3-second lines;
    # HOLD_S 6, SHOT_MIN_S 4, FOLLOW_MAX_S 12.
    from studio.schemas import Particles, TimelineCamera
    from studio.steps.timeline import _follow

    def beat(t0, scene, *named, spotlight=None, target=None, particles=(), line=True):
        return {"t0": t0, "scene": scene, "line_id": f"l{t0}" if line else None, "named": list(named),
                "labels": ["A", "B", "C"], "spotlight": spotlight, "particles": list(particles),
                "camera": TimelineCamera(target=target, zoom=1.8 if target else 1.0)}

    flow = Particles(path=["A", "B"], color="#F2C230", trail=True, label="H⁺")
    beats = [beat(0, "hook", "A"),  # the hook holds still
             beat(3, "overview", "A"),  # a cut: the whole picture
             beat(6, "overview", "A"),  # still only 3 s on it
             beat(9, "overview", "A"),  # 6 s of the whole picture: in on A
             beat(12, "overview", "B"),  # A has had 3 s: it stays
             beat(15, "overview", line=False),  # an instrumental gap holds the framing
             beat(18, "overview", "B"),  # A has had 9 s: on to B
             beat(21, "overview"),  # B has had 3 s: it stays, though nothing is named
             beat(24, "overview", "A"),  # following since 9 s, past FOLLOW_MAX_S: the whole picture again
             beat(27, "overview", "A", spotlight="B"),  # the whole picture for 3 s
             beat(30, "overview", "A", spotlight="B"),  # 6 s: in on what the line is about, its spotlight
             beat(33, "overview", "A", particles=[flow]),  # a flow is watched whole
             beat(36, "overview", "A", target="C"),  # the direction framed it: kept
             beat(39, "overview", "A"),
             beat(42, "overview", "A"),
             beat(45, "overview", "A"),  # 6 s of the whole picture since the direction's C
             beat(48, "detail", "A"),  # a cut: the whole picture, whatever came before
             beat(51, "hook", "A"), beat(54, "hook", "A"), beat(57, "hook", "A")]  # never on the hook
    _follow(beats, "hook")
    assert [(b["camera"].target, b["camera"].zoom) for b in beats] == [
        (None, 1.0), (None, 1.0), (None, 1.0), ("A", 1.4), ("A", 1.4), ("A", 1.4), ("B", 1.4), ("B", 1.4),
        (None, 1.0), (None, 1.0), ("B", 1.4), (None, 1.0), ("C", 1.8), (None, 1.0), (None, 1.0), ("A", 1.4),
        (None, 1.0), (None, 1.0), (None, 1.0), (None, 1.0)]


def test_the_song_follows_on_its_long_hold_and_the_lesson_keeps_its_direction(job):
    data = sample_media.bundle().model_dump()
    for lyric in data["lyrics"]:
        if lyric["id"] == "v2-1":
            lyric["text"] = "Only cytochrome b6f pumps them in"
    for line_id in ("v1-2", "c1-1", "v2-1", "c2-1"):  # 4.3-second lines on the overview, the camera on all of it
        direct(data, line_id, scene="overview", spotlight="", particles=[], also=[], glow="",
               camera={"target": "", "zoom": 1.0})
    write_bundle(job, data)
    timelines = make(job)
    locations = jobs.read(job / media.LOCATIONS, Locations)
    assert all(timeline_problems(t, locations) == [] for t in timelines.values())
    song = lines(timelines["song"])
    framed = {line_id: (song[line_id].camera.target, song[line_id].camera.zoom)
              for line_id in ("v1-2", "c1-1", "v2-1", "c2-1")}
    # 8.6 s into the overview, v2-1 names cytochrome b6f: the camera goes in on it, and once it has had its 4.3 s,
    # back to the whole picture for a chorus that names no part.
    assert framed == {"v1-2": (None, 1.0), "c1-1": (None, 1.0), "v2-1": ("Cytochrome b6f", step.FOLLOW_ZOOM),
                      "c2-1": (None, 1.0)}
    assert song["v2-1"].camera.from_whole  # 1.4 times closer than the whole scene, not the whole art
    directed = {d.line_id: d.camera.target or None for d in Bundle.model_validate(data).direction}
    for beat in timelines["lesson"].beats:
        if beat.line_id:
            assert beat.camera.target in (None, directed[beat.line_id])


def test_a_graph_parts_meaning_stays_with_its_name_from_when_it_is_first_named():
    from types import SimpleNamespace
    from studio.schemas import Meaning, Point, Reveal, Value
    from studio.steps.timeline import _mean

    names = [Value(text=n, at=Point(x=500, y=300), label=True) for n in ("Demand curve", "Supply curve", "Gap")]
    locations = SimpleNamespace(scenes=[SimpleNamespace(scene="graph", values=names),
                                        SimpleNamespace(scene="close", values=names[:1]),
                                        SimpleNamespace(scene="hook", values=[])])
    bundle = SimpleNamespace(meanings=[Meaning(structure="Demand curve", text="lower price → people buy more"),
                                       Meaning(structure="Supply curve", text="higher price → sellers offer more"),
                                       Meaning(structure="Gap", text="buyers want more than sellers have")])

    def beat(scene, t0, t1, *reveal):
        return {"scene": scene, "t0": t0, "t1": t1, "reveal": [Reveal(name=n, t=at) for n, at in reveal]}

    beats = [beat("graph", 0.0, 3.0), beat("graph", 3.0, 6.0, ("Demand curve", 4.0)), beat("graph", 6.0, 9.0),
             beat("hook", 9.0, 12.0), beat("graph", 12.0, 15.0, ("Gap", 13.0)), beat("close", 15.0, 20.0)]
    _mean(beats, bundle, locations)
    got = [[(m.structure, m.since, m.until) for m in b["meanings"]] for b in beats]
    # Demand curve, named on the second line, keeps its meaning from then to the video's end wherever its name is
    # written, the close-up too; the gap from its own naming. Supply curve, never named: none. The hook has no names.
    assert got[0] == []
    assert got[1] == got[2] == [("Demand curve", 4.0, 20.0)]
    assert got[3] == []
    assert got[4] == [("Demand curve", 4.0, 20.0), ("Gap", 13.0, 20.0)]
    assert got[5] == [("Demand curve", 4.0, 20.0)]
