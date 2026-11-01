import json
import random

from studio import checks
from studio.jobs import read, write
from studio.schemas import Bundle, CheckReport, LessonBundle, Problem, Request, bundle_of
from studio.steps import bundle as bundle_step
from studio.tests.sample import bundle_data, lesson_bundle_data


def review(change):
    data = bundle_data()
    change(data)
    bundle, report = checks.review(Bundle.model_validate(data))
    return bundle, [(p.line_id, p.rule, p.repaired) for p in report.problems], report


def line(data, line_id):
    return next(item for item in [*data["lyrics"], *data["narration"]] if item["id"] == line_id)


def direction(data, line_id):
    return next(d for d in data["direction"] if d["line_id"] == line_id)


LATER_CHORUSES = ("c2-1", "c3-1")  # directed as c1-1 by the review (_direct_choruses), whatever a test set on c1-1


def own(found):
    """What a test found, leaving out the later choruses, which the review directs as the first."""
    return [f for f in found if f[0] not in LATER_CHORUSES]


def test_the_sample_bundle_is_clean():
    bundle, found, _ = review(lambda d: None)
    assert found == [] and bundle == Bundle.model_validate(bundle_data())


def song_picture(scene_id):
    return {"id": scene_id, "image_prompt": "ATP synthase turning.", "structures": [
        {"name": "ATP synthase", "aliases": [], "sam_phrase": "the atp synthase", "color_hex": "#3366CC"}]}


def test_the_songs_own_picture_stays_the_songs_and_one_no_line_plays_over_is_left_out():
    def change(d):
        d["song_scenes"] = [song_picture("chorus"), song_picture("unplayed")]
        for line_id in ("c1-1", "c2-1", "c3-1"):
            direction(d, line_id)["scene"] = "chorus"

    bundle, found, _ = review(change)
    assert [(s.id, s.song_only) for s in bundle.scenes][-1] == ("chorus", True)
    assert found == [("scene:unplayed", "song-scene", True)]
    assert checks.review(bundle)[1].problems == []  # settled: a second look finds nothing


def test_the_song_keeps_the_3_pictures_of_its_own_it_plays_longest():
    def change(d):
        d["song_scenes"] = [song_picture(f"song-{i}") for i in range(4)]
        sung = ["v1-1", "v1-2", "c1-1", "v2-1", "c2-1", "b-1", "c3-1"]
        for line_id, scene in zip(sung, ["song-0", "song-1", "song-1", "song-2", "song-2", "song-3", "song-3"]):
            direction(d, line_id)["scene"] = scene

    bundle, found, _ = review(change)
    assert [s.id for s in bundle.scenes if s.song_only] == ["song-1", "song-2", "song-3"]
    assert own(found) == [("scene:song-0", "song-scene", True)]
    assert {d.line_id: d.scene for d in bundle.direction}["v1-1"] == "hook"  # the scene before the song's first


def test_a_narration_sentence_never_plays_over_the_songs_own_picture():
    def narrated(d):
        d["song_scenes"] = [song_picture("chorus")]
        direction(d, "c1-1")["scene"] = "chorus"
        direction(d, "n2")["scene"] = "chorus"

    # With room in the lesson, the picture is the lesson's too, and the sentence keeps it.
    bundle, found, _ = review(narrated)
    assert [(s.id, s.song_only) for s in bundle.scenes][-1] == ("chorus", False)
    assert own(found) == [("scene:chorus", "song-scene", True)]
    assert {d.line_id: d.scene for d in bundle.direction}["n2"] == "chorus"

    # With the lesson's 7 scenes drawn, the sentence plays over the scene the narration was on.
    def full(d):
        narrated(d)
        d["scenes"] += [{**song_picture(f"detail-{i}"), "image_prompt": "A cell."} for i in range(4)]

    bundle, found, _ = review(full)
    assert [(s.id, s.song_only) for s in bundle.scenes][-1] == ("chorus", True)
    assert ("n2", "song-scene", True) in found
    assert {d.line_id: d.scene for d in bundle.direction}["n2"] == "overview"


def test_a_spotlight_waits_until_its_structure_is_named_on_that_scene():
    def change(d):
        for line_id in ("v1-1", "v1-2", "c1-1"):  # v1-1 names the thylakoid on the overview
            direction(d, line_id).update(spotlight="Thylakoid", camera={"target": "Thylakoid", "zoom": 2.0})
        direction(d, "v1-2").update(spotlight="Cytochrome b6f")  # drawn, but never named
        direction(d, "n2").update(spotlight="Thylakoid")  # named, but not on this scene

    bundle, found, _ = review(change)
    assert own(found) == [("v1-2", "spotlight", True), ("n2", "spotlight", True)]
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


def test_a_word_for_a_part_off_screen_is_a_word_not_a_name():
    # The line says "ATP synthase" over a scene that does not draw it, and does not list it: a word, as a song
    # carrying its frame says one. Listed, the same line is a real slip (the test above).
    def change(d):
        direction(d, "n2").update(scene="overview")
        line(d, "n2").update(names=[])

    bundle, found, _ = review(change)
    assert found == []
    assert next(n for n in bundle.narration if n.id == "n2").names == []


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
    # Unmarked, it moves as an arrow (it has no symbol to write on a particle), which then wants its one-liner.
    assert carrying(["Thylakoid", "Cytochrome b6f"], "plastoquinone") == ("", [("v1-1", "carries", True),
                                                                              ("v1-1", "says", False)])


