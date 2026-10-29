import json
import random

from studio import checks
from studio.jobs import read, write
from studio.schemas import Bundle, CheckReport, Problem, Request
from studio.steps import bundle as bundle_step
from studio.tests.sample import bundle_data


def review(change):
    data = bundle_data()
    change(data)
    bundle, report = checks.review(Bundle.model_validate(data))
    return bundle, [(p.line_id, p.rule, p.repaired) for p in report.problems], report


def line(data, line_id):
    return next(item for item in [*data["lyrics"], *data["narration"]] if item["id"] == line_id)


def direction(data, line_id):
    return next(d for d in data["direction"] if d["line_id"] == line_id)


def test_the_sample_bundle_is_clean():
    bundle, found, _ = review(lambda d: None)
    assert found == [] and bundle == Bundle.model_validate(bundle_data())


def test_a_spotlight_waits_until_its_structure_is_named_on_that_scene():
    def change(d):
        for line_id in ("v1-1", "v1-2", "c1-1"):  # v1-1 names the thylakoid on the overview
            direction(d, line_id).update(spotlight="Thylakoid", camera={"target": "Thylakoid", "zoom": 2.0})
        direction(d, "v1-2").update(spotlight="Cytochrome b6f")  # drawn, but never named
        direction(d, "n2").update(spotlight="Thylakoid")  # named, but not on this scene

    bundle, found, _ = review(change)
    assert found == [("v1-2", "spotlight", True), ("n2", "spotlight", True)]
    spotlights = {d.line_id: d.spotlight for d in bundle.direction}
    assert spotlights["v1-1"] == "Thylakoid" and spotlights["c1-1"] == "Thylakoid"
    assert spotlights["v1-2"] == "" and spotlights["n2"] == ""


def test_the_song_and_the_lesson_video_reveal_separately():
    def change(d):
        line(d, "n1").update(names=[], text="Light reactions need membranes.")
        direction(d, "n1").update(spotlight="Thylakoid", camera={"target": "Thylakoid", "zoom": 1.5})

    bundle, found, _ = review(change)
    assert found == [("n1", "spotlight", True), ("n1", "camera", True)]
    assert bundle.direction[-2].camera.zoom == 1.0


def test_a_group_no_scene_draws_is_left_for_the_prompt():
    def change(d):
        line(d, "b-1").update(text="Feel the limbic system build", names=["limbic system"])

    bundle, found, report = review(change)
    assert found == [("b-1", "unknown-name", False)]
    assert report.problems[0].detail == 'names "limbic system", which no scene draws'
    assert bundle.lyrics[5].names == ["limbic system"]


def test_a_scene_with_nothing_to_reveal_is_left_for_the_prompt():
    _, found, report = review(lambda d: d["scenes"][2].update(structures=[]))
    assert ("scene:synthase", "empty-scene", False) in found
    assert report.problems[0].detail.startswith('scene "synthase" draws no structures')


def test_a_name_the_scene_does_not_draw_is_left_for_the_prompt():
    _, found, report = review(lambda d: direction(d, "n2").update(scene="overview"))
    assert found == [("n2", "not-in-scene", False)]
    assert report.problems[0].detail == 'names ATP synthase, which scene "overview" does not draw'


def test_names_are_held_to_what_the_line_says():
    def change(d):
        line(d, "v1-1").update(names=[])  # says "thylakoid" without listing it
        line(d, "v1-2").update(names=["ATP synthase"])  # lists what it never says

    bundle, found, _ = review(change)
    assert found == [("v1-1", "unlisted-name", True), ("v1-2", "name-not-said", True)]
    assert bundle.lyrics[0].names == ["thylakoid"] and bundle.lyrics[1].names == []


def test_the_longest_name_wins():
    canon = {"motor cortex": "Motor cortex", "cortex": "Cerebral cortex", "pons": "Pons"}
    assert checks.said("The motor cortex moves you; the cortex thinks", canon) == {
        "Motor cortex": "motor cortex", "Cerebral cortex": "cortex"}
    assert checks.said("Responsible", canon) == {}  # "pons" inside a word is not the pons


def test_particles_stay_on_the_scene():
    _, found, _ = review(lambda d: direction(d, "v1-1").update(particles=["Thylakoid", "ATP synthase"]))
    assert found == [("v1-1", "particles", True)]


def test_a_structure_named_on_an_earlier_scene_can_be_spotlit_on_a_later_one():
    # "Light lands on the thylakoid" is sung over the hook; the overview, next, draws it labelled.
    bundle, found, _ = review(lambda d: direction(d, "v1-2").update(spotlight="Thylakoid"))
    assert found == [] and next(d.spotlight for d in bundle.direction if d.line_id == "v1-2") == "Thylakoid"
    _, found, report = review(lambda d: direction(d, "v1-2").update(spotlight="Cytochrome b6f"))  # not named yet
    assert found == [("v1-2", "spotlight", True)] and "has not been named yet" in report.problems[0].detail


