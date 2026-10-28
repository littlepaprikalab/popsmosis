"""The brain's providers, offline: request shapes, stop reasons, usage and cost."""

from types import SimpleNamespace as NS

import pytest

from studio import brain
from studio.schemas import FactCheck

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 8
ANSWER = '{"checks": [{"id": "lyric:v1-1", "verdict": "FILLER", "source": "", "problem": "", "corrected": ""}]}'


@pytest.fixture(autouse=True)
def default_brain(monkeypatch):
    """The defaults, whatever the session's environment sets (a cloud environment may run the brain on Haiku)."""
    for name in ("BRAIN_PROVIDER", "STUDIO_BRAIN_MODEL", "STUDIO_BRAIN_EFFORT"):
        monkeypatch.delenv(name, raising=False)


def text(t):
    return NS(type="text", text=t)


def usage(input_tokens, output_tokens, iterations=None):
    return NS(input_tokens=input_tokens, output_tokens=output_tokens, cache_creation_input_tokens=None,
              cache_read_input_tokens=None, iterations=iterations)


def message(content, stop_reason="end_turn", model="claude-opus-5-5", usage_=None, stop_details=None):
    return NS(content=content, stop_reason=stop_reason, model=model, stop_details=stop_details,
              usage=usage_ or usage(1000, 500))


def test_the_brain_is_opus_at_high_effort_with_the_refusal_fallback(tmp_path):
    image = tmp_path / "notes.png"
    image.write_bytes(PNG)
    request = brain.claude_request("brain", "Check these.", FactCheck, [image], "You check facts.")
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": brain.strict_schema(FactCheck)},
    }
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["system"] == "You check facts."
    [content] = request["messages"]
    assert [block["type"] for block in content["content"]] == ["image", "text"]
    assert content["content"][0]["source"]["media_type"] == "image/png"


def test_the_helper_is_haiku_at_low_effort_without_a_fallback():
    request = brain.claude_request("helper", "Any text in this image?", None, [], None)
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert "fallbacks" not in request and "betas" not in request and "system" not in request


def test_the_brains_model_and_effort_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "claude-haiku-5-5")
    request = brain.claude_request("brain", "Write.", None, [], None)
    assert request["model"] == "claude-haiku-5-5" and request["output_config"] == {"effort": "high"}
    assert "fallbacks" not in request  # Haiku 5.5 has no server-side fallback
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "")
    monkeypatch.setenv("STUDIO_BRAIN_EFFORT", " medium\n")  # pasted with stray whitespace
    request = brain.claude_request("brain", "Write.", None, [], None)
    assert request["model"] == "claude-opus-5-5" and request["output_config"] == {"effort": "medium"}
    assert request["fallbacks"] == "default"
    helper = brain.claude_request("helper", "Look.", None, [], None)
    assert helper["model"] == "claude-haiku-5-5" and helper["output_config"] == {"effort": "low"}


def test_a_misspelled_effort_fails_the_call_with_the_allowed_values(monkeypatch):
    monkeypatch.setenv("STUDIO_BRAIN_EFFORT", "hihg")
    with pytest.raises(brain.BrainError, match=r"STUDIO_BRAIN_EFFORT must be one of low, medium, high, xhigh, max"):
        brain.claude_request("brain", "Write.", None, [], None)
    # The helper does not read it, so its calls still go through.
    assert brain.claude_request("helper", "Look.", None, [], None)["output_config"] == {"effort": "low"}


def test_stop_reasons_are_checked():
    refusal = message([], "refusal", stop_details=NS(category="bio", explanation="declined"))
    with pytest.raises(brain.Refused, match=r"claude-opus-5-5 declined the request \(bio\): declined"):
        brain.claude_texts(refusal)
    with pytest.raises(brain.Truncated):
        brain.claude_texts(message([text("{")], "max_tokens"))
    with pytest.raises(brain.BrainError, match="pause_turn"):
        brain.claude_texts(message([], "pause_turn"))
    assert brain.claude_texts(message([NS(type="thinking", thinking=""), text("{}")])) == ["{}"]


