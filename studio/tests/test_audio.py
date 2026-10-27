import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import jobs, media
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
    """Also stands in for the Gemini API's generateContent: answers with a song, or with what it is given."""

    def __init__(self, status=200, answer=None, song=b"ID3 lyria", mime_type="audio/mpeg"):
        super().__init__()
        self.gemini_status, self.answer, self.song, self.mime_type = status, answer, song, mime_type

    def __call__(self, method, url, headers, body):
        if "generativelanguage.googleapis.com" not in url:
            return super().__call__(method, url, headers, body)
        self.requests.append((url, headers, json.loads(body)))
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
        return [r for r in self.requests if "generativelanguage.googleapis.com" in r[0]]


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    for name in ("ELEVENLABS_VOICE_ID", "SONG_PROVIDER", "GEMINI_SONG_MODEL", audio.ELEVENLABS_PRICE_VAR):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audio, "duration", lambda path: 30.0)
    monkeypatch.setattr(audio, "sleep", lambda seconds: None)
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type: data)  # the fakes' songs are not real audio
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    return job


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
    assert chunks[0]["text"] == "[Verse]\nLight lands on the thylakoid\nEvery electron hand-off pumps a proton"
    assert [c["duration_ms"] for c in chunks] == [8571, 4286, 4286, 4286, 4286, 4286]  # two bars a line at 112 bpm
    assert all(c["positive_styles"] == ["upbeat pop", "112 bpm"] and c["negative_styles"] == ["mumbled vocals"]
               for c in chunks)
    plan = json.loads((job / audio.plan_path("song")).read_text())
    assert plan["song_id"] == "song123"
    assert [(s["line_ids"], s["start_ms"]) for s in plan["sections"][:2]] == [(["v1-1", "v1-2"], 0), (["c1-1"], 8571)]
    assert (job / "audio/song.mp3").read_bytes() == b"ID3 song"


@pytest.mark.parametrize("tags, chorus_ms, chorus_at", [
    (["pop-punk anthem at 150 bpm"], 3200, 6400),
    (["K-pop", "98 bpm"], 4898, 9796),
    (["upbeat pop"], 4286, 8571),  # a bundle from before the styles chose a tempo: 112 bpm
])
def test_each_section_is_timed_by_the_tempo_the_styles_name(tags, chorus_ms, chorus_at):
    data = sample_media.bundle().model_dump()
    data["music"]["positive_styles"] = tags
    plan, placed = audio.composition_plan(sample_media.bundle().model_validate(data))
    assert plan["chunks"][1]["duration_ms"] == chorus_ms  # the first chorus: one line
    assert placed[1]["start_ms"] == chorus_at  # after a two-line verse


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
    assert [url.split("?")[0].rsplit("/", 2)[-2] for url, _, _ in tts] == [audio.DEFAULT_VOICE] * 2
    frq = json.loads((job / audio.plan_path("frq")).read_text())
    bundle = sample_media.bundle()
    assert frq["text"] == " ".join(text for _, text in media.frq_lines(bundle))
    for span, (line_id, text) in zip(frq["lines"], media.frq_lines(bundle)):
        assert span["id"] == line_id and frq["text"][span["start"]:span["end"]] == text
    assert len(frq["alignment"]["characters"]) == len(frq["text"])
    assert (job / "audio/frq.mp3").read_bytes() == b"ID3 speech"
    assert audio.is_done(job)


