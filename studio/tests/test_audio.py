import base64
import json

import pytest

from studio import jobs, media
from studio.steps import audio
from studio.tests import sample_media


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


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.setattr(audio, "duration", lambda path: 30.0)
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
    assert [c["duration_ms"] for c in chunks] == [8600, 4300, 4300, 4300, 4300, 4300]
    assert all(c["positive_styles"] == ["upbeat pop", "112 bpm"] and c["negative_styles"] == ["mumbled vocals"]
               for c in chunks)
    plan = json.loads((job / audio.plan_path("song")).read_text())
    assert plan["song_id"] == "song123"
    assert [(s["line_ids"], s["start_ms"]) for s in plan["sections"][:2]] == [(["v1-1", "v1-2"], 0), (["c1-1"], 8600)]
    assert (job / "audio/song.mp3").read_bytes() == b"ID3 song"


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
