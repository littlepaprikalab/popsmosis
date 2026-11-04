import base64
import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import checks, jobs, knobs, ledger, media, styles
from studio.steps import audio
from studio.tests import sample_media

AS_MP3, DURATION = audio.as_mp3, audio.duration  # the real ones, which the job fixture stands in for


class FakeEleven:
    """Stands in for the ElevenLabs API: records each request, answers like the real one."""

    def __init__(self, status=200):
        self.status = status
        self.requests = []

    def __call__(self, method, url, headers, body):
        request = json.loads(body)
        self.requests.append((url, headers, request))
        if self.status != 200:
            return self.status, {}, json.dumps({"detail": {"message": "quota exceeded"}}).encode()
        if "/v1/music" in url:
            return 200, {"song-id": "song123", "Content-Type": "audio/mpeg"}, b"ID3 song"
        text = request["text"]
        answer = {"audio_base64": base64.b64encode(b"ID3 speech").decode(),
                  "alignment": {"characters": list(text),
                                "character_start_times_seconds": [i * 0.05 for i in range(len(text))],
                                "character_end_times_seconds": [(i + 1) * 0.05 for i in range(len(text))]}}
        return 200, {"character-cost": str(len(text))}, json.dumps(answer).encode()

    def urls(self):
        return [url for url, _, _ in self.requests]


