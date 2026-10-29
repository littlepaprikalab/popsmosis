"""The knobs an admin sets on /admin/studio over this machine's environment (studio/knobs.py)."""

from studio import jobs, knobs


def test_a_knob_set_on_the_page_wins_over_the_environment_and_is_the_environments_again_once_cleared(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voiceEnv")
    assert knobs.env("ELEVENLABS_VOICE_ID") == "voiceEnv"
    knobs.use({"brain": {"default": "claude"}, "knobs": {"ELEVENLABS_VOICE_ID": "voicePage", "ELEVENLABS_USD_PER_1K_CREDITS": 0.3}})
    assert knobs.env("ELEVENLABS_VOICE_ID") == "voicePage"
    assert knobs.env("ELEVENLABS_USD_PER_1K_CREDITS") == "0.3"
    assert knobs.named("ELEVENLABS_VOICE_ID") == "ELEVENLABS_VOICE_ID, set on /admin/studio"
    knobs.use({"knobs": {}})  # cleared on the page
    assert knobs.env("ELEVENLABS_VOICE_ID") == "voiceEnv"
    assert knobs.named("ELEVENLABS_VOICE_ID") == "ELEVENLABS_VOICE_ID"
    monkeypatch.delenv("ELEVENLABS_VOICE_ID")
    assert knobs.env("ELEVENLABS_VOICE_ID") == ""


def test_settings_without_knobs_leave_those_in_force_and_only_plain_values_are_taken(monkeypatch):
    for name in ("STUDIO_VIDEOS", "GEMINI_SONG_MODEL", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(name, raising=False)
    knobs.use({"knobs": {"STUDIO_VIDEOS": "off"}})
    for studio in (None, {}, {"art": {"claude": "svg"}}, {"knobs": "off"}, []):
        knobs.use(studio)  # an older API, or a local job
        assert knobs.env("STUDIO_VIDEOS") == "off"
    knobs.use({"knobs": {"STUDIO_VIDEOS": True, "GEMINI_SONG_MODEL": ["lyria-3.5"], "ELEVENLABS_VOICE_ID": None}})
    assert knobs.env("STUDIO_VIDEOS") == knobs.env("GEMINI_SONG_MODEL") == knobs.env("ELEVENLABS_VOICE_ID") == ""


def test_the_knobs_a_claim_carries_are_kept_with_the_job_and_put_in_force(tmp_path):
    job = tmp_path / "q1"
    job.mkdir()
    studio = {"art": {"claude": "gemini", "meta": "muse"}, "knobs": {"GEMINI_SONG_MODEL": "lyria-3-clip-preview"}}
    jobs.keep_studio_settings(job, studio)
    assert jobs.studio_settings(job) == studio
    assert knobs.env("GEMINI_SONG_MODEL") == "lyria-3-clip-preview"