def test_what_particles_carry_needs_particles_and_fits_on_the_dot():
    def carrying(particles, carries):
        def change(data):
            direction(data, "v1-1").update(particles=particles, carries=carries)
        bundle, found, _ = review(change)
        return next(d.carries for d in bundle.direction if d.line_id == "v1-1"), found

    assert carrying(["Thylakoid", "Cytochrome b6f"], "H⁺") == ("H⁺", [])
    assert carrying([], "H⁺") == ("", [("v1-1", "carries", True)])
    assert carrying(["Thylakoid", "Cytochrome b6f"], "plastoquinone") == ("", [("v1-1", "carries", True)])


def test_what_builds_up_is_where_marked_particles_start_or_end():
    def building(*builds):
        def change(data):
            direction(data, "v1-2").update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
            data["builds_up"] = [{"carries": c, "region": r} for c, r in builds]
        bundle, found, _ = review(change)
        return [(b.carries, b.region) for b in bundle.builds_up], found

    assert building(("H+", "cytochrome B6F")) == ([("H+", "Cytochrome b6f")], [])  # its name, from any spelling
    assert building(("H⁺", "Thylakoid")) == ([("H⁺", "Thylakoid")], [])  # where they start counts too
    left_out = [("builds_up", "builds_up", True)]
    assert building(("H⁺", "ATP synthase")) == ([], left_out)  # no particles carrying H⁺ start or end there
    assert building(("e⁻", "Thylakoid")) == ([], left_out)  # none carry electrons
    assert building(("H⁺", "the lumen")) == ([], left_out)  # no such structure


def test_what_turns_is_where_particles_flow_in_or_through():
    def turning(*spins):
        def change(data):
            direction(data, "v1-2").update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
            data["spins"] = [{"structure": s} for s in spins]
        bundle, found, _ = review(change)
        return [s.structure for s in bundle.spins], found

    assert turning("cytochrome B6F") == (["Cytochrome b6f"], [])  # particles flow into it
    assert turning("Thylakoid") == ([], [("spins", "spins", True)])  # they only leave it
    assert turning("the rotor") == ([], [("spins", "spins", True)])  # no such structure


def test_a_conversion_names_a_structure_and_prints_what_goes_in_and_out():
    def converting(*convs):
        def change(data):
            data["conversions"] = [{"structure": s, "inputs": i, "outputs": o} for s, i, o in convs]
        bundle, found, _ = review(change)
        return [(c.structure, c.inputs, c.outputs) for c in bundle.conversions], found

    assert converting(("cytochrome B6F", ["h2o"], ["O2", "H+"])) == (
        [("Cytochrome b6f", ["h₂o"], ["O₂", "H⁺"])], [])  # its name, and as a textbook prints them
    left_out = [("conversions", "conversions", True)]
    assert converting(("the rotor", ["H₂O"], ["O₂"])) == ([], left_out)  # no such structure
    assert converting(("Thylakoid", [], ["O₂"])) == ([], left_out)  # nothing goes in
    assert converting(("Thylakoid", ["glucose6phosphate"], ["O₂"])) == ([], left_out)  # too long to print
    assert converting(("Thylakoid", ["H₂O"], ["O₂"]), ("thylakoid", ["CO₂"], ["O₂"]))[1] == left_out  # one a structure


def test_a_lyric_that_narrates_the_picture_or_runs_long_is_recorded_not_repaired():
    def change(data):
        data["lyrics"][0]["text"] = "Zoom inside to the chloroplast"
        data["lyrics"][1]["text"] = " ".join(["la"] * 13)
        data["lyrics"][2]["text"] = "Light hits the leaf and the race begins"

    _, found, _ = review(change)
    assert [(line, repaired) for line, rule, repaired in found if rule == "lyric-style"] == [
        ("v1-1", False), ("v1-2", False)]


