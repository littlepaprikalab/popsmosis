from types import SimpleNamespace

from studio import brain
from studio.steps import songwriter
from studio.steps.factcheck import check_bundle, couplets
from studio.tests.sample import CHORUS
from studio.tests.test_factcheck import FIRST, FIXED, SEEDED, FakeChecker, bundle, seeded_is_wrong, supported

SUNG = "Only b6f pumps them, that's the motion"  # the songwriter's line for SEEDED: true, and it still sings


def sings(couplets_asked):
    """A songwriter that rewrites the seeded couplet, keeping its first line."""
    return [{"id": c["id"], "lines": [FIRST, SUNG]} for c in couplets_asked if c["id"] == "v1-1,v1-2"]


# --- couplets -----------------------------------------------------------------------


def test_the_song_is_judged_a_couplet_at_a_time_as_it_rhymes():
    lyric = lambda i, section: SimpleNamespace(id=i, section=section, text=f"line {i}")  # noqa: E731
    song = SimpleNamespace(lyrics=[lyric("i1", "intro"), lyric("v1", "verse"), lyric("v2", "verse"),
                                   lyric("v3", "verse"), lyric("v4", "verse"),
                                   *[lyric(f"c{n}", "chorus") for n in range(1, 6)], lyric("o1", "outro")])
    units = couplets(song)
    # Pairs from each section's start; a chorus of five ends on three; a section of one line stands alone.
    assert [u.members for u in units] == [("i1",), ("v1", "v2"), ("v3", "v4"), ("c1", "c2"), ("c3", "c4", "c5"),
                                          ("o1",)]
    assert (units[1].id, units[1].text, units[1].key) == ("v1,v2", "line v1\nline v2", "lyric:v1,v2")


def test_a_couplet_true_by_course_knowledge_passes_but_a_lesson_line_must_rest_on_the_facts():
    def by_knowledge(text):  # "course knowledge" is the only source this checker gives
        return "SUPPORTED", "course knowledge", ""

    fake = FakeChecker(by_knowledge)
    report, _ = check_bundle(bundle(), "", fake)
    lyrics = [line for line in report.lines if line.kind == "lyric"]
    assert lyrics and all(line.verdict == "SUPPORTED" and line.source == "course knowledge" for line in lyrics)
    # Every other line never gets a verdict: course knowledge is no source for it.
    assert report.status == "failed" and "narration n1" in report.reason
    assert not any(i.startswith("lyric:") for i in fake.ids(2))


# --- the songwriter ---------------------------------------------------------------------


def test_a_wrong_couplet_is_rewritten_by_the_songwriter_and_checked_again():
    fake = FakeChecker(seeded_is_wrong, song=sings)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.rounds == 2
    assert [line.text for line in checked.lyrics[:2]] == [FIRST, SUNG]
    [correction] = report.corrections
    assert (correction.by, correction.ids, correction.after) == ("songwriter", ["v1-1", "v1-2"], f"{FIRST}\n{SUNG}")
    assert fake.ids(2) == ["lyric:v1-1,v1-2"]  # the rewrite is judged like any changed line

    [asked] = fake.songs
    assert [line["text"] for line in asked["song"]] == [line.text for line in bundle().lyrics]  # the whole song
    assert (asked["bpm"], asked["audience"], asked["song_style"]) == (112, "high school", "upbeat pop, 112 bpm")
    assert [f["id"] for f in asked["facts"]] == ["f1", "f2", "f3"]
    [couplet] = asked["couplets"]
    assert couplet == {"id": "v1-1,v1-2", "lines": [{"id": "v1-1", "text": FIRST}, {"id": "v1-2", "text": SEEDED}],
                       "problem": "wrong", "true_version": [FIRST, FIXED]}