def test_the_voice_can_be_changed(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice42")
    audio.run(job)
    assert sum("/text-to-speech/voice42/with-timestamps" in url for url in fake.urls()) == 2


def test_made_files_are_never_asked_for_again_until_their_lines_change(job, monkeypatch):
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(job)
    assert len(fake.requests) == 3
    assert "nothing" in audio.run(job) and len(fake.requests) == 3

    bundle = sample_media.bundle()
    data = bundle.model_dump()
    data["frq"]["model_answer_steps"][0] = "Cytochrome b6f pumps protons into the lumen."
    jobs.write(job / "bundle.json", bundle.model_validate(data))
    assert not audio.is_done(job)
    audio.run(job)
    assert len(fake.requests) == 4 and "text-to-speech" in fake.requests[-1][0]


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
    assert waits == [30.0, 20] and len(fake.requests) == 3 and audio.is_done(job)


def test_a_timeout_or_a_server_that_stays_busy_fails_without_more_calls(job, monkeypatch):
    calls = []

    def times_out(method, url, headers, body):
        calls.append(url)
        raise media.StepFailed("ElevenLabs took over 600s to answer")

    monkeypatch.setattr(audio, "send", times_out)
    with pytest.raises(media.StepFailed, match="600s"):
        audio.run(job)
    assert len(calls) == 1  # the song may still be being made, and billed

    monkeypatch.setattr(audio, "send", FakeEleven(status=503))
    with pytest.raises(media.StepFailed, match="503"):
        audio.run(job)
    assert len(audio.send.requests) == 1 + len(audio.RETRY_WAITS_S)


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


# --- the song from Lyria on the Gemini API (SONG_PROVIDER=gemini) ---------------------------------


def test_gemini_sings_the_song_from_one_prompt_and_elevenlabs_still_reads(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    summary = audio.run(job)
    assert "song (gemini)" in summary
    (url, headers, body), = fake.gemini()
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/lyria-3.5:generateContent"
    assert headers["x-goog-api-key"] == "gemini-key" and "xi-api-key" not in headers
    assert body["generationConfig"] == {"responseModalities": ["AUDIO", "TEXT"]}
    prompt = body["contents"][0]["parts"][0]["text"]
    assert prompt.startswith("Style: upbeat pop, 112 bpm.\nAvoid: mumbled vocals.\nLength: about 0:30.")
    assert "[0:00 - 0:09] Verse\n[0:09 - 0:13] Chorus\n" in prompt
    assert prompt.endswith("Lyrics:\n[Verse]\nLight lands on the thylakoid\nEvery electron hand-off pumps a proton"
                           "\n\n[Chorus]\nProtons flow and ATP grows\n\n[Verse]\nOnly b6f pumps them in"
                           "\n\n[Chorus]\nProtons flow and ATP grows\n\n[Bridge]\nFeel the gradient build"
                           "\n\n[Chorus]\nProtons flow and ATP grows")
    assert not any("/v1/music" in u for u in fake.urls())
    assert sum("text-to-speech" in u for u in fake.urls()) == 2  # the narration and the exam question

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
    assert len(fake.requests) == 3 and json.loads((job / audio.plan_path("song")).read_text())["provider"] == "elevenlabs"

    monkeypatch.setenv("SONG_PROVIDER", "Gemini ")
    assert not audio.is_done(job)
    audio.run(job)
    assert len(fake.requests) == 4 and len(fake.gemini()) == 1  # the speech was kept
    assert "nothing" in audio.run(job) and len(fake.requests) == 4

    monkeypatch.setenv("SONG_PROVIDER", "elevenlabs")
    audio.run(job)
    assert len(fake.requests) == 5 and "/v1/music" in fake.requests[-1][0]


def test_a_song_made_before_there_was_a_choice_counts_as_elevenlabs(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven())
    audio.run(job)
    plan = json.loads((job / audio.plan_path("song")).read_text())
    del plan["provider"], plan["sections_pinned"]
    jobs.write(job / audio.plan_path("song"), plan)
    assert audio.is_done(job)
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    assert not audio.is_done(job)


def test_the_lyria_model_can_be_changed(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_SONG_MODEL", "lyria-3-clip-preview")
    audio.run(job)
    assert fake.gemini()[0][0].endswith("/models/lyria-3-clip-preview:generateContent")
    assert json.loads((job / audio.plan_path("song")).read_text())["model_id"] == "lyria-3-clip-preview"


@pytest.mark.parametrize("fake, reason", [
    (FakeGemini(status=400), "400 INVALID_ARGUMENT Lyria could not make it"),
    (FakeGemini(answer={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}), "blocked the song's prompt: PROHIBITED"),
    (FakeGemini(answer={"candidates": [{"content": {"parts": [{"text": "I can't sing that."}]},
                                        "finishReason": "RECITATION"}]}), "no song .finish reason: RECITATION"),
    (FakeGemini(answer={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/mpeg", "data": "%%"}}]}}]}),
     "not readable audio"),
])
def test_a_gemini_refusal_or_block_fails_the_step_and_keeps_nothing(job, monkeypatch, fake, reason):
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    with pytest.raises(media.StepFailed, match=reason):
        audio.run(job)
    assert not (job / "audio/song.mp3").exists() and not (job / audio.plan_path("song")).exists()
    assert len(fake.requests) == 1  # nothing else was asked for once the song failed


def test_every_key_the_step_needs_is_checked_before_anything_is_spent(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(media.StepFailed, match="^GEMINI_API_KEY is not set"):
        audio.run(job)
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    with pytest.raises(media.StepFailed, match="ELEVENLABS_API_KEY and GEMINI_API_KEY are not set"):
        audio.run(job)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    with pytest.raises(media.StepFailed, match="^ELEVENLABS_API_KEY is not set"):
        audio.run(job)  # the narration still needs it: the song is not bought without it
    assert fake.requests == []


def test_an_unknown_provider_fails_before_any_call(job, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(audio, "send", fake)
    monkeypatch.setenv("SONG_PROVIDER", "suno")
    with pytest.raises(media.StepFailed, match="SONG_PROVIDER must be elevenlabs or gemini, not 'suno'"):
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
def test_lyrias_song_is_kept_as_a_constant_bitrate_mp3_of_its_true_length(job, monkeypatch, sent, mime_type):
    song = _sound(6, *sent)
    if "-write_xing" in sent:
        assert abs(DURATION_OF(song) - 6.0) > 0.15  # as sent, its length reads wrong (3% here)
    monkeypatch.setattr(audio, "as_mp3", AS_MP3)
    monkeypatch.setattr(audio, "send", FakeGemini(song=song, mime_type=mime_type))
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    audio.run(job)
    assert _probe(job / "audio/song.mp3") == ("mp3", "192000")
    assert abs(DURATION(job / "audio/song.mp3") - 6.0) < 0.1  # the MP3's own padding, a few hundredths
    assert json.loads((job / audio.plan_path("song")).read_text())["mime_type"] == mime_type


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_song_ffmpeg_cannot_read_fails_the_step_and_keeps_nothing(job, monkeypatch):
    monkeypatch.setattr(audio, "as_mp3", AS_MP3)
    monkeypatch.setattr(audio, "send", FakeGemini(song=b"not audio at all", mime_type="audio/mpeg"))
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
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
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    quota = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"}]}}

    def busy_once(method, url, headers, body):
        if "generativelanguage" in url and not fake.gemini():
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
    rows = _logged(job)
    assert [(r["label"], r["role"], r["model"]) for r in rows] == [
        ("song", "audio", "music_v2_5"), ("narration", "audio", "eleven_multilingual_v2"),
        ("frq", "audio", "eleven_multilingual_v2")]
    # The fake's music answer names no cost; its speech costs a credit a character. No plan price: no dollars.
    assert rows[0]["credits"] is None and rows[0]["cost_usd"] is None
    narration = json.loads((job / audio.plan_path("narration")).read_text())["text"]
    assert rows[1]["credits"] == len(narration) and rows[1]["cost_usd"] is None


def test_the_plans_price_of_credits_turns_them_into_dollars(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setenv(audio.ELEVENLABS_PRICE_VAR, "0.30")
    audio.run(job)
    narration = _logged(job)[1]
    assert narration["cost_usd"] == pytest.approx(narration["credits"] * 0.30 / 1000)


@pytest.mark.parametrize("value", ["cheap", "-1", "nan", "inf"])
def test_a_price_that_is_not_one_leaves_the_credits_alone(job, monkeypatch, value):
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setenv(audio.ELEVENLABS_PRICE_VAR, value)
    audio.run(job)
    assert _logged(job)[1]["cost_usd"] is None and _logged(job)[1]["credits"]


def test_a_lyria_song_is_logged_at_its_models_price(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    audio.run(job)
    song = _logged(job)[0]
    assert (song["label"], song["model"], song["cost_usd"]) == ("song", "lyria-3.5", 0.08)


def test_a_refused_call_is_not_logged_as_spent(job, monkeypatch):
    monkeypatch.setattr(audio, "send", FakeEleven(status=401))
    with pytest.raises(media.StepFailed):
        audio.run(job)
    assert not (job / "calls.jsonl").exists()