def test_an_arrow_has_a_short_one_liner_beside_it():
    def saying(carries, says, moves=""):
        def change(data):
            direction(data, "v1-1").update(particles=["Thylakoid", "Cytochrome b6f"], carries=carries, moves=moves,
                                           says=says)
        bundle, found, _ = review(change)
        return next(d.says for d in bundle.direction if d.line_id == "v1-1"), [f for f in found if f[1] == "says"]

    assert saying("H⁺", "") == ("", [])  # a marked ion crosses as particles: no arrow, nothing to say
    assert saying("H⁺", "", moves="travels") == ("", [("v1-1", "says", False)])  # an arrow, bare
    assert saying("glucose", "") == ("", [("v1-1", "says", False)])  # a word moves as an arrow
    assert saying("glucose", "  Glucose rides in   with sodium ") == ("Glucose rides in with sodium", [])
    assert saying("glucose", "x" * (checks.SAYS_MAX + 1)) == ("", [("v1-1", "says", True), ("v1-1", "says", False)])
    assert checks.arrow("Ca²⁺", "") is False and checks.arrow("NAD⁺", "crosses") is False  # three letters at most
    assert checks.arrow("NADPH", "crosses") is True and checks.arrow("", "") is True


def test_a_picture_asking_for_two_moments_side_by_side_is_recorded():
    def asking(prompt):
        def change(data):
            data["scenes"][-1]["image_prompt"] = prompt
        _, found, _ = review(change)
        return [f for f in found if f[1] == "side-by-side"]

    assert asking("One sodium-potassium pump in a membrane, drawn twice side by side.") != []
    assert asking("A before and after view of the cell.") != []
    assert asking("The dog in four panels, left to right.") != [] and asking("In the third panel, the bell.") != []
    assert asking("The Moon drawn three times across the sky.") != []
    assert asking("One pump in a membrane, its pocket open toward the inside.") == []
    # Two things side by side are no sign (the scorecard of Nov 1): two markets, two people, a chart's bars.
    assert asking("Two places that sell strawberries, side by side.") == []
    assert asking("Romeo and Juliet standing side by side under the stars.") == []
    assert asking("Eleven vertical bars of equal width stand side by side.") == []


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
    assert converting(("Thylakoid", ["H₂O"], ["thylakoid"])) == ([], left_out)  # never the structure itself
    assert converting(("ATP synthase", ["ADP", "Pᵢ"], ["ATP"])) == (  # a word of its name is not the structure
        [("ATP synthase", ["ADP", "Pᵢ"], ["ATP"])], [])


def test_a_meaning_names_a_part_of_a_graph_in_one_short_line():
    def meaning(*pairs, graph=True):
        def change(data):
            if graph:
                scene = next(s for s in data["scenes"] if s["id"] == "overview")
                scene["axes"] = {"x": "Time", "y": "Rate"}
            data["meanings"] = [{"structure": s, "text": text} for s, text in pairs]
        bundle, found, _ = review(change)
        return [(m.structure, m.text) for m in bundle.meanings], [f for f in found if f[1] == "meanings"]

    assert meaning(("thylakoid", "  more  light → faster ")) == ([("Thylakoid", "more light → faster")], [])
    left_out = [("meanings", "meanings", True)]
    assert meaning(("the rotor", "spins")) == ([], left_out)  # no such structure
    assert meaning(("Thylakoid", "more light → faster"), graph=False) == ([], left_out)  # no graph draws it
    assert meaning(("Thylakoid", "x" * 41)) == ([], left_out)  # too long for one line under its name
    assert meaning(("Thylakoid", "")) == ([], left_out)
    assert meaning(("Thylakoid", "one"), ("thylakoid", "two"))[1] == left_out  # one a part

    def on_analogy(*pairs):  # no picture draws the analogy (Nov 1): a meaning for one of its parts is left out
        def change(data):
            next(s for s in data["scenes"] if s["id"] == "hook")["id"] = "analogy"
            for d in data["direction"]:
                d["scene"] = "analogy" if d["scene"] == "hook" else d["scene"]
            data["meanings"] = [{"structure": s, "text": text} for s, text in pairs]
        bundle, found, _ = review(change)
        return [(m.structure, m.text) for m in bundle.meanings], [f for f in found if f[1] == "meanings"]

    assert on_analogy(("Thylakoid", "the stored-up protons")) == ([], left_out)


def test_syllables_are_counted_as_sung():
    from studio.checks import syllables
    # A silent "-ed" is no syllable, but a sung one is; a contraction is one word, not a word and a lone letter.
    assert [syllables(w) for w in ("learned", "used", "wanted", "needed", "hundred", "troubled")] == [1, 1, 2, 2, 2, 2]
    assert [syllables(w) for w in ("it's", "you're", "don't", "they're")] == [1, 1, 1, 1]
    assert syllables("Acquisition, now it's learned by heart") == 9  # was counted 11, and flagged


def test_a_lyric_one_syllable_over_passes_and_two_over_is_recorded():
    def lyric(text):
        def change(data):
            data["lyrics"][0]["text"] = text
        _, found, _ = review(change)
        return [rule for line, rule, _ in found if line == "v1-1" and rule == "lyric-style"]

    assert lyric(" ".join(["la"] * 11)) == []  # one over the cap: a singer takes it in stride
    assert lyric(" ".join(["la"] * 12)) == ["lyric-style"]