def test_after_a_fallback_the_answer_is_the_text_after_the_switch():
    switched = message([text('{"chec'), NS(type="fallback"), text(ANSWER)])
    assert brain.claude_texts(switched) == [ANSWER, '{"chec' + ANSWER]
    assert brain.parse(FactCheck, brain.claude_texts(switched))["checks"][0]["verdict"] == "FILLER"
    continued = message([text(ANSWER[:20]), NS(type="fallback"), text(ANSWER[20:])])
    assert brain.parse(FactCheck, brain.claude_texts(continued))["checks"][0]["id"] == "lyric:v1-1"


def test_parse_reports_an_answer_that_does_not_fit():
    with pytest.raises(brain.InvalidOutput, match="does not fit FactCheck") as caught:
        brain.parse(FactCheck, ['{"checks": [{"id": 1}]}'])
    assert caught.value.raw == '{"checks": [{"id": 1}]}'


def test_usage_and_cost():
    call = brain.claude_call(message([], usage_=usage(10_000, 2_000)), "claude-opus-5-5", "brain", "bundle", 12.34)
    assert (call.input_tokens, call.output_tokens, call.model, call.fell_back) == (10_000, 2_000, "claude-opus-5-5", False)
    assert call.cost_usd == pytest.approx(10_000 * 4e-6 + 2_000 * 20e-6) and call.seconds == 12.3

    hops = [
        NS(type="message", model=None, input_tokens=1_000, output_tokens=0,
           cache_creation_input_tokens=0, cache_read_input_tokens=0),
        NS(type="fallback_message", model="claude-opus-5", input_tokens=1_000, output_tokens=3_000,
           cache_creation_input_tokens=0, cache_read_input_tokens=0),
    ]
    fell_back = message([], model="claude-opus-5", usage_=usage(1_000, 3_000, hops))
    call = brain.claude_call(fell_back, "claude-opus-5-5", "brain", "bundle", 1.0)
    assert (call.input_tokens, call.output_tokens, call.model, call.fell_back) == (2_000, 3_000, "claude-opus-5", True)
    assert call.cost_usd == pytest.approx(1_000 * 4e-6 + 1_000 * 5e-6 + 3_000 * 25e-6)
    assert brain.cost("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(5.50)
    assert brain.cost("muse-spark-1.3-contributor", 1_000_000, 1_000_000) == pytest.approx(0.30)
    assert brain.cost("muse-glimmer", 1, 1) is None  # not on Meta's API: no price


class FakeStream:
    def __init__(self, final, sent):
        self.final, self.sent = final, sent

    def __call__(self, **request):
        self.sent.append(request)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.final


def test_ask_streams_records_the_call_and_validates(monkeypatch):
    sent = []
    client = NS(beta=NS(messages=NS(stream=FakeStream(message([text(ANSWER)]), sent))))
    monkeypatch.setitem(brain._clients, "claude", client)
    monkeypatch.setattr(brain, "calls", [])
    monkeypatch.delenv("BRAIN_PROVIDER", raising=False)
    answer = brain.ask("brain", "Check.", FactCheck, system="You check.", label="factcheck round 1")
    assert answer == FactCheck.model_validate_json(ANSWER).model_dump()
    assert sent[0]["system"] == "You check." and sent[0]["model"] == "claude-opus-5-5"
    [call] = brain.calls
    assert call.label == "factcheck round 1" and call.stop_reason == "end_turn"


def test_a_refused_call_is_still_recorded(monkeypatch):
    refusal = message([], "refusal", usage_=usage(800, 0), stop_details=NS(category=None, explanation=None))
    client = NS(beta=NS(messages=NS(stream=FakeStream(refusal, []))))
    monkeypatch.setitem(brain._clients, "claude", client)
    monkeypatch.setattr(brain, "calls", [])
    with pytest.raises(brain.Refused):
        brain.ask("brain", "Check.", FactCheck, label="bundle")
    assert brain.calls[0].stop_reason == "refusal" and brain.calls[0].input_tokens == 800


def test_meta_request_shape(tmp_path):
    image = tmp_path / "notes.png"
    image.write_bytes(PNG)
    request = brain.meta_request("brain", "Check.", FactCheck, [image], "You check.")
    assert request["model"] == "muse-wonder-1.0"
    assert request["messages"][0] == {"role": "system", "content": "You check."}
    content = request["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "Check."}
    assert request["response_format"]["json_schema"] == {
        "name": "FactCheck", "schema": brain.strict_schema(FactCheck), "strict": True,
    }
    assert brain.meta_request("helper", "Hi", None, [], None) == {
        "model": "muse-spark-1.3", "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
    }


def test_the_meta_brains_model_comes_from_the_environment_and_the_helper_stays(monkeypatch):
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", " muse-spark-1.3\n")  # pasted with stray whitespace
    assert brain.meta_request("brain", "Write.", None, [], None)["model"] == "muse-spark-1.3"
    assert brain.meta_request("helper", "Look.", None, [], None)["model"] == "muse-spark-1.3"
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "muse-spark-1.3-contributor")
    assert brain.meta_request("brain", "Write.", None, [], None)["model"] == "muse-spark-1.3-contributor"
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "")
    assert brain.meta_request("brain", "Write.", None, [], None)["model"] == "muse-wonder-1.0"


