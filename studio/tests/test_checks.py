import json

from studio import checks
from studio.jobs import read, write
from studio.schemas import Bundle, CheckReport, Request
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
    [problem] = read(tmp_path / "checks.json", CheckReport).problems
    assert (problem.line_id, problem.rule, problem.repaired) == ("c1-1", "spotlight", True)