def test_a_style_that_raps_one_section_lets_only_its_lines_run_longer():
    # The owner (Nov 1): a K-pop song has a rap section, not a rapped song. The rest stays sung, held to the sung cap.
    def rapped(style):
        data = bundle_data()
        data["music"]["positive_styles"] = [style]
        return checks.rapped_lines(data)

    assert rapped("K-pop, a sung first verse, a rapped second verse, 100 bpm") == {"v2-1"}
    assert rapped("dance-pop, short sung verses and a rapped bridge, 112 bpm") == {"b-1"}
    assert rapped("Pop with clear rapped verses, rising into a sung chorus") == {
        "v1-1", "v1-2", "c1-1", "v2-1", "c2-1", "b-1", "c3-1"}  # rapped, no section named: every line, as before
    assert rapped("bright teen pop with melodic sung verses, 104 bpm") == set()
    # However the writer words it, the section it names, never every line.
    assert rapped("K-pop that raps the second verse, 100 bpm") == rapped("K-pop, verse two rapped") == {"v2-1"}
    assert rapped("pop, the bridge is rapped") == rapped("pop rapping on the bridge") == {"b-1"}

    def bridgeless(data):
        data["lyrics"] = [line for line in data["lyrics"] if line["section"] != "bridge"]
        return data
    # A rotation style that raps the bridge, for a topic with no bridge: nothing is rapped, not everything.
    assert checks.rapped_lines(bridgeless(dict(bundle_data(), music={
        "positive_styles": ["dance-pop, short sung verses and a rapped bridge, 112 bpm"], "negative_styles": [],
        "sections": []}))) == set()

    def long_lines(style):
        def change(data):
            data["music"]["positive_styles"] = [style]
            for line in data["lyrics"]:
                if line["id"] in ("v1-1", "v2-1"):
                    line["text"] = " ".join(["la"] * 13)
        _, found, _ = review(change)
        return [line for line, rule, _ in found if rule == "lyric-style"]

    assert long_lines("K-pop, a sung first verse, a rapped second verse, 100 bpm") == ["v1-1"]  # the sung one

def test_a_lyric_that_narrates_the_picture_or_runs_long_is_recorded_not_repaired():
    def change(data):
        data["lyrics"][0]["text"] = "Zoom inside to the chloroplast"
        data["lyrics"][1]["text"] = " ".join(["la"] * 13)
        data["lyrics"][2]["text"] = "Light hits the leaf and the race begins"

    _, found, _ = review(change)
    assert [(line, repaired) for line, rule, repaired in found if rule == "lyric-style"] == [
        ("v1-1", False), ("v1-2", False)]


def test_next_up_and_here_comes_announce_a_section_but_are_sung_before_anything_else():
    narrates = [bool(checks.DIRECTION_LINE.match(text)) for text in (
        "Next up, the bridge", "Now next up is the chorus", "Here comes the bridge", "Here we go again",
        "Next up, the power rule, with no limit to compute", "Next up: estar plus an -ando, the present progressive",
        "Here comes HIV with a protein like a key")]
    assert narrates == [True, True, True, True, False, False, False]


def test_a_sung_watch_or_see_is_not_a_direction():
    # Nov 1: two songs' frames were flagged as narrating the picture.
    narrates = [bool(checks.DIRECTION_LINE.match(text)) for text in (
        "Zoom inside to the chloroplast", "Zoom on the corner, what do you see?", "Now look at the membrane",
        "Look inside the nephron", "Let's see how the pump turns", "Now watch the gate swing open",
        "Watch the replay, you know the score", "Watch the needle climbing slow", "See if the same result is found",
        "Look up some night, a silver sliver", "Now seeing is believing")]
    assert narrates == [True, True, True, True, True, True, False, False, False, False, False]