def test_a_brain_model_left_over_from_the_other_provider_fails_before_anything_is_sent(monkeypatch):
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "claude-haiku-5-5")
    with pytest.raises(brain.BrainError, match="claude-haiku-5-5 is not a Muse model, and BRAIN_PROVIDER is meta"):
        brain.meta_request("brain", "Write.", None, [], None)
    assert brain.meta_request("helper", "Look.", None, [], None)["model"] == "muse-spark-1.3"
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "muse-spark-1.3")
    with pytest.raises(brain.BrainError, match="muse-spark-1.3 is not a Claude model, and BRAIN_PROVIDER is claude"):
        brain.claude_request("brain", "Write.", None, [], None)
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "claude-opus-5-5")
    with pytest.raises(brain.BrainError, match="not a Muse model"):
        brain.ask("brain", "Hi")  # before any client or request is made


def test_a_meta_answer_naming_its_model_another_way_is_priced_as_asked(monkeypatch):
    answer = NS(model="muse-spark-1.3-20261001", usage=NS(prompt_tokens=1_000_000, completion_tokens=0),
                choices=[NS(finish_reason="stop", message=NS(content="{}", refusal=None))])
    monkeypatch.setitem(brain._clients, "meta", NS(chat=NS(completions=NS(create=lambda **request: answer))))
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "muse-spark-1.3")
    monkeypatch.setattr(brain, "calls", [])
    brain.ask("brain", "Hi")
    assert brain.calls[-1].model == "muse-spark-1.3-20261001" and brain.calls[-1].cost_usd == pytest.approx(1.25)


def test_each_provider_needs_its_own_key(monkeypatch):
    for name in (*brain.CLAUDE_KEY_VARS, "MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(brain._clients, "claude", raising=False)
    monkeypatch.delitem(brain._clients, "meta", raising=False)
    monkeypatch.setenv("BRAIN_PROVIDER", "claude")
    with pytest.raises(brain.BrainError, match="needs POPSMOSIS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"):
        brain.ask("brain", "Hi")
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")
    with pytest.raises(brain.BrainError, match="needs MODEL_API_KEY"):
        brain.ask("brain", "Hi")


def test_the_studio_key_wins_over_anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "laptop")
    monkeypatch.delenv("POPSMOSIS_ANTHROPIC_API_KEY", raising=False)
    assert brain.claude_key() == "laptop"
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "studio")
    assert brain.claude_key() == "studio"
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "")
    assert brain.claude_key() == "laptop"


def test_bad_role_provider_or_image_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="role"):
        brain.ask("writer", "Hi")
    monkeypatch.setenv("BRAIN_PROVIDER", "llama")
    with pytest.raises(ValueError, match="BRAIN_PROVIDER"):
        brain.ask("brain", "Hi")
    (tmp_path / "notes.bmp").write_bytes(b"BM")
    with pytest.raises(ValueError, match="notes.bmp"):
        brain.claude_request("brain", "Hi", None, [tmp_path / "notes.bmp"], None)