def test_a_rewrite_that_fails_too_takes_the_checkers_correction_and_goes_to_the_songwriter_once():
    def rule(text):
        if text == SUNG:
            return "UNSUPPORTED", "", "Only b6f pumps them in, that's the motion"
        return seeded_is_wrong(text)

    fake = FakeChecker(rule, song=sings)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and report.rounds == 3 and len(fake.songs) == 1
    assert checked.lyrics[1].text == "Only b6f pumps them in, that's the motion"
    assert [c.by for c in report.corrections] == ["songwriter", "checker"]


def test_a_rewrite_that_does_not_fit_the_song_is_left_for_the_checkers():
    def far_too_long(couplets_asked):
        return [{"id": "v1-1,v1-2", "lines": [FIRST, "Only cytochrome b6f, of all of the carriers in the chain, "
                                                    "pumps protons"]}]

    for song in (far_too_long, lambda asked: [{"id": "v1-1,v1-2", "lines": [SUNG]}]):  # too long; a line short
        report, checked = check_bundle(bundle(), "", FakeChecker(seeded_is_wrong, song=song))
        assert report.status == "passed" and checked.lyrics[1].text == FIXED
        assert report.corrections[0].by == "checker"


def test_a_songwriter_that_fails_never_fails_the_check():
    fake = FakeChecker(seeded_is_wrong)

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        if label.startswith("lyric rewrite"):
            raise brain.Truncated("hit max_tokens")
        return fake(role, prompt, schema, images, system=system, label=label)

    report, checked = check_bundle(bundle(), "", ask)
    assert report.status == "passed" and checked.lyrics[1].text == FIXED


def test_the_songwriter_saves_a_couplet_the_checker_could_not_correct():
    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "") if text == SEEDED else supported(text), song=sings)
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed" and checked.lyrics[1].text == SUNG
    assert fake.songs[0]["couplets"][0]["true_version"] == []


def test_a_chorus_the_songwriter_rewrites_is_sung_so_in_every_chorus():
    hook = "Protons flow back and ATP grows"

    def chorus_is_wrong(text):
        return ("UNSUPPORTED", "", "Protons return and ATP is made") if text == CHORUS else supported(text)

    fake = FakeChecker(chorus_is_wrong, song=lambda asked: [{"id": c["id"], "lines": [hook]} for c in asked])
    report, checked = check_bundle(bundle(), "", fake)
    assert report.status == "passed"
    assert [line.text for line in checked.lyrics if line.section == "chorus"] == [hook] * 3
    assert report.corrections[0].ids == ["c1-1", "c2-1", "c3-1"]


def test_a_line_sung_in_two_couplets_stays_one_line_in_the_choruss_words():
    def hook_opens_the_verse(data):  # as the title line opens the intro and every chorus
        data["lyrics"][0]["text"] = CHORUS

    def rewrites(asked):  # the couplets come back worded differently
        words = {"v1-1,v1-2": ["Protons come back, ATP is made", SEEDED], "c1-1": ["Protons flow back and ATP grows"]}
        return [{"id": c["id"], "lines": words[c["id"]]} for c in asked]

    fake = FakeChecker(lambda text: ("UNSUPPORTED", "", "Protons return and ATP is made") if text == CHORUS
                       else supported(text), song=rewrites)
    report, checked = check_bundle(bundle(hook_opens_the_verse), "", fake)
    assert report.status == "passed"
    assert [line.text for line in checked.lyrics if line.id in ("v1-1", "c1-1", "c2-1", "c3-1")] == [
        "Protons flow back and ATP grows"] * 4
    assert "lyric:v1-1,v1-2" in fake.ids(2)  # the verse couplet, changed by the chorus's words, is checked again


def test_a_rewritten_line_must_stay_near_the_length_of_the_one_it_replaces():
    assert songwriter.syllables("Protons flow and ATP grows") == 8  # ATP is said letter by letter
    assert songwriter.syllables("Only b6f pumps a proton through") == 10
    assert songwriter.fits([SEEDED], [FIXED])
    assert not songwriter.fits([CHORUS], ["Protons flow back through the synthase and the ATP grows and grows"])
    assert not songwriter.fits([FIRST, SEEDED], [FIRST]) and not songwriter.fits([FIRST], [""])