def test_what_blocks_is_where_particles_end_and_a_glow_waits_for_its_name():
    def change(data):
        direction(data, "v1-2").update(particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻", glow="Thylakoid")
        direction(data, "c1-1").update(glow="Cytochrome b6f")  # not named yet on the overview
        data["blocks"] = [{"structure": "cytochrome B6F"}, {"structure": "Thylakoid"}]
    bundle, found, _ = review(change)
    assert [b.structure for b in bundle.blocks] == ["Cytochrome b6f"]  # particles end there; none end at the other
    glows = {d.line_id: d.glow for d in bundle.direction if d.line_id in ("v1-2", "c1-1")}
    assert glows == {"v1-2": "Thylakoid", "c1-1": ""}
    assert sorted(found) == [("blocks", "blocks", True), ("c1-1", "glow", True)]


def test_an_old_bundle_gets_names_from_its_text(tmp_path):
    data = bundle_data()
    for item in [*data["lyrics"], *data["narration"]]:
        del item["names"]
    (tmp_path / "bundle.json").write_text(json.dumps(data))
    report = checks.run(tmp_path)
    assert report.problems == []
    bundle = read(tmp_path / "bundle.json", Bundle)
    assert bundle.lyrics[0].names == ["thylakoid"] and bundle.narration[1].names == ["ATP synthase"]
    assert read(tmp_path / "checks.json", CheckReport) == report


def test_the_bundle_step_saves_the_repaired_bundle_and_the_checks(tmp_path):
    write(tmp_path / "request.json", Request(topic="the light reactions"))
    data = bundle_data()
    direction(data, "c1-1").update(spotlight="Cytochrome b6f")

    bundle_step.run(tmp_path, ask=lambda *args, **kwargs: data)
    assert read(tmp_path / "bundle.json", Bundle).direction[2].spotlight == ""
    problems = read(tmp_path / "checks.json", CheckReport).problems
    # The bundle step also records what only a better bundle fixes: the sample song is short, and still in places.
    assert (problems[0].line_id, problems[0].rule, problems[0].repaired) == ("c1-1", "spotlight", True)
    assert ("music", "lyric-style", False) in [(p.line_id, p.rule, p.repaired) for p in problems]
    assert all(not p.repaired for p in problems[1:])


def test_curly_quotes_and_dashes_match_plain_ones():
    canon = {"broca's area": "Broca's area", "na-k pump": "Na-K pump"}
    assert checks.said("Broca’s area builds words", canon) == {"Broca's area": "Broca’s area"}
    assert checks.said("the Na–K pump", canon) == {"Na-K pump": "Na–K pump"}

    def change(d):
        d["scenes"][1]["structures"][1].update(name="Broca's area")
        line(d, "v2-1").update(text="Broca’s area builds them", names=["Broca's area"])
        direction(d, "v2-1").update(spotlight="Broca's area")

    _, found, _ = review(change)
    assert found == []


def test_a_word_two_structures_share_is_left_for_the_prompt():
    def change(d):
        d["scenes"][1]["structures"][0]["aliases"] = ["membrane"]
        d["scenes"][1]["structures"][1]["aliases"] = ["membrane"]

    _, found, report = review(change)
    assert found == [("structures", "shared-name", False)]
    assert report.problems[0].detail == '"membrane" names Thylakoid and Cytochrome b6f, but only reveals Thylakoid'


def test_merging_two_reports_keeps_both_without_repeats():
    a = Problem(line_id="v1-1", rule="spotlight", detail="x", repaired=True)
    b = Problem(line_id="scene:hook", rule="empty-scene", detail="y", repaired=False)
    c = Problem(line_id="c1-1", rule="unlisted-name", detail="z", repaired=True)
    merged = checks.merge(CheckReport(problems=[a, b]), CheckReport(problems=[b, c]))
    assert merged.problems == [a, b, c]


def test_checking_a_checked_bundle_changes_nothing():
    """A repair never makes a new problem, whatever the model wrote."""
    rng = random.Random(1026)
    words = ["thylakoid", "Thylakoid", "ATP synthase", "Cytochrome b6f", "limbic system", "", "b6f"]
    targets = ["", "Thylakoid", "thylakoid", "Cytochrome b6f", "ATP synthase", "nowhere"]
    for _ in range(300):
        data = bundle_data()
        for item in [*data["lyrics"], *data["narration"]]:
            item["names"] = rng.sample(words, rng.randint(0, 3))
        for d in data["direction"]:
            d["scene"] = rng.choice(["hook", "overview", "synthase"])
            d["spotlight"] = rng.choice(targets)
            d["camera"] = {"target": rng.choice(targets), "zoom": rng.choice([1.0, 2.0])}
            d["particles"] = rng.sample(targets, rng.randint(0, 3))
            d["carries"] = rng.choice(["", "H⁺", "plastoquinone"])
        if rng.random() < 0.2:
            data["scenes"][0]["structures"] = []
        once, first = checks.review(Bundle.model_validate(data))
        twice, second = checks.review(once)
        assert twice == once
        assert not any(p.repaired for p in second.problems)
        assert {(p.line_id, p.rule) for p in second.problems} <= {(p.line_id, p.rule) for p in first.problems}


def test_the_styles_name_one_tempo_the_song_can_be_timed_by():
    def styles(tags):
        bundle, found, _ = review(lambda d: d["music"].update(positive_styles=tags))
        return bundle.music.positive_styles, found

    assert styles(["K-pop", "98 BPM"]) == (["K-pop", "98 BPM"], [])
    repaired = [("music", "tempo", True)]
    assert styles(["bright pop"]) == (["bright pop", "112 bpm"], repaired)  # none named: what songs used to be
    assert styles(["trap", "200 bpm"]) == (["trap", "160 bpm"], repaired)  # held to the range
    assert styles(["pop", "98 bpm", "half-time 140 bpm"]) == (["pop", "98 bpm", "half-time 98 bpm"], repaired)


def test_a_process_needs_two_stages_or_more():
    def staging(*stages):
        def change(data):
            for scene in data["scenes"]:
                scene["stage_of"] = "photosynthesis" if scene["id"] in stages else ""
        bundle, found, _ = review(change)
        return [s.stage_of for s in bundle.scenes], found

    assert staging("overview", "synthase") == (["", "photosynthesis", "photosynthesis"], [])
    assert staging("synthase") == (["", "", ""], [("scene:synthase", "stages", True)])  # an ordinary scene


def test_the_style_rotation_gives_a_job_the_same_style_every_time_and_neighbours_differ():
    from studio.styles import SONG_STYLES, style_for

    assert style_for("cmvabc") == style_for("cmvabc") and style_for("cmvabc") in SONG_STYLES
    assert len({style_for(f"job{i}") for i in range(40)}) > 4
    assert all(" bpm" in s and 92 <= int(s.rsplit(" bpm", 1)[0].split()[-1]) <= 112 for s in SONG_STYLES)


def test_only_the_verses_are_asked_to_rap_and_the_rest_to_sing():
    from studio.steps import audio

    data = bundle_data()
    data["music"]["positive_styles"] = ["pop with clear rapped verses", "100 bpm"]
    bundle = Bundle.model_validate(data)
    plan, _ = audio.composition_plan(bundle)
    by_section = {c["text"].splitlines()[0]: c for c in plan["chunks"]}
    assert "clear rapped verse" in by_section["[Verse]"]["positive_styles"]
    assert "rapping" not in by_section["[Verse]"]["negative_styles"]
    assert "rapping" in by_section["[Chorus]"]["negative_styles"]
    assert "sung melodic vocal, not rapped" in by_section["[Chorus]"]["positive_styles"]
    data["music"]["positive_styles"] = ["bright pop", "100 bpm"]  # no rap asked for: nothing added
    plan, _ = audio.composition_plan(Bundle.model_validate(data))
    assert all("rapp" not in " ".join(c["positive_styles"] + c["negative_styles"]) for c in plan["chunks"])


def test_a_song_that_stands_still_or_directs_its_choruses_differently_is_recorded():
    data = bundle_data()
    for d in data["direction"]:
        d.update(particles=[], carries="")
    found = [(p.line_id, p.detail) for p in checks.motion_problems(Bundle.model_validate(data))]
    still = [line for line, detail in found if "nothing moving" in detail]
    assert still, "a song with nothing moving on its diagram scenes is recorded"
    assert all(next(d.scene for d in Bundle.model_validate(data).direction if d.line_id == line) != "hook"
               for line in still)  # the hook need not move


def test_key_lines_are_put_in_song_order_and_ones_the_screen_cannot_show_are_dropped():
    data = bundle_data()
    data["key_lines"] = [{"from_line": "v2-1", "text": "Light pumps H⁺", "from_narration": "n2"},
                         {"from_line": "v1-1", "text": "How?", "from_narration": "n2"},
                         {"from_line": "nope", "text": "Lost"}, {"from_line": "v2-1", "text": "Twice"},
                         {"from_line": "c1-1", "text": "Unspoken", "from_narration": "zz"}]
    bundle, report = checks.review(Bundle.model_validate(data))
    assert [(k.from_line, k.text, k.from_narration) for k in bundle.key_lines] == [
        ("v1-1", "How?", ""), ("c1-1", "Unspoken", ""), ("v2-1", "Light pumps H⁺", "n2")]  # n2 went to the first
    assert len([p for p in report.problems if p.rule == "key-line"]) == 4


def test_key_lines_are_recorded_when_too_few_or_too_long():
    data = bundle_data()
    data["why_chain"] = ["a", "b", "c"]
    data["key_lines"] = [{"from_line": "v1-1", "text": " ".join(["word"] * 11)}]
    found = [p.detail for p in checks.key_line_problems(Bundle.model_validate(data))]
    assert found == ["1 key lines, not 3 to 10", "no key line is placed in the narration, so the lesson video shows none",
                     f'the key line "{" ".join(["word"] * 11)}" runs 11 words, over 10']
    assert checks.key_line_problems(Bundle.model_validate(bundle_data())) == []  # before the why chain