class FakeGemini(FakeEleven):
    """Also stands in for the Gemini API: generateContent answers with a song, the Interactions API with speech, or
    either with what it is given."""

    def __init__(self, status=200, answer=None, song=b"ID3 lyria", mime_type="audio/mpeg", speech=None,
                 speech_status=200):
        super().__init__()
        self.gemini_status, self.answer, self.song, self.mime_type = status, answer, song, mime_type
        self.speech_answer, self.speech_status = speech, speech_status

    def __call__(self, method, url, headers, body):
        if "generativelanguage.googleapis.com" not in url:
            return super().__call__(method, url, headers, body)
        self.requests.append((url, headers, json.loads(body)))
        if url.endswith("/interactions"):
            if self.speech_status != 200:
                error = {"code": self.speech_status, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED"}
                return self.speech_status, {}, json.dumps({"error": error}).encode()
            answer = self.speech_answer or {
                "id": "int42", "object": "interaction", "status": "completed",
                "steps": [{"type": "model_output", "content": [
                    {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(b"RIFF speech").decode()}]}],
                "usage": {"total_input_tokens": 600, "total_output_tokens": 2500},
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(answer).encode()
        if self.gemini_status != 200:
            error = {"code": self.gemini_status, "message": "Lyria could not make it", "status": "INVALID_ARGUMENT"}
            return self.gemini_status, {}, json.dumps({"error": error}).encode()
        answer = self.answer or {
            "candidates": [{"content": {"role": "model", "parts": [
                {"text": "[Verse]\nLight lands on the thylakoid"},
                {"inlineData": {"mimeType": self.mime_type, "data": base64.b64encode(self.song).decode()}},
            ]}, "finishReason": "STOP"}],
            "modelVersion": "lyria-3.5", "responseId": "resp42", "usageMetadata": {"promptTokenCount": 210},
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(answer).encode()

    def gemini(self):
        """The songs asked of Lyria."""
        return [r for r in self.requests if "generativelanguage.googleapis.com" in r[0] and ":generateContent" in r[0]]

    def speech(self):
        """The narrations asked of Gemini's text-to-speech."""
        return [r for r in self.requests if r[0].endswith("/interactions")]


def sing(job, provider):
    """Who sings the song, as an admin sets it on /admin/studio and the claim keeps it with the job."""
    jobs.keep_studio_settings(job, {**jobs.studio_settings(job), "song": {"provider": provider}})


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    for name in ("ELEVENLABS_VOICE_ID", "GEMINI_SONG_MODEL", "GEMINI_TTS_VOICE", audio.ELEVENLABS_PRICE_VAR):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audio, "duration", lambda path: 30.0)
    monkeypatch.setattr(audio, "sleep", lambda seconds: None)
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)  # the fakes' audio is not real
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    sing(job, "elevenlabs")  # most tests here fake ElevenLabs; the default is Lyria (test below)
    return job


def test_lyria_sings_when_no_one_has_chosen(tmp_path):
    job = tmp_path / "unset"
    job.mkdir()
    assert audio.song_provider(job) == "gemini"


def test_the_song_is_one_chunk_per_section_timed_by_its_lines(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    music = [r for r in fake.requests if "/v1/music" in r[0]]
    assert len(music) == 1
    url, headers, body = music[0]
    assert headers["xi-api-key"] == "test-key"
    assert body["model_id"] == "music_v2_5" and "prompt" not in body
    chunks = body["composition_plan"]["chunks"]
    assert [c["text"].split("\n")[0] for c in chunks] == ["[Verse]", "[Chorus]", "[Verse]", "[Chorus]", "[Bridge]",
                                                         "[Chorus]"]
    assert chunks[0]["text"] == "[Verse]\nLight lands on the thylakoid\nEach electron hand-off pumps a proton"
    # as long as its lines take to sing: 7 and 10 syllables at 4 a second, and a second's rest each; the
    # one-line sections are held to the 3 s a chunk must last
    assert [c["duration_ms"] for c in chunks] == [6250, 3000, 3000, 3000, 3000, 3000]
    assert all(c["positive_styles"] == ["upbeat pop", "112 bpm", audio.DICTION]
               and c["negative_styles"] == ["mumbled vocals", *audio.AVOID] for c in chunks)
    plan = json.loads((job / audio.plan_path("song")).read_text())
    assert plan["song_id"] == "song123"
    assert [(s["line_ids"], s["start_ms"]) for s in plan["sections"][:2]] == [(["v1-1", "v1-2"], 0), (["c1-1"], 6250)]
    assert (job / "audio/song.mp3").read_bytes() == b"ID3 song"


@pytest.mark.parametrize("tags", [["pop-punk anthem at 150 bpm"], ["laid-back R&B", "85 bpm"], ["upbeat pop"]])
def test_a_section_takes_the_time_its_lines_need_to_sing_at_any_tempo(tags):
    data = sample_media.bundle().model_dump()
    data["music"]["positive_styles"] = tags
    plan, placed = audio.composition_plan(sample_media.bundle().model_validate(data))
    assert plan["chunks"][0]["duration_ms"] == 6250  # the first verse: two lines, 17 syllables
    assert placed[1]["start_ms"] == 6250
    data["lyrics"][1]["text"] = "Each hand-off, every single time, pumps one more out"
    plan, _ = audio.composition_plan(sample_media.bundle().model_validate(data))
    assert plan["chunks"][0]["duration_ms"] == 7000  # 13 syllables: more to sing, more time


def test_the_song_can_have_an_intro_pre_choruses_and_an_outro():
    data = sample_media.bundle().model_dump()
    first = data["lyrics"][0]
    data["lyrics"] = [{**first, "id": "i-1", "section": "intro"}, *data["lyrics"],
                      {**first, "id": "pc-1", "section": "pre-chorus"}, {**first, "id": "o-1", "section": "outro"}]
    data["music"]["sections"] = ["intro", *data["music"]["sections"], "pre-chorus", "outro"]
    data["direction"].append({**data["direction"][0], "line_id": "i-1"})
    data["direction"] += [{**data["direction"][0], "line_id": i} for i in ("pc-1", "o-1")]
    plan, _ = audio.composition_plan(sample_media.bundle().model_validate(data))
    heads = [c["text"].split("\n")[0] for c in plan["chunks"]]
    assert heads[0] == "[Intro]" and heads[-2:] == ["[Pre-Chorus]", "[Outro]"]


def test_a_chunk_is_never_shorter_than_the_api_allows():
    bundle = sample_media.bundle()
    data = bundle.model_dump()
    data["lyrics"] = [{**line, "text": "x"} for line in data["lyrics"]]
    plan, _ = audio.composition_plan(bundle.model_validate(data))
    assert all(c["duration_ms"] >= audio.MIN_CHUNK_MS for c in plan["chunks"])


def test_speech_keeps_its_character_timings_and_where_each_line_is(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    tts = [r for r in fake.requests if "text-to-speech" in r[0]]
    assert [url.split("?")[0].rsplit("/", 2)[-2] for url, _, _ in tts] == [audio.DEFAULT_VOICE]
    narration = json.loads((job / audio.plan_path("narration")).read_text())
    lines = [(n.id, n.text) for n in sample_media.bundle().narration]
    assert narration["text"] == " ".join(text for _, text in lines)
    for span, (line_id, text) in zip(narration["lines"], lines):
        assert span["id"] == line_id and narration["text"][span["start"]:span["end"]] == text
    assert len(narration["alignment"]["characters"]) == len(narration["text"])
    assert (job / "audio/narration.mp3").read_bytes() == b"ID3 speech"
    assert audio.is_done(job)


def test_the_voice_can_be_changed(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice42")
    audio.run(job)
    assert sum("/text-to-speech/voice42/with-timestamps" in url for url in fake.urls()) == 1


def test_the_voice_set_on_the_studio_page_wins_over_this_machines(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice42")
    jobs.keep_studio_settings(job, {**jobs.studio_settings(job), "knobs": {"ELEVENLABS_VOICE_ID": "voice7"}})  # as the claim carried it
    audio.run(job)
    assert sum("/text-to-speech/voice7/with-timestamps" in url for url in fake.urls()) == 1
    assert json.loads((job / audio.plan_path("narration")).read_text())["voice_id"] == "voice7"


def test_made_files_are_never_asked_for_again_until_their_lines_change(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    assert len(fake.requests) == 2
    assert "nothing" in audio.run(job) and len(fake.requests) == 2

    bundle = sample_media.bundle()
    data = bundle.model_dump()
    data["narration"][0]["text"] = "The light reactions happen on the thylakoid membrane."
    jobs.write(job / "bundle.json", bundle.model_validate(data))
    assert not audio.is_done(job)
    audio.run(job)
    assert len(fake.requests) == 3 and "text-to-speech" in fake.requests[-1][0]


def test_a_refusal_or_a_missing_key_fails_the_step(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven(status=401))
    with pytest.raises(media.StepFailed, match="quota exceeded"):
        audio.run(job)
    assert not (job / "audio/song.mp3").exists()
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(media.StepFailed, match="ELEVENLABS_API_KEY"):
        audio.run(job)


def test_a_busy_server_or_a_dropped_connection_is_tried_again(job, monkeypatch):
    fake, answers, waits = FakeEleven(), [], []
    monkeypatch.setattr(audio, "sleep", waits.append)

    def flaky(method, url, headers, body):
        answers.append(url)
        if len(answers) == 1:
            return 429, {"Retry-After": "30"}, b'{"detail": "too many concurrent requests"}'
        if len(answers) == 2:
            raise audio.NoAnswer("connection reset")
        return fake(method, url, headers, body)

    monkeypatch.setattr(audio, "send", flaky)
    audio.run(job)
    assert waits == [30.0, 20] and len(fake.requests) == 2 and audio.is_done(job)


def test_a_timeout_or_a_server_that_stays_busy_fails_without_more_calls(job, monkeypatch):
    calls = []

    def times_out(method, url, headers, body):
        calls.append(url)
        raise media.StepFailed("ElevenLabs took over 600s to answer")

    monkeypatch.setattr(audio, "send", times_out)
    with pytest.raises(media.StepFailed, match="600s"):
        audio.run(job)
    # Each file asked for once, never again: it may still be being made, and billed.
    assert len(calls) == 2 and sum("/v1/music" in url for url in calls) == 1

    monkeypatch.setattr(audio, "send", FakeEleven(status=503))
    with pytest.raises(media.StepFailed, match="503"):
        audio.run(job)
    assert len(audio.send.requests) == 2 * (1 + len(audio.RETRY_WAITS_S))


def test_a_song_cut_off_before_its_audio_is_written_is_made_again(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    (job / "audio/song.plan.json").unlink()  # as if killed between the two writes of an older layout
    assert not audio.is_done(job)
    audio.run(job)
    assert sum("/v1/music" in url for url in fake.urls()) == 2 and audio.is_done(job)


def test_speech_too_long_for_one_call_fails_before_spending_anything(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    data = sample_media.bundle().model_dump()
    data["narration"][0]["text"] = "word " * 2100
    jobs.write(job / "bundle.json", sample_media.bundle().model_validate(data))
    with pytest.raises(media.StepFailed, match="characters"):
        audio.run(job)
    assert not any("text-to-speech" in url for url in fake.urls())


# --- the song from Lyria on the Gemini API (set to gemini on /admin/studio) -------------------------


def test_the_prompt_says_which_sections_are_rapped():
    # The owner, Nov 2: three songs whose styles said "rapped verses" came back sung all through, the prompt's line
    # saying "Sing the lines in order".
    assert audio.delivery(["upbeat pop, 112 bpm"]) == ""  # singing the lyrics in order goes without saying
    assert audio.delivery(["Latin pop, easygoing rapped verses, a catchy sung chorus, 100 bpm"]) == (
        "Rap every verse, spoken on the beat, not sung.")  # cut to "Rap every verse.", it was sung; no "sing the
    # chorus" since Nov 3, which could contradict a style that chants or raps its hook
    assert audio.delivery(["teen pop, a sung first verse and a rapped second verse"]).startswith("Rap the second verse,")
    assert audio.delivery(["dance-pop, short sung verses and a rapped bridge"]).startswith("Rap the bridge,")
    assert all(checks.RAPPED_VERSES.search(style) and "Rap every verse" in audio.delivery([style])
               for style in styles.SONG_STYLES)


def test_gemini_sings_the_song_from_one_prompt_and_reads_the_narration(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    summary = audio.run(job)
    assert "song (gemini)" in summary and "narration (gemini)" in summary
    (url, headers, body), = fake.gemini()
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/lyria-3.5:generateContent"
    assert headers["x-goog-api-key"] == "gemini-key" and "xi-api-key" not in headers
    assert body["generationConfig"] == {"responseModalities": ["AUDIO", "TEXT"]}
    prompt = body["contents"][0]["parts"][0]["text"]
    # the style and the lyrics: nothing to avoid (the owner, Nov 2), no length and no timings, so the singer takes
    # the time it needs
    # a sung style: the style, then each line once and in order (Nov 4), and no more
    assert prompt.startswith(f"Style: upbeat pop, 112 bpm, {audio.DICTION}.\n{audio.AS_WRITTEN}\n\nLyrics:\n")
    assert "ad-lib" not in prompt and "exactly as written" not in prompt  # nothing of how the words are sung
    assert "Avoid" not in prompt
    assert "Length" not in prompt and "Structure" not in prompt and "0:0" not in prompt
    assert prompt.endswith("Lyrics:\n[Verse]\nLight lands on the thylakoid\nEach electron hand-off pumps a proton"
                           "\n\n[Chorus]\nProtons flow and ATP grows\n\n[Verse]\nOnly b6f pumps them in"
                           "\n\n[Chorus]\nProtons flow and ATP grows\n\n[Bridge]\nFeel the gradient build"
                           "\n\n[Chorus]\nProtons flow and ATP grows")
    assert not any("api.elevenlabs.io" in u for u in fake.urls())  # nothing from ElevenLabs: its quota may be gone

    plan = json.loads((job / audio.plan_path("song")).read_text())
    assert plan["provider"] == "gemini" and plan["model_id"] == "lyria-3.5" and plan["sections_pinned"] is False
    assert plan["sections"] == audio.composition_plan(sample_media.bundle())[1]  # the same planned layout
    assert plan["text"] == "[Verse]\nLight lands on the thylakoid" and plan["finish_reason"] == "STOP"
    assert plan["response_id"] == "resp42" and plan["mime_type"] == "audio/mpeg"
    assert (job / "audio/song.mp3").read_bytes() == b"ID3 lyria"
    assert audio.is_done(job)


def test_switching_the_provider_makes_only_the_song_again(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    assert len(fake.requests) == 2 and json.loads((job / audio.plan_path("song")).read_text())["provider"] == "elevenlabs"

    sing(job, "gemini")
    assert not audio.is_done(job)
    audio.run(job)
    assert len(fake.requests) == 3 and len(fake.gemini()) == 1  # the speech was kept
    assert "nothing" in audio.run(job) and len(fake.requests) == 3

    sing(job, "elevenlabs")
    audio.run(job)
    assert len(fake.requests) == 4 and "/v1/music" in fake.requests[-1][0]


def test_a_song_made_before_there_was_a_choice_counts_as_elevenlabs(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven())
    audio.run(job)
    plan = json.loads((job / audio.plan_path("song")).read_text())
    del plan["provider"], plan["sections_pinned"]
    jobs.write(job / audio.plan_path("song"), plan)
    assert audio.is_done(job)
    sing(job, "gemini")
    assert not audio.is_done(job)


def test_the_lyria_model_can_be_changed(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    monkeypatch.setenv("GEMINI_SONG_MODEL", "lyria-3-clip-preview")
    audio.run(job)
    assert fake.gemini()[0][0].endswith("/models/lyria-3-clip-preview:generateContent")
    assert json.loads((job / audio.plan_path("song")).read_text())["model_id"] == "lyria-3-clip-preview"


def test_the_lyria_model_and_the_credits_price_set_on_the_studio_page_win_over_this_machines(job, monkeypatch):
    monkeypatch.setenv("GEMINI_SONG_MODEL", "lyria-3-clip-preview")
    monkeypatch.setenv(audio.ELEVENLABS_PRICE_VAR, "cheap")
    knobs.use({"knobs": {"GEMINI_SONG_MODEL": "lyria-3.5", audio.ELEVENLABS_PRICE_VAR: 0.3}})
    assert audio.gemini_song_model() == "lyria-3.5"
    assert audio.eleven_usd_per_1k_credits() == 0.3


@pytest.mark.parametrize("fake, reason", [
    (FakeGemini(status=400), "400 INVALID_ARGUMENT Lyria could not make it"),
    (FakeGemini(answer={"candidates": [{"content": {"parts": [{"text": "I can't sing that."}]},
                                        "finishReason": "RECITATION"}]}), "no song .finish reason: RECITATION"),
    (FakeGemini(answer={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/mpeg", "data": "%%"}}]}}]}),
     "not readable audio"),
])
def test_a_gemini_refusal_or_block_fails_the_step_and_keeps_nothing(job, monkeypatch, fake, reason):
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    with pytest.raises(media.StepFailed, match=reason):
        audio.run(job)
    assert not (job / "audio/song.mp3").exists() and not (job / audio.plan_path("song")).exists()
    # The voice was made at the same time and is kept, so a retry asks for the song alone.
    assert len(fake.gemini()) == 1 and (job / "audio/narration.mp3").exists()
    assert not any("/v1/music" in u for u in fake.urls())  # only what Lyria will not sing goes to Eleven Music


@pytest.mark.parametrize("answer, reason", [
    ({"promptFeedback": {"blockReason": "OTHER"}}, "Gemini blocked the song's prompt: OTHER"),
    ({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}, "Gemini blocked the song's prompt: PROHIBITED_CONTENT"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]},
     "Gemini sent back no song (finish reason: SAFETY)"),
])
def test_a_song_lyria_will_not_sing_is_made_by_eleven_music(job, monkeypatch, answer, reason):
    fake = FakeGemini(answer=answer)
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    assert f"song (elevenlabs, as {reason})" in audio.run(job)
    assert len(fake.gemini()) == 1 and sum("/v1/music" in u for u in fake.urls()) == 1
    plan = json.loads((job / audio.plan_path("song")).read_text())
    assert plan["provider"] == "elevenlabs" and plan["sections_pinned"] is True  # step 6 finds its lines where planned
    assert plan["instead_of"] == {"provider": "gemini", "model_id": "lyria-3.5", "reason": reason}
    assert (job / "audio/song.mp3").read_bytes() == b"ID3 song"
    # Lyria's refusal cost nothing; the song is billed as Eleven Music's.
    assert [r["model"] for r in _logged(job) if r["label"] == "song"] == ["music_v2_5"]

    # Made, it is not asked for again, of either: Lyria would refuse it again.
    assert audio.is_done(job) and "nothing" in audio.run(job)
    sing(job, "elevenlabs")
    assert audio.is_done(job)
    assert len(fake.requests) == 3


def test_new_lyrics_go_to_lyria_first_again(job, monkeypatch):
    fake = FakeGemini(answer={"promptFeedback": {"blockReason": "OTHER"}})
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    audio.run(job)
    data = sample_media.bundle().model_dump()
    data["lyrics"][0]["text"] = "Light lands on the thylakoid membrane"
    jobs.write(job / "bundle.json", sample_media.bundle().model_validate(data))
    fake.answer = None  # Lyria sings these
    assert "song (gemini)" in audio.run(job)
    assert len(fake.gemini()) == 2 and sum("/v1/music" in u for u in fake.urls()) == 1
    assert "instead_of" not in json.loads((job / audio.plan_path("song")).read_text())


def test_a_song_neither_will_make_fails_the_step_naming_both(job, monkeypatch):
    fake = FakeGemini(answer={"promptFeedback": {"blockReason": "OTHER"}})

    def send(method, url, headers, body):
        if "/v1/music" in url:
            fake.requests.append((url, headers, json.loads(body)))
            return 422, {}, json.dumps({"detail": {"status": "bad_composition_plan"}}).encode()
        return fake(method, url, headers, body)

    monkeypatch.setattr(audio, "send", send)
    sing(job, "gemini")
    with pytest.raises(media.StepFailed, match="^Gemini blocked the song's prompt: OTHER, and Eleven Music could not "
                                               "make it instead: Eleven Music refused the song: 422 .*bad_composition"):
        audio.run(job)
    assert not (job / "audio/song.mp3").exists() and not (job / audio.plan_path("song")).exists()
    assert (job / "audio/narration.mp3").exists()


def test_every_key_the_step_needs_is_checked_before_anything_is_spent(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(media.StepFailed, match="^GEMINI_API_KEY is not set"):
        audio.run(job)  # the narration needs it too: the song is not bought without it
    sing(job, "elevenlabs")
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(media.StepFailed, match="^ELEVENLABS_API_KEY is not set"):
        audio.run(job)
    assert fake.requests == []
    # Gemini makes it all with no ElevenLabs key at all: a droplet is made with ElevenLabs out of reach.
    sing(job, "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    audio.run(job)
    assert audio.is_done(job) and len(fake.gemini()) == 1 == len(fake.speech()) and len(fake.requests) == 2


def test_an_unknown_provider_fails_before_any_call(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "suno")
    with pytest.raises(media.StepFailed, match="the song's provider 'suno' is not one this studio has .elevenlabs, gemini.: update the studio"):
        audio.run(job)
    assert fake.requests == []


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name:stream=bit_rate",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    found = json.loads(out)
    return found["format"]["format_name"], found["streams"][0]["bit_rate"]


def _sound(seconds, *args):
    """Stereo 44.1 kHz noise, near silent for its first half and loud after (as a song's intro and
    chorus differ), encoded with ffmpeg's args, as bytes."""
    source = f"anoisesrc=d={seconds}:a=0.5:r=44100,volume='if(lt(t,{seconds / 2}),0.0005,1)':eval=frame"
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, "-ac", "2", *args, "pipe:1"],
                          capture_output=True, check=True).stdout


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("sent, mime_type", [
    (("-f", "wav"), "audio/wav"),
    # a variable-bitrate MP3 with no length header: its length cannot be read from it as it is
    (("-codec:a", "libmp3lame", "-q:a", "0", "-write_xing", "0", "-f", "mp3"), "audio/mpeg"),
])
def test_lyrias_song_is_kept_as_an_mp3_of_its_true_length(job, monkeypatch, sent, mime_type):
    song = _sound(6, *sent)
    if "-write_xing" in sent:
        assert abs(DURATION_OF(song) - 6.0) > 0.15  # as sent, its length reads wrong (3% here)
    monkeypatch.setattr(audio, "as_mp3", AS_MP3)
    speech = {"id": "int7", "status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(_sound(2, "-f", "wav")).decode()}]}]}
    monkeypatch.setattr(audio, "send", FakeGemini(song=song, mime_type=mime_type, speech=speech))
    sing(job, "gemini")
    audio.run(job)
    # A WAV is encoded once, at a constant rate; an MP3 is copied as it was sent, with a length header.
    assert _probe(job / "audio/song.mp3")[0] == "mp3"
    assert mime_type == "audio/mpeg" or _probe(job / "audio/song.mp3") == ("mp3", "192000")
    assert _probe(job / "audio/narration.mp3") == ("mp3", "128000")  # Gemini's WAV narration, kept as ElevenLabs' are
    assert abs(DURATION(job / "audio/song.mp3") - 6.0) < 0.1  # the MP3's own padding, a few hundredths
    assert json.loads((job / audio.plan_path("song")).read_text())["mime_type"] == mime_type


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_song_ffmpeg_cannot_read_fails_the_step_and_keeps_nothing(job, monkeypatch):
    monkeypatch.setattr(audio, "as_mp3", AS_MP3)
    monkeypatch.setattr(audio, "send", FakeGemini(song=b"not audio at all", mime_type="audio/mpeg"))
    sing(job, "gemini")
    with pytest.raises(media.StepFailed, match="cannot keep Gemini's song .audio/mpeg. as an MP3"):
        audio.run(job)
    assert not (job / "audio/song.mp3").exists() and not (job / audio.plan_path("song")).exists()



def DURATION_OF(data):
    """The length ffprobe reads from audio bytes, as the later steps would read it from a file."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        f.write(data)
        f.flush()
        return DURATION(Path(f.name))


def test_a_gemini_quota_error_waits_as_long_as_it_asks(job, monkeypatch):
    fake, waits = FakeGemini(), []
    monkeypatch.setattr(audio, "sleep", waits.append)
    sing(job, "gemini")
    quota = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"}]}}

    def busy_once(method, url, headers, body):
        if ":generateContent" in url and not fake.gemini():
            fake.requests.append((url, headers, json.loads(body)))
            return 429, {}, json.dumps(quota).encode()
        return fake(method, url, headers, body)

    monkeypatch.setattr(audio, "send", busy_once)
    audio.run(job)
    assert waits == [33.0] and len(fake.gemini()) == 2 and audio.is_done(job)
    assert audio._retry_after({}, b"not json") == 0.0 and audio._retry_after({"Retry-After": "900"}) == 300.0


# --- what it spent (studio/ledger.py) ---------------------------------------------------------------


def _logged(job):
    return [json.loads(row) for row in (job / "calls.jsonl").read_text().splitlines()]


def test_each_elevenlabs_call_is_logged_in_the_credits_it_cost(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven())
    audio.run(job)
    rows = {r["label"]: r for r in _logged(job)}  # made at the same time, so logged in any order
    assert {label: (r["role"], r["model"]) for label, r in rows.items()} == {
        "song": ("audio", "music_v2_5"), "narration": ("audio", "eleven_multilingual_v2")}
    # The fake's music answer names no cost; its speech costs a credit a character. No plan price: no dollars.
    assert rows["song"]["credits"] is None and rows["song"]["cost_usd"] is None
    narration = json.loads((job / audio.plan_path("narration")).read_text())["text"]
    assert rows["narration"]["credits"] == len(narration) and rows["narration"]["cost_usd"] is None


def test_the_plans_price_of_credits_turns_them_into_dollars(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setenv(audio.ELEVENLABS_PRICE_VAR, "0.30")
    audio.run(job)
    narration = next(r for r in _logged(job) if r["label"] == "narration")
    assert narration["cost_usd"] == pytest.approx(narration["credits"] * 0.30 / 1000)


@pytest.mark.parametrize("value", ["cheap", "-1", "nan", "inf"])
def test_a_price_that_is_not_one_leaves_the_credits_alone(job, monkeypatch, value):
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setenv(audio.ELEVENLABS_PRICE_VAR, value)
    audio.run(job)
    narration = next(r for r in _logged(job) if r["label"] == "narration")
    assert narration["cost_usd"] is None and narration["credits"]


def test_a_lyria_song_is_logged_at_its_models_price(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeGemini())
    sing(job, "gemini")
    audio.run(job)
    song = next(r for r in _logged(job) if r["label"] == "song")
    assert (song["label"], song["model"], song["cost_usd"]) == ("song", "lyria-3.5", 0.08)


def test_a_refused_call_is_not_logged_as_spent(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven(status=401))
    with pytest.raises(media.StepFailed):
        audio.run(job)
    assert not (job / "calls.jsonl").exists()



@pytest.mark.parametrize("header", ["nan", "inf", "-3", "lots", ""])
def test_a_credit_count_that_is_not_one_is_not_kept(header):
    assert audio.eleven_credits({"character-cost": header}) is None
    assert audio.eleven_credits({"Character-Cost": "120"}) == 120.0


def test_a_lyria_answer_with_no_song_is_not_logged_as_one(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeGemini(answer={"candidates": [{"finishReason": "RECITATION"}]}))
    sing(job, "gemini")
    with pytest.raises(media.StepFailed, match="no song"):
        audio.run(job)
    assert "song" not in [r["label"] for r in _logged(job)]  # only the voice, made at the same time


def test_a_topic_past_its_spend_ceiling_asks_for_no_song_or_voice(job, monkeypatch):
    from studio import ledger

    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    jobs.keep_studio_settings(job, {"spend": {"topicUsd": 0.5}})
    (job / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "claude-opus-5-5", "cost_usd": 0.6}) + "\n")
    with pytest.raises(ledger.OverCeiling):
        audio.run(job)
    assert fake.requests == []


def test_the_lesson_alone_makes_only_the_narration(job, monkeypatch):
    """STUDIO_VIDEOS=lesson on /admin/studio: no song is asked for, only the narration, from the audio's provider."""
    sing(job, "gemini")
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    assert audio.run(job) == "made narration (gemini); narration 30.0s"
    assert not fake.gemini() and len(fake.speech()) == 1 and not any("api.elevenlabs.io" in url for url in fake.urls())
    assert (job / media.audio_path("narration")).exists() and not (job / media.audio_path("song")).exists()
    assert audio.is_done(job)
    knobs.use({"knobs": {"STUDIO_VIDEOS": "on"}})
    assert not audio.is_done(job)  # both videos again: the song is still to make


# --- the narration from Gemini's text-to-speech (gemini on /admin/studio) ---------------------------


def test_gemini_reads_the_narration_in_one_call_and_keeps_no_timings(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    audio.run(job)
    (url, headers, body), = fake.speech()
    assert url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert headers["x-goog-api-key"] == "gemini-key"
    text, spans = audio.join_lines(audio.speech_lines(sample_media.bundle(), "narration"))
    assert body == {
        "model": "gemini-3.8-flash-tts",
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text, "annotations": [
            {"type": "speech_metadata", "style": audio.GEMINI_TTS_STYLE}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "en-gb-tutor-12"}]},
        "store": False,
    }
    plan = json.loads((job / audio.plan_path("narration")).read_text())
    assert plan["provider"] == "gemini" and plan["voice"] == "en-gb-tutor-12" and plan["model_id"] == "gemini-3.8-flash-tts"
    assert plan["text"] == text and plan["lines"] == spans and plan["interaction_id"] == "int42"
    assert plan["mime_type"] == "audio/wav" and "alignment" not in plan  # step 6 aligns it
    assert (job / "audio/narration.mp3").read_bytes() == b"RIFF speech"  # as_mp3 is stood in for
    narration, = [json.loads(row) for row in (job / "calls.jsonl").read_text().splitlines()
                  if json.loads(row)["label"] == "narration"]
    assert narration["model"] == "gemini-3.8-flash-tts" and narration["cost_usd"] > 0


def test_a_narration_made_stays_whoever_read_it(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    audio.run(job)
    sing(job, "elevenlabs")
    audio.run(job)  # the song only: Eleven Music sings now, the Gemini narration is kept
    assert [u for u in fake.urls() if "api.elevenlabs.io" in u] == ["https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128"]
    assert json.loads((job / audio.plan_path("narration")).read_text())["provider"] == "gemini"


def test_the_gemini_voice_can_be_changed_on_the_studio_page(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Kore")
    jobs.keep_studio_settings(job, {**jobs.studio_settings(job), "knobs": {"GEMINI_TTS_VOICE": "Sulafat"}})
    knobs.use(jobs.studio_settings(job))
    audio.run(job)
    knobs.use({"knobs": {}})
    body = fake.speech()[0][2]
    assert body["generation_config"]["speech_config"] == [{"voice": "Sulafat"}]


@pytest.mark.parametrize("fake, reason", [
    (FakeGemini(speech_status=429), "refused the narration: 429 RESOURCE_EXHAUSTED Resource has been exhausted"),
    (FakeGemini(speech={"id": "i1", "status": "failed", "steps": []}), r"did not finish the narration \(status: failed\)"),
    (FakeGemini(speech={"id": "i1", "status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "text", "text": "I cannot read that."}]}]}), "Gemini sent back no narration"),
])
def test_a_gemini_narration_refused_or_unfinished_fails_the_step_and_keeps_nothing(job, monkeypatch, fake, reason):
    monkeypatch.setattr(audio, "send", fake)
    sing(job, "gemini")
    with pytest.raises(media.StepFailed, match=reason):
        audio.run(job)
    assert not (job / "audio/narration.mp3").exists() and not (job / audio.plan_path("narration")).exists()
    assert (job / "audio/song.mp3").exists()  # what was made is kept: the retry asks only for the narration


def test_a_gemini_narration_costs_its_tokens_at_the_prices_of_the_day():
    usage = {"total_input_tokens": 600, "total_output_tokens": 2500}
    assert audio.gemini_speech_cost(usage, 100.0, datetime.date(2026, 12, 31)) == 0.0228  # $0.50 and $9 a million
    assert audio.gemini_speech_cost(usage, 100.0, datetime.date(2027, 1, 1)) == 0.0456  # doubled from 2027
    # No count of its own: the audio's length, at 25 tokens a second; neither: no price.
    assert audio.gemini_speech_cost(None, 100.0, datetime.date(2026, 12, 31)) == 0.0225
    assert audio.gemini_speech_cost({"total_output_tokens": "lots"}, None) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_song_sent_as_an_mp3_is_kept_frame_for_frame(tmp_path):
    # Each lossy encode costs some of the sound, and the video's AAC is one more: Lyria's MP3 is copied, not encoded
    # again (Nov 2), with the length header a copy writes.
    import subprocess

    import numpy as np

    sent = tmp_path / "sent.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=1.2*sin(2*PI*440*t)+0.05*random(0):s=44100:d=20",
                    "-codec:a", "libmp3lame", "-q:a", "4", "-write_xing", "0", str(sent)], check=True)
    kept = tmp_path / "kept.mp3"
    kept.write_bytes(AS_MP3(sent.read_bytes(), "audio/mpeg", "song"))
    assert DURATION(kept) == pytest.approx(20.0, abs=0.05)

    def decoded(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"], capture_output=True,
                             check=True).stdout
        return np.frombuffer(raw, np.float32)

    before, after = decoded(sent), decoded(kept)
    skipped = len(before) - len(after)  # the copy's header tells the decoder to skip the encoder's delay
    assert 0 <= skipped < 3000
    assert np.array_equal(before[skipped:skipped + len(after)], after)  # the same frames: nothing encoded again
    assert np.abs(after).max() > 1.0  # its peaks as sent: the render holds the mix under full scale


@pytest.mark.parametrize("raised, part", [(media.StepFailed("Eleven Music refused the song: 400 bad"), "song"),
                                          (KeyError("composition_plan"), "song"),
                                          (ledger.OverCeiling("past the topic's ceiling"), "")])
def test_a_song_that_fails_is_the_songs_alone_and_the_narration_is_still_made(tmp_path, monkeypatch, raised, part):
    """The videos can go on with the lesson video alone when the song cannot be made (studio/anneal.py); not past the
    spend ceiling, which would stop the lesson video's own paid calls too."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setattr(audio, "duration", lambda path: 30.0)
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    sing(job, "elevenlabs")

    def fails(*args):
        raise raised

    monkeypatch.setattr(audio, "_make_song", fails)
    with pytest.raises(media.StepFailed) as failed:
        audio.run(job)
    assert failed.value.part == part
    assert audio.made(job, "narration") and not audio.made(job, "song")


def test_a_song_blocked_and_then_refused_for_credit_says_both_in_fixed_words(job, monkeypatch):
    # Nov 4: Gemini blocked the prompt, and Eleven Music had 1 credit left of the 1578 a song needs.
    fake = FakeGemini(answer={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})

    def send(method, url, headers, body):
        if "/v1/music" in url:
            return 401, {}, json.dumps({"detail": {"type": "invalid_request", "code": "quota_exceeded",
                                                   "message": "1 credits remaining"}}).encode()
        return fake(method, url, headers, body)

    monkeypatch.setattr(audio, "send", send)
    sing(job, "gemini")
    with pytest.raises(media.StepFailed) as failed:
        audio.run(job)
    assert failed.value.code == f"{audio.BLOCKED}, and {audio.NO_CREDIT}"


def test_an_account_out_of_credit_has_its_own_code_and_an_outage_stays_an_outage():
    assert audio._code(401, b'{"detail":{"code":"quota_exceeded"}}') == audio.NO_CREDIT
    assert audio._code(402) == audio.NO_CREDIT
    assert audio._code(401, b'{"detail":"invalid api key"}') is None
    assert audio._code(503, b"quota_exceeded") == audio.OUTAGE