def test_what_blocks_is_where_particles_end_and_a_glow_waits_for_its_name():
    def change(data):
        direction(data, "v1-2").update(particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻", glow="Thylakoid")
        direction(data, "c1-1").update(glow="Cytochrome b6f")  # not named yet on the overview
        data["blocks"] = [{"structure": "cytochrome B6F"}, {"structure": "Thylakoid"}]
    bundle, found, _ = review(change)
    assert [b.structure for b in bundle.blocks] == ["Cytochrome b6f"]  # particles end there; none end at the other
    glows = {d.line_id: d.glow for d in bundle.direction if d.line_id in ("v1-2", "c1-1")}
    assert glows == {"v1-2": "Thylakoid", "c1-1": ""}
    assert sorted(own(found)) == [("blocks", "blocks", True), ("c1-1", "glow", True)]


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
    # What the review repaired is kept when the words set once (the study tags) are read again; the later choruses are
    # directed as the first, so its spotlight waits on them too. The bundle step also records what only a better
    # bundle fixes: the sample song is short, and still in places.
    found = [(p.line_id, p.rule, p.repaired) for p in problems]
    assert [f for f in found if f[2] and f[0] not in LATER_CHORUSES] == [("c1-1", "spotlight", True)]
    assert ("music", "lyric-style", False) in found


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
    assert styles(["bright pop."]) == (["bright pop, 112 bpm"], repaired)  # none named: what songs used to be, said
    # at the end of the description
    assert styles(["trap", "200 bpm"]) == (["trap", "160 bpm"], repaired)  # held to the range
    assert styles(["pop", "98 bpm", "half-time 140 bpm"]) == (["pop", "98 bpm", "half-time 98 bpm"], repaired)
    assert styles(["pop at 98 beats per minute"]) == (["pop at 98 beats per minute"], [])  # named, in words


def test_the_song_style_is_one_description_that_keeps_the_learners_tempo():
    def found(styles, asked=""):
        data = bundle_data()
        data["music"]["positive_styles"] = styles
        return [p.detail for p in checks.style_problems(Bundle.model_validate(data), asked)]

    asked = "Bright pop with hip-hop verses; buoyant 104 bpm beat"
    # Built on in the writer's own words, as long as the learner's tempo stays.
    assert found(["Bright pop with sharp hip-hop verses; glassy synths over a buoyant 104 bpm beat"], asked) == []
    assert found(["Bright pop; 98 bpm"], asked) == ["the song is at 98 bpm, not the 104 bpm the learner asked for"]
    assert found(["bright pop", "hip-hop verses", "104 bpm"]) == ["the style is 3 tags, not one free-form description"]


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


def test_every_section_is_asked_for_the_songs_own_style_and_clear_diction_once():
    from studio.steps import audio

    data = bundle_data()
    data["music"]["positive_styles"] = ["Pop with clear rapped verses, rising into a sung chorus; 100 bpm."]
    bundle = Bundle.model_validate(data)
    plan, _ = audio.composition_plan(bundle)
    assert all(c["positive_styles"] == [*bundle.music.positive_styles, audio.DICTION] for c in plan["chunks"])
    assert all(c["negative_styles"] == [*bundle.music.negative_styles, *audio.AVOID] for c in plan["chunks"])
    prompt, _ = audio.song_prompt(bundle)
    style = bundle.music.positive_styles[0].rstrip(".")
    assert "never rapped" not in prompt and prompt.startswith(f"Style: {style}, {audio.DICTION}.\n")
    assert ".." not in prompt  # the description's own full stop is the sentence's
    # A style that asks for diction already is not asked again.
    data["music"]["positive_styles"] = ["Pop with crisp diction on the verses; 100 bpm"]
    plan, _ = audio.composition_plan(Bundle.model_validate(data))
    assert all(c["positive_styles"] == ["Pop with crisp diction on the verses; 100 bpm"] for c in plan["chunks"])
    assert audio.song_prompt(Bundle.model_validate(data))[0].count("diction") == 1


def test_every_song_avoids_a_long_intro_and_explicit_lyrics_and_its_avoid_list_never_asks_for_diction():
    from studio.steps import audio

    assert audio.with_avoid([]) == ["long instrumental intro", "explicit lyrics"]
    assert audio.with_avoid(["screamed vocals", "Mumbled diction", "Long  instrumental intro"]) == [
        "screamed vocals", "Long  instrumental intro", "explicit lyrics"]  # each once, as the writer put it


def test_lines_that_hold_still_are_not_a_problem():
    # A line about nothing that travels holds still, whether or not something travels on its scene elsewhere.
    data = bundle_data()
    for d in data["direction"]:
        d.update(particles=[], carries="")
    assert checks.motion_problems(Bundle.model_validate(data)) == []
    direction(data, "n1").update(particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻")
    direction(data, "v1-2").update(particles=["Thylakoid", "Cytochrome b6f"], carries="e⁻")
    assert checks.motion_problems(Bundle.model_validate(data)) == []


def test_key_lines_keep_the_chains_order_and_ones_the_song_cannot_show_stay_in_the_lesson():
    data = bundle_data()
    data["key_lines"] = [{"from_line": "v2-1", "text": "Light pumps H⁺", "from_narration": "n2"},
                         {"from_line": "v1-1", "text": "How?", "from_narration": "n2"},
                         {"from_line": "nope", "text": "Lost"}, {"from_line": "v2-1", "text": "Twice"},
                         {"from_line": "c1-1", "text": "Unspoken", "from_narration": "zz"},
                         {"from_line": "", "text": "Not sung", "from_narration": "n1"},  # as the brief asks
                         {"from_line": "gone", "text": "Said only", "from_narration": "n1"}]
    bundle, report = checks.review(Bundle.model_validate(data))
    assert [(k.from_line, k.text, k.from_narration) for k in bundle.key_lines] == [
        ("v2-1", "Light pumps H⁺", "n2"), ("v1-1", "How?", ""), ("c1-1", "Unspoken", ""), ("", "Not sung", "n1"),
        ("", "Said only", "")]  # n2 and n1 went to the first that asked; a song may sing the chain out of order
    details = [p.detail for p in report.problems if p.rule == "key-line"]
    assert len(details) == 6 and any("out of the song video" in d and "Said only" in d for d in details)
    assert not any("Not sung" in d for d in details)  # "" is no problem: a step the song never sings


def test_every_chorus_is_directed_as_the_first_where_it_sings_the_same_words():
    def change(data):
        direction(data, "c1-1").update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
        direction(data, "c2-1").update(spotlight="Thylakoid", caption="Proton pump")
        next(line for line in data["lyrics"] if line["id"] == "c3-1")["text"] = "Protons flow and the gradient goes"
        direction(data, "c3-1").update(spotlight="Thylakoid")  # a line of its own keeps its own

    bundle, found, _ = review(change)
    directed = {d.line_id: d for d in bundle.direction}
    assert (directed["c2-1"].particles, directed["c2-1"].carries, directed["c2-1"].spotlight) == (
        ["Thylakoid", "Cytochrome b6f"], "H⁺", "")
    assert directed["c2-1"].caption == "Proton pump"  # but its caption
    assert (directed["c3-1"].spotlight, directed["c3-1"].particles) == ("Thylakoid", [])
    assert [f for f in found if f[1] == "motion"] == [("c2-1", "motion", True)]


def test_the_questions_key_line_goes_where_each_video_asks_it():
    # Nov 1: the writer put the question on the narration's first sentence, a fact two before the narrator asks it.
    def asked(question_on="n1", step_on="n3"):
        data = bundle_data()
        data["narration"] = [{"id": "n1", "text": "The light reactions happen in the thylakoid membrane.",
                              "names": ["thylakoid"], "fact_id": "f1"},
                             {"id": "n2", "text": "So how does a leaf make ATP from light?", "names": [],
                              "fact_id": "filler"},
                             {"id": "n3", "text": "Only cytochrome b6f pumps protons.", "names": ["cytochrome b6f"],
                              "fact_id": "f2"}]
        scenes = {"n1": "hook", "n2": "hook", "n3": "overview"}
        data["direction"] = [d for d in data["direction"] if not d["line_id"].startswith("n")] + [
            {**direction(bundle_data(), "n1"), "line_id": n, "scene": s} for n, s in scenes.items()]
        data["key_lines"] = [{"from_line": "v1-2", "text": "How does a leaf make ATP?", "from_narration": question_on},
                             {"from_line": "v2-1", "text": "Only b6f pumps H⁺", "from_narration": step_on}]
        bundle, report = checks.review(Bundle.model_validate(data))
        return ([(k.from_line, k.from_narration) for k in bundle.key_lines],
                [p.line_id for p in report.problems if p.rule == "key-line"])

    assert asked() == ([("v1-1", "n2"), ("v2-1", "n3")], ["v1-1", "n2"])  # the song's first line, the hook's question
    assert asked(question_on="n2") == ([("v1-1", "n2"), ("v2-1", "n3")], ["v1-1"])
    assert asked(step_on="n2") == ([("v1-1", "n1"), ("v2-1", "n2")], ["v1-1"])  # never onto another's sentence


def test_key_lines_are_recorded_when_too_few_or_too_long():
    data = bundle_data()
    data["why_chain"] = ["a", "b", "c"]
    data["key_lines"] = [{"from_line": "v1-1", "text": " ".join(["word"] * 11)}]
    found = [p.detail for p in checks.key_line_problems(Bundle.model_validate(data))]
    assert found == ["1 key lines, not 3 to 10", "no key line is placed in the narration, so the lesson video shows none",
                     f'the key line "{" ".join(["word"] * 11)}" runs 11 words, over 10']
    assert checks.key_line_problems(Bundle.model_validate(bundle_data())) == []  # before the why chain


def test_the_arrows_and_signs_in_a_key_line_are_not_words():
    data = bundle_data()
    data["why_chain"] = ["a", "b", "c"]
    data["key_lines"] = [{"from_line": "v1-1", "text": "Rise over run → average rate of change = secant slope",
                          "from_narration": "n1"},  # 9 words, an arrow and a sign: as the prompt asks
                         {"from_line": "v1-2", "text": "One vote per estate → Third Estate outvoted two to one"},
                         {"from_line": "v2-1", "text": "Corner: slopes 1 vs −1 → no limit → so no derivative there"}]
    found = [p.detail for p in checks.key_line_problems(Bundle.model_validate(data))]
    assert found == ['the key line "Corner: slopes 1 vs −1 → no limit → so no derivative there" runs 11 words, over 10']


def test_a_second_flow_on_a_line_is_held_to_the_same_rules_and_one_at_most_is_kept():
    def change(data):
        direction(data, "v1-1").update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺", also=[
            {"particles": ["Cytochrome b6f", "Thylakoid"], "carries": "plastoquinone"},  # kept, unmarked
            {"particles": ["Thylakoid", "Cytochrome b6f"], "carries": "O₂"}])  # a third thing: left out
    bundle, found, _ = review(change)
    line = next(d for d in bundle.direction if d.line_id == "v1-1")
    assert [(f.particles, f.carries) for f in line.flows()] == [
        (["Thylakoid", "Cytochrome b6f"], "H⁺"), (["Cytochrome b6f", "Thylakoid"], "")]
    # The unmarked one moves as an arrow, which wants a one-liner (Direction.says).
    assert sorted(found) == [("v1-1", "carries", True), ("v1-1", "particles", True), ("v1-1", "says", False)]
    _, found, _ = review(lambda d: direction(d, "v1-1").update(also=[{"particles": ["Thylakoid", "ATP synthase"],
                                                                       "carries": "O₂"}]))
    assert found == [("v1-1", "particles", True)]  # ATP synthase is not on the hook: fewer than two stops


def shaped(chorus, core_idea, extra=(), bpm_tag="112 bpm"):
    data = bundle_data()
    data["core_idea"] = core_idea
    data["why_chain"] = ["a", "b", "c"]
    data["music"]["positive_styles"] = ["upbeat pop", bpm_tag]
    for line in data["lyrics"]:
        if line["section"] == "chorus":
            line["text"] = chorus
    for line_id, text in extra:
        next(line for line in data["lyrics"] if line["id"] == line_id)["text"] = text
    return [p.detail for p in checks.song_shape_problems(Bundle.model_validate(data))]


def test_a_hook_that_names_the_idea_passes_and_one_made_of_pronouns_is_recorded():
    idea = "Light splits water, and the protons it pumps make ATP."
    assert shaped("Split the water, catch the light", idea) == []
    found = shaped("Pump them in, they spin it out", idea)
    assert any("leans on pronouns" in f for f in found) and any("too little of the core idea" in f for f in found)


def test_the_song_shape_check_holds_any_subject_to_its_own_idea():
    # History: the hook is held to the droplet's own core idea, not to any science words.
    assert shaped("Bread too dear, the people rise against the king",
                  "Debt and hunger turned the French people against the king.") == []
    assert shaped("Feel it rise, let it go",
                  "Debt and hunger turned the French people against the king.") != []


def test_a_bridge_that_retells_the_verses_is_recorded_and_one_on_a_new_case_is_not():
    idea = "Light splits water, and the protons it pumps make ATP."
    retold = shaped("Split the water, catch the light", idea,
                    [("b-1", "Light lands, and every hand-off pumps a proton")])  # verse 1 again
    assert retold == ["the bridge retells the verses: 1 of its 1 lines sing what the verses sang (b-1)"]
    assert shaped("Split the water, catch the light", idea,
                  [("b-1", "A herbicide jams photosystem two, so the flow stops")]) == []


def test_too_few_choruses_long_verses_and_rhymes_filled_with_tags_are_recorded():
    data = bundle_data()
    data["why_chain"] = ["a", "b", "c"]
    data["lyrics"] = [line for line in data["lyrics"] if line["id"] != "c2-1"]
    data["direction"] = [d for d in data["direction"] if d["line_id"] != "c2-1"]
    found = [p.detail for p in checks.song_shape_problems(Bundle.model_validate(data))]
    assert "the chorus is sung 2 times, not 3" in found
    tags = shaped("Split the water, catch the light", "Light splits water.",
                  [("v1-1", "Light lands on the thylakoid, for real"), ("v1-2", "Protons pile up, no doubt"),
                   ("v2-1", "Only b6f pumps them in, it's true")])
    assert any("3 lines end on a claim-free tag" in f for f in tags)


TITLE = "Pull the sea, then let it go"
CHORUS_LINES = [TITLE, "High tide, low tide", "To and fro, to and fro", TITLE]


def sung(data, sections):
    """The sample with these lyrics, (id, section, text) each, all over the overview."""
    data["lyrics"] = [{"id": i, "section": s, "text": t, "names": [], "fact_id": "f1"} for i, s, t in sections]
    spoken = [d for d in data["direction"] if d["line_id"].startswith("n")]
    data["direction"] = [{**spoken[0], "line_id": i, "scene": "overview"} for i, *_ in sections] + spoken
    return data


def chorus(k, lines=CHORUS_LINES):
    return [(f"c{k}-{n}", "chorus", text) for n, text in enumerate(lines, 1)]


def test_a_song_with_no_bridge_sings_its_chorus_three_times_though_the_last_two_run_together():
    # Nov 1: with no bridge, the second chorus runs straight into the last, which opens on its summary couplet; read
    # as one run, every bridgeless song was "sung 2 times" (dramatic irony, the subjunctive, classical conditioning).
    lyrics = [("v1-1", "verse", "x"), *chorus(1), ("v2-1", "verse", "y"), *chorus(2),
              ("s-1", "chorus", "The Moon pulls, the sea obeys"), ("s-2", "chorus", "Twice a day, in every bay"),
              *chorus(3), ("o-1", "outro", TITLE)]
    data = sung(dict(bundle_data(), why_chain=["a", "b", "c"]), lyrics)
    assert checks.chorus_spans([s for _, s, _ in lyrics]) == [[1, 2, 3, 4], [6, 7, 8, 9], [12, 13, 14, 15]]
    assert not any("chorus is sung" in p.detail for p in checks.song_shape_problems(Bundle.model_validate(data)))
    data = sung(data, [line for line in lyrics if not line[0].startswith("c3")])
    found = [p.detail for p in checks.song_shape_problems(Bundle.model_validate(data))]
    assert "the chorus is sung 2 times, not 3" in found  # the couplet alone is no chorus


def test_every_later_chorus_is_sung_word_for_word_as_the_first():
    slipped = [TITLE, "High tide and low tide", "To and fro, to and fro", TITLE]
    lyrics = [("v1-1", "verse", "x"), *chorus(1), ("v2-1", "verse", "y"), *chorus(2, slipped),
              ("s-1", "chorus", "sum one"), ("s-2", "chorus", "sum two"), *chorus(3, slipped)]
    bundle, problems = checks.repair_once(Bundle.model_validate(sung(bundle_data(), lyrics)))
    texts = {line.id: line.text for line in bundle.lyrics}
    assert texts["c2-2"] == texts["c3-2"] == "High tide, low tide" and texts["s-1"] == "sum one"
    assert [(p.line_id, p.rule, p.repaired) for p in problems] == [("c2-2", "chorus", True), ("c3-2", "chorus", True)]
    # One that does not open and end as the first may not line up with it: left as written.
    shifted = [*chorus(1), ("v2-1", "verse", "y"), *chorus(2, ["High tide, low tide", "x", "y", TITLE])]
    bundle, problems = checks.repair_once(Bundle.model_validate(sung(bundle_data(), shifted)))
    assert [line.text for line in bundle.lyrics[-4:]] == ["High tide, low tide", "x", "y", TITLE] and problems == []


def test_a_head_count_is_said_as_a_common_answer():
    # The facts call a mistake common; they never count who makes it, so the fact-check rewrote each head count.
    data = bundle_data()
    data["narration"][1]["text"] = "Does the heavy one land first? Most people say yes."
    data["lesson_markdown"] += "\nMany students think that every carrier pumps protons. And most people say no.\n"
    bundle, problems = checks.repair_once(Bundle.model_validate(data))
    assert bundle.narration[1].text == "Does the heavy one land first? A common answer is yes."
    assert "\nA common guess is that every carrier pumps protons. And most people say no.\n" in bundle.lesson_markdown
    assert [(p.line_id, p.rule) for p in problems] == [("n2", "head-count"), ("lesson_markdown", "head-count")]


def test_a_source_quote_the_study_guide_does_not_hold_is_left_out():
    data = bundle_data()
    data["facts"][0]["source_quote"] = "The light  reactions happen in the THYLAKOID"  # spacing and case aside
    data["facts"][1]["source_quote"] = "only b6f pumps"
    guide = "Notes: the light reactions happen in the thylakoid membrane."
    bundle, problems = checks.repair_once(Bundle.model_validate(data), guide)
    assert [f.source_quote for f in bundle.facts] == ["The light  reactions happen in the THYLAKOID", "", ""]
    assert [(p.line_id, p.rule) for p in problems] == [("f2", "source-quote")]
    bundle, problems = checks.repair_once(Bundle.model_validate(data))  # no guide: no quote stands
    assert [f.source_quote for f in bundle.facts] == ["", "", ""] and len(problems) == 2


def test_the_study_tags_follow_the_writers_own_and_the_rest_is_left_as_it_was():
    data = bundle_data()
    bundle, problems = checks.repair_once(Bundle.model_validate(data))
    assert bundle.tags.song == ["photosynthesis", "lightreactions", "apbio", "studysong", "pop", "studytok"]
    assert bundle.tags.lesson == ["photosynthesis", "lightreactions", "apbio", "explained", "examprep", "studytok"]
    assert problems == [] and checks.repair_once(bundle) == (bundle, [])  # said once: the same bundle again


def test_an_axis_name_is_trimmed_and_left_off_when_long_or_a_structures_name():
    data = bundle_data()
    data["scenes"][1]["axes"] = {"x": "  Quantity ", "y": "Number of protons pumped per second"}
    data["scenes"][2]["axes"] = {"x": "atp synthase", "y": "Price"}
    bundle, report = checks.review(Bundle.model_validate(data))
    assert [(s.axes.x, s.axes.y) for s in bundle.scenes] == [("", ""), ("Quantity", ""), ("", "Price")]
    assert [(p.line_id, p.rule, p.repaired) for p in report.problems if p.rule == "axes"] == [
        ("scene:overview", "axes", True), ("scene:synthase", "axes", True)]


def test_a_bundle_written_before_axes_has_none():
    data = bundle_data()
    assert "axes" not in data["scenes"][0]
    assert not any(scene.axes for scene in Bundle.model_validate(data).scenes)


def test_a_second_flow_keeps_how_it_moves_and_a_chorus_is_held_to_what_moves_not_how():
    def change(data):
        direction(data, "v1-1").update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺", moves="crosses",
                                       also=[{"particles": ["Cytochrome b6f", "Thylakoid"], "carries": "filtrate",
                                              "moves": "travels"}])
    bundle, _, _ = review(change)
    line = next(d for d in bundle.direction if d.line_id == "v1-1")
    assert [f.moves for f in line.flows()] == ["crosses", "travels"]  # kept through the review, both flows

    data = bundle_data()
    choruses = [d for d in data["direction"] if d["line_id"].startswith("c")]
    for d in choruses:
        d.update(particles=["Thylakoid", "Cytochrome b6f"], carries="H⁺")
    choruses[-1]["moves"] = "crosses"  # drawn otherwise, moving the same: no chorus moves differently
    assert not [p for p in checks.motion_problems(Bundle.model_validate(data)) if "moves differently" in p.detail]


def test_a_bundle_with_no_song_is_checked_as_the_lesson_alone():
    """STUDIO_VIDEOS=lesson: no tempo is written into its empty music, nothing about a song is recorded, and its key
    lines keep to the narration, in its order, one to a sentence."""
    keys = [{"text": "H⁺ flow spins ATP synthase → ATP", "from_narration": "n2"},
            {"text": "A second on the same sentence", "from_narration": "n2"},
            {"text": "Nowhere to stand", "from_narration": "n9"},
            {"text": "Light splits water → O₂ + H⁺ + e⁻", "from_narration": "n1"}]
    bundle = bundle_of(LessonBundle.model_validate(lesson_bundle_data(keys)).model_dump())
    reviewed, report = checks.review(bundle)
    assert reviewed.music.positive_styles == [] and not reviewed.sung
    assert [(k.from_narration, k.text) for k in reviewed.key_lines] == [
        ("n1", "Light splits water → O₂ + H⁺ + e⁻"), ("n2", "H⁺ flow spins ATP synthase → ATP")]
    dropped = [p for p in report.problems if p.rule == "key-line"]
    assert [p.line_id for p in dropped] == ["n2", "n9"] and all(p.repaired for p in dropped)
    assert not any(p.rule == "tempo" for p in report.problems)
    assert checks.song_length_problems(reviewed) == checks.song_shape_problems(reviewed) == []
    assert checks.style_problems(reviewed, "k-pop, 140 bpm") == [] and checks.motion_problems(reviewed) == []


def test_no_picture_draws_the_analogy_and_its_lines_play_over_the_real_thing():
    # The owner (Nov 1): a water tower, then a nightclub, drawn for the pump made the learner map two pictures. The
    # analogy lives in the words: a scene drawn for it is left out, and its lines, sung or said, play over the scene
    # with the real thing after it, whole and still.
    def analogy_first(extra):
        def change(data):
            analogy = next(s for s in data["scenes"] if s["id"] == "hook")
            analogy["id"] = "analogy"
            data["scenes"] = [s for s in data["scenes"] if s is not analogy]
            data["scenes"].insert(0, analogy)  # just before the overview, the real thing it explains
            if extra:
                data["scenes"].append({**data["scenes"][1], "id": "detail"})
            for d in data["direction"]:
                if d["line_id"] in ("v1-1", "n1"):
                    d.update(scene="analogy", particles=["Thylakoid"], carries="", moves="")
        return change

    for extra in (True, False):
        bundle, found, _ = review(analogy_first(extra))
        directed = {d.line_id: d for d in bundle.direction}
        for line in ("v1-1", "n1"):
            assert directed[line].scene == "overview" and not directed[line].particles  # the real thing, still
        assert ("scenes", "analogy", True) in found
        assert ("analogy" in [s.id for s in bundle.scenes]) == (not extra)  # kept, unshown, when 3 would not be left
        assert not any(d.scene == "analogy" for d in bundle.direction)


def test_a_scene_with_more_structures_than_a_learner_can_follow_is_recorded():
    # Nov 1: the light reactions' overview drew 15 parts; it stayed pale for 30 s and SAM never found 7 of 43.
    def drawing(count, audience="high school"):
        def change(data):
            data["audience"] = audience
            data["scenes"][1]["structures"] += [{"name": f"Part {k}", "aliases": [], "sam_phrase": f"the part {k}",
                                                 "color_hex": "#3366CC"} for k in range(count - 2)]
        return [p for p in review(change)[1] if p[1] == "crowded"]

    assert drawing(8) == [] and drawing(9) == [("scene:overview", "crowded", False)]
    assert drawing(4, "about age 10") == [] and drawing(5, "about age 10") == [("scene:overview", "crowded", False)]
    assert [checks.younger(a) for a in ("about age 10", "6th grade", "middle school", "10-year-olds")] == [True] * 4
    assert [checks.younger(a) for a in ("high school", "college", "9th grade", "adults")] == [False] * 4


def test_sam_is_asked_for_each_colour_in_the_word_the_picture_is_painted_in():
    # Nov 1: the picture names no colour of its own, so the painting has the Colours line's ("NADPH yellow"), and SAM
    # asked for the writer's "lime ball" would look for a colour it was never asked for.
    def phrased(*phrases):
        def change(data):
            for structure, phrase in zip(data["scenes"][1]["structures"], phrases):
                structure["sam_phrase"] = phrase
        bundle, found, _ = review(change)
        return [s.sam_phrase for s in bundle.scenes[1].structures], found

    assert phrased("pale lime blob", "coral wedge") == (["green blob", "orange wedge"],
                                                        [("structures", "colours", True)])
    assert phrased("green blob", "man in orange") == (["green blob", "man in orange"], [])  # as it is painted
    assert phrased("the thylakoid", "red light") == (["the thylakoid", "orange light"],  # only the colour word
                                                     [("structures", "colours", True)])


def test_two_parts_in_one_colour_word_are_recorded():
    # Nov 1: water (#00ACC1) and carbon dioxide (#00897B) were both "teal" to the illustrator, and were painted alike;
    # Louis XVI and Charles I, both a "king in blue", were sung as one king.
    def coloured(overview):
        def change(data):
            for structure, colour in zip(data["scenes"][1]["structures"], overview):
                structure["color_hex"] = colour
        return [(p.line_id, p.detail) for p in review(change)[2].problems if p.rule == "colours"]

    assert coloured(["#2E8B57", "#E07A2F"]) == []
    assert coloured(["#2E8B57", "#43A047"]) == [
        ("scene:overview", 'Thylakoid and Cytochrome b6f are both "green" to the illustrator')]

    def alike(data):
        data["scenes"][1]["structures"][1].update(sam_phrase="king in blue", color_hex="#2F6FDB")
        data["scenes"][2]["structures"][0].update(sam_phrase="king in blue", color_hex="#3366CC")
    assert [(p.line_id, p.detail) for p in review(alike)[2].problems] == [
        ("structures", 'Cytochrome b6f and ATP synthase are drawn alike, each a "king in blue", so they read as one')]


def test_a_part_named_for_an_ion_is_painted_in_the_ions_colour():
    # Nov 1: the pump's sodium channel was green, as the K⁺ dots were, and its potassium channel purple, as Na⁺: the
    # colours taught the swap. A part named for two ions keeps its own; so does every other part.
    def part(name, colour, phrase):
        return {"name": name, "aliases": [], "sam_phrase": phrase, "color_hex": colour}

    data = {"scenes": [{"id": "overview", "structures": [
        part("Sodium channel", "#22A355", "green tube"), part("Potassium channel", "#8B5CF6", "purple tube"),
        part("Sodium-potassium pump", "#F57C1F", "orange blob"), part("Phosphate", "#8CC63F", "green ball")],
        "image_prompt": "On the left a green sodium channel; in the centre the orange sodium-potassium pump; on the "
                        "right a purple potassium channel."}], "direction": []}
    problems = []
    checks._check_colours(data, problems)
    parts = {s["name"]: (s["color_hex"], s["sam_phrase"]) for s in data["scenes"][0]["structures"]}
    assert parts == {"Sodium channel": (checks.MARKER_COLOURS["Na⁺"], "light purple tube"),
                     "Potassium channel": (checks.MARKER_COLOURS["K⁺"], "green tube"),
                     "Sodium-potassium pump": ("#F57C1F", "orange blob"), "Phosphate": ("#8CC63F", "green ball")}
    assert data["scenes"][0]["image_prompt"] == ("On the left a light purple sodium channel; in the centre the orange "
                                                 "sodium-potassium pump; on the right a green potassium channel.")
    assert problems[0].detail == ("a part named for an ion is painted in its colour: Sodium channel green as light "
                                  "purple; Potassium channel purple as green") and problems[0].repaired
    assert [checks._names_kinds(n, checks.IONS) for n in ("Na⁺ channel", "Proton pumps", "ATP synthase")] == [
        ["Na⁺"], ["H⁺"], []]


def test_an_alias_that_is_a_word_of_the_topic_is_recorded():
    # Nov 1: Sunlight's alias "light" revealed it on every "light reactions".
    def aliased(name, *aliases):
        def change(data):
            data["scenes"][1]["structures"][1].update(name=name, aliases=list(aliases))
        return [(p.line_id, p.rule, p.repaired) for p in review(change)[2].problems if p.rule == "alias"]

    assert aliased("Sunlight", "light", "sun") == [("structures", "alias", False)]
    assert aliased("Light-harvesting complex", "light") == []  # a word of its own name: the topic's word means it
    assert aliased("Sunlight", "sun") == []
