"""The brain's providers, offline: request shapes, stop reasons, usage and cost."""

from types import SimpleNamespace as NS

import pytest

from studio import brain
from studio.schemas import FactCheck

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 8
ANSWER = '{"checks": [{"id": "lyric:v1-1", "verdict": "FILLER", "source": "", "problem": "", "corrected": ""}]}'


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
    # The instructions are cached: every call of a kind sends the same ones first.
    assert request["system"] == [{"type": "text", "text": "You check facts.", "cache_control": {"type": "ephemeral"}}]
    [content] = request["messages"]
    assert [block["type"] for block in content["content"]] == ["image", "text"]
    assert content["content"][0]["source"]["media_type"] == "image/png"


def test_the_helper_is_sonnet_at_low_effort_without_a_fallback():
    request = brain.claude_request("helper", "Any text in this image?", None, [], None)
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert "fallbacks" not in request and "betas" not in request and "system" not in request


def test_the_drawer_is_claude_opus_at_high_whatever_the_brain(monkeypatch):
    # It draws a Muse brain's charts and math as SVG (studio/steps/art.py): a painter cannot count.
    asked = []
    monkeypatch.setattr(brain, "_ask_claude", lambda role, *a: asked.append(role) or ["<svg/>"])
    monkeypatch.setattr(brain, "_ask_meta", lambda role, *a: pytest.fail(f"{role} went to Meta"))
    with brain.chosen("muse-wonder", "low"):
        assert brain.ask("drawer", "Draw.", label="art overview") == {"text": "<svg/>"}
        request = brain.claude_request("drawer", "Draw.", None, [], None)
    assert asked == ["drawer"]
    assert request["model"] == "claude-opus-5-5" and request["output_config"] == {"effort": "high"}
    assert request["max_tokens"] == brain.CLAUDE["brain"]["max_tokens"] and "fallbacks" not in request


def test_the_drawer_without_an_anthropic_key_says_so(monkeypatch):
    with brain.chosen("muse-spark"), pytest.raises(brain.BrainError, match=r"Claude, drawing a scene as SVG, needs"):
        brain.ask("drawer", "Draw.")


def test_the_brains_effort_is_the_one_set_on_the_studio_page(monkeypatch):
    with brain.chosen("claude", "medium"):
        request = brain.claude_request("brain", "Write.", None, [], None)
        assert request["model"] == "claude-opus-5-5" and request["output_config"] == {"effort": "medium"}
        assert request["fallbacks"] == "default"
        helper = brain.claude_request("helper", "Look.", None, [], None)
        assert helper["model"] == "claude-sonnet-5-5" and helper["output_config"] == {"effort": "low"}
    assert brain.claude_request("brain", "Write.", None, [], None)["output_config"] == {"effort": "high"}


def test_the_environment_no_longer_picks_the_brain(monkeypatch):
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")
    monkeypatch.setenv("STUDIO_BRAIN_MODEL", "muse-spark-1.3")
    monkeypatch.setenv("STUDIO_BRAIN_EFFORT", "low")
    request = brain.claude_request("brain", "Write.", None, [], None)
    assert (brain.provider(), request["model"], request["output_config"]) == ("claude", "claude-opus-5-5", {"effort": "high"})
    assert brain.retired_problem() == (
        "BRAIN_PROVIDER, STUDIO_BRAIN_MODEL, STUDIO_BRAIN_EFFORT are still set, but the studio no longer reads them: "
        "the brain and the song are set on /admin/studio now. Set them there, then remove them from the environment")
    monkeypatch.setenv("SONG_PROVIDER", "gemini")
    assert brain.retired_problem().startswith("BRAIN_PROVIDER, STUDIO_BRAIN_MODEL, STUDIO_BRAIN_EFFORT, SONG_PROVIDER are")
    for name in brain.RETIRED_VARS:
        monkeypatch.delenv(name)
    assert brain.retired_problem() is None


def test_the_brain_a_lesson_was_written_on_is_known_by_its_model():
    assert [brain.choice_for_model(m) for m in ("claude-opus-5-5", "muse-wonder-1.0", "muse-spark-1.3")] == list(brain.CHOICES)
    assert brain.choice_for_model("claude-opus-5") == "claude"  # a refusal's fallback model
    assert brain.choice_for_model("muse-spark-1.3-20261001") == "muse-spark"  # a dated id
    assert brain.choice_for_model("gpt-9") is None and brain.choice_for_model(None) is None


def test_an_effort_claude_does_not_take_fails_the_call_with_the_allowed_values():
    with brain.chosen("claude", "hihg"):
        with pytest.raises(brain.BrainError, match=r"Claude's effort must be one of low, medium, high, xhigh, max"):
            brain.claude_request("brain", "Write.", None, [], None)
        # The helper has its own, so its calls still go through.
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
    answer = brain.ask("brain", "Check.", FactCheck, system="You check.", label="factcheck round 1")
    assert answer == FactCheck.model_validate_json(ANSWER).model_dump()
    assert sent[0]["system"][0]["text"] == "You check." and sent[0]["model"] == "claude-opus-5-5"
    [call] = brain.calls
    assert call.label == "factcheck round 1" and call.stop_reason == "end_turn"


class TooLargeWhenStrict(FakeStream):
    """Refuses any request whose schema is in output_config, as the API does a schema too large to compile."""

    def __enter__(self):
        if "format" in self.sent[-1]["output_config"]:
            import anthropic

            response = NS(status_code=400, headers={}, request=None)
            raise anthropic.BadRequestError("Error code: 400 - The compiled grammar is too large, which would "
                                            "cause performance issues.", response=response, body=None)
        return self


def test_a_schema_too_large_for_claudes_grammar_is_asked_for_in_the_prompt(monkeypatch):
    sent = []
    fenced = "Here it is.\n```json\n" + ANSWER + "\n```"
    client = NS(beta=NS(messages=NS(stream=TooLargeWhenStrict(message([text(fenced)]), sent))))
    monkeypatch.setitem(brain._clients, "claude", client)
    monkeypatch.setattr(brain, "calls", [])
    monkeypatch.setattr(brain, "_too_large", set())
    assert brain.ask("brain", "Check.", FactCheck) == FactCheck.model_validate_json(ANSWER).model_dump()
    assert "format" in sent[0]["output_config"] and "format" not in sent[1]["output_config"]
    assert '"properties"' in sent[1]["messages"][0]["content"][-1]["text"]  # the schema, in the prompt
    brain.ask("brain", "Again.", FactCheck)
    assert len(sent) == 3 and "format" not in sent[2]["output_config"]  # not tried strictly again this run
    assert len(brain.calls) == 2  # the refused request generated nothing, so it is not a call


def test_a_refused_call_is_still_recorded(monkeypatch):
    refusal = message([], "refusal", usage_=usage(800, 0), stop_details=NS(category=None, explanation=None))
    client = NS(beta=NS(messages=NS(stream=FakeStream(refusal, []))))
    monkeypatch.setitem(brain._clients, "claude", client)
    monkeypatch.setattr(brain, "calls", [])
    with pytest.raises(brain.Refused):
        brain.ask("brain", "Check.", FactCheck, label="bundle")
    assert brain.calls[0].stop_reason == "refusal" and brain.calls[0].input_tokens == 800


def test_meta_request_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "_chosen", "muse-wonder")
    image = tmp_path / "notes.png"
    image.write_bytes(PNG)
    request = brain.meta_request("brain", "Check.", FactCheck, [image], "You check.")
    assert request["model"] == "muse-wonder-1.0" and request["background"] is True  # polled, never held open
    assert request["instructions"] == "You check."
    [turn] = request["input"]
    assert turn["role"] == "user"
    assert turn["content"][0]["type"] == "input_image"
    assert turn["content"][0]["image_url"].startswith("data:image/png;base64,")
    assert turn["content"][1] == {"type": "input_text", "text": "Check."}
    assert request["text"]["format"] == {
        "type": "json_schema", "name": "FactCheck", "schema": brain.strict_schema(FactCheck), "strict": True,
    }
    assert brain.meta_request("helper", "Hi", None, [], None) == {  # a short call, answered in the one request
        "model": "muse-spark-1.3", "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}],
    }


def test_each_muse_brain_has_its_model_and_the_helper_stays_spark():
    for name, model in (("muse-wonder", "muse-wonder-1.0"), ("muse-spark", "muse-spark-1.3")):
        with brain.chosen(name):
            assert brain.meta_request("brain", "Write.", None, [], None)["model"] == model
            assert brain.meta_request("helper", "Look.", None, [], None)["model"] == "muse-spark-1.3"


def meta_response(text="{}", status="completed", model="muse-spark-1.3", input_tokens=10, output_tokens=5,
                  reason=None, refusal=None, id="resp_1"):
    """A Responses API answer, as Meta's comes back."""
    content = [NS(type="refusal", refusal=refusal)] if refusal is not None else [NS(type="output_text", text=text)]
    return NS(id=id, status=status, model=model, usage=NS(input_tokens=input_tokens, output_tokens=output_tokens),
              incomplete_details=NS(reason=reason) if reason else None, error=None,
              output=[NS(type="reasoning", summary=[]), NS(type="message", content=content)])


class FakeResponses:
    """Stands in for Meta's client.responses: each call is queued, then in progress, then `answer`."""

    def __init__(self, answer, looks=2, fail_looks=0):
        self.answer, self.looks, self.fail_looks = answer, looks, fail_looks
        self.created, self.retrieved, self.cancelled = [], 0, []

    def create(self, **request):
        self.created.append(request)
        return NS(id=self.answer.id, status="queued") if request.get("background") else self.answer

    def retrieve(self, id):
        import openai

        self.retrieved += 1
        if self.fail_looks:
            self.fail_looks -= 1
            raise openai.APIConnectionError(request=None)
        return self.answer if self.retrieved > self.looks else NS(id=self.answer.id, status="in_progress")

    def cancel(self, id):
        self.cancelled.append(id)


@pytest.fixture
def meta(monkeypatch):
    """Meta's client, stood in for, with no wait between looks."""
    monkeypatch.setattr(brain, "_sleep", lambda s: None)
    monkeypatch.setenv("MODEL_API_KEY", "k")

    def install(answer, **kw):
        fake = FakeResponses(answer, **kw)
        monkeypatch.setitem(brain._clients, "meta", NS(responses=fake))
        return fake

    return install


def test_a_meta_call_runs_in_the_background_and_is_polled_for_its_answer(meta, monkeypatch):
    fake = meta(meta_response(ANSWER, model="muse-wonder-1.0", input_tokens=1000, output_tokens=2000))
    monkeypatch.setattr(brain, "calls", [])
    with brain.chosen("muse-wonder"):
        answer = brain.ask("brain", "Check.", FactCheck, label="fact-check")
    assert answer["checks"][0]["verdict"] == "FILLER"
    assert fake.created[0]["background"] is True and fake.retrieved == 3
    [call] = brain.calls
    assert (call.label, call.model, call.input_tokens, call.output_tokens) == ("fact-check", "muse-wonder-1.0", 1000, 2000)
    assert call.cost_usd == pytest.approx(0.044) and call.stop_reason == "completed"


def test_a_look_that_fails_is_tried_again_while_the_call_runs_on(meta):
    meta(meta_response("ok"), looks=0, fail_looks=2)
    with brain.chosen("muse-spark"):
        assert brain.ask("brain", "Hi") == {"text": "ok"}
    meta(meta_response("ok"), looks=0, fail_looks=brain.META_POLL_FAILURES)
    with brain.chosen("muse-spark"), pytest.raises(brain.BrainError, match="lost the call resp_1"):
        brain.ask("brain", "Hi")


class StuckFirst(FakeResponses):
    """Meta never starts the first try (it stays queued), and answers the next at once."""

    def create(self, **request):
        self.created.append(request)
        return NS(id=f"resp_{len(self.created)}", status="queued")

    def retrieve(self, id):
        self.retrieved += 1
        return NS(id=id, status="queued") if id == "resp_1" else self.answer


def test_a_writers_call_that_never_starts_is_cancelled_and_asked_again(meta, monkeypatch, capsys):
    clock = iter(range(0, 10**7, 20))  # 20 s a look
    monkeypatch.setattr(brain.time, "monotonic", lambda: next(clock))
    fake = StuckFirst(meta_response("ok"))
    monkeypatch.setitem(brain._clients, "meta", NS(responses=fake))
    with brain.chosen("muse-wonder"):
        assert brain.ask("brain", "Write.", label="bundle") == {"text": "ok"}
    assert fake.cancelled == ["resp_1"] and len(fake.created) == 2
    assert "bundle: resp_1 still queued after" in capsys.readouterr().err

    fake = meta(meta_response("ok"), looks=10**6)  # started, never done
    with brain.chosen("muse-wonder"), pytest.raises(brain.BrainError, match="no answer in 3 tries: resp_1 not done"):
        brain.ask("brain", "Hi")
    assert len(fake.created) == 3 and len(fake.cancelled) == 3


def test_a_helpers_call_is_answered_in_the_one_request(meta):
    fake = meta(meta_response("seen"))
    with brain.chosen("muse-wonder"):
        assert brain.ask("helper", "Any text in it?") == {"text": "seen"}
    assert "background" not in fake.created[0] and fake.retrieved == 0


@pytest.mark.parametrize("answer, error", [
    (meta_response(refusal="I can't help with that."), brain.Refused),
    (meta_response(status="incomplete", reason="content_filter"), brain.Refused),
    (meta_response('{"checks": [', status="incomplete", reason="max_output_tokens"), brain.Truncated),
    (meta_response(status="failed"), brain.BrainError),
])
def test_a_meta_answer_that_is_not_an_answer_is_an_error_and_still_billed(meta, monkeypatch, answer, error):
    meta(answer, looks=0)
    monkeypatch.setattr(brain, "calls", [])
    with brain.chosen("muse-spark"), pytest.raises(error):
        brain.ask("brain", "Check.", FactCheck)
    assert len(brain.calls) == 1 and brain.calls[0].input_tokens == 10


def test_a_meta_answer_naming_its_model_another_way_is_priced_as_asked(meta, monkeypatch):
    meta(meta_response(model="muse-spark-1.3-20261001", input_tokens=1_000_000, output_tokens=0), looks=0)
    monkeypatch.setattr(brain, "_chosen", "muse-spark")
    monkeypatch.setattr(brain, "calls", [])
    brain.ask("brain", "Hi")
    assert brain.calls[-1].model == "muse-spark-1.3-20261001" and brain.calls[-1].cost_usd == pytest.approx(1.25)


@pytest.mark.parametrize("name, effort, keys, problem", [
    (None, None, {"POPSMOSIS_ANTHROPIC_API_KEY": "k"}, None),
    ("muse-wonder", None, {"MODEL_API_KEY": "k"}, None),
    ("muse-spark", None, {"MODEL_API_KEY": "k"}, None),
    ("claude", "max", {"ANTHROPIC_API_KEY": "k"}, None),
    ("muse-spark", None, {"POPSMOSIS_ANTHROPIC_API_KEY": "k"}, "Muse Spark 1.3 needs MODEL_API_KEY"),
    (None, None, {"MODEL_API_KEY": "k"}, "Claude Opus 5.5 needs POPSMOSIS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"),
    ("claude", "hihg", {"POPSMOSIS_ANTHROPIC_API_KEY": "k"}, "Claude's effort must be one of"),
    ("muse-spark-2", None, {"MODEL_API_KEY": "k"}, "the brain 'muse-spark-2' is not one this studio has"),
])
def test_the_brains_settings_are_checked_in_one_place(monkeypatch, name, effort, keys, problem):
    for var in (*brain.CLAUDE_KEY_VARS, "MODEL_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var, value in keys.items():
        monkeypatch.setenv(var, value)
    with brain.chosen(name, effort):
        found = brain.settings_problem()
    assert found is None if problem is None else problem in found


def test_each_provider_needs_its_own_key(monkeypatch):
    for name in (*brain.CLAUDE_KEY_VARS, "MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(brain._clients, "claude", raising=False)
    monkeypatch.delitem(brain._clients, "meta", raising=False)
    with pytest.raises(brain.BrainError, match="needs POPSMOSIS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"):
        brain.ask("brain", "Hi")
    with brain.chosen("muse-wonder"), pytest.raises(brain.BrainError, match="needs MODEL_API_KEY"):
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
    with brain.chosen("llama"), pytest.raises(brain.BrainError, match="'llama' is not one this studio has"):
        brain.ask("brain", "Hi")
    (tmp_path / "notes.bmp").write_bytes(b"BM")
    with pytest.raises(ValueError, match="notes.bmp"):
        brain.claude_request("brain", "Hi", None, [tmp_path / "notes.bmp"], None)


# --- a topic's own brain, picked by an admin on /learn ------------------------------------


def test_a_brain_put_in_force_lasts_until_its_block_ends():
    with brain.chosen("claude", "medium"):
        assert brain.provider() == "claude"
        assert brain.claude_request("brain", "Write.", None, [], None)["output_config"] == {"effort": "medium"}
        with brain.chosen("muse-wonder"):
            assert brain.provider() == "meta"
            assert brain.meta_request("brain", "Write.", None, [], None)["model"] == "muse-wonder-1.0"
        assert (brain.chosen_name(), brain.provider()) == ("claude", "claude")
    assert brain.chosen_name() is None and brain.provider() == "claude"  # the default: Claude Opus 5.5


def test_choose_puts_a_brain_in_force_until_the_block_around_it_ends():
    with brain.chosen(None):
        assert brain.chosen_name() is None
        brain.choose("muse-spark")
        assert (brain.provider(), brain.brain_model()) == ("meta", "muse-spark-1.3")
        brain.choose("")
        assert brain.chosen_name() is None
        brain.choose("muse-wonder", "low")
    assert brain.chosen_name() is None and brain._effort is None


def test_a_brain_the_studio_does_not_have_fails_the_call_before_anything_is_sent(monkeypatch):
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    with brain.chosen("muse-spark-2"), pytest.raises(brain.BrainError, match="not one this studio has"):
        brain.ask("brain", "Hi")


def test_the_brains_are_the_ones_the_api_and_the_page_offer():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    api = (root / "api" / "src" / "lib" / "brains.ts").read_text(encoding="utf-8")
    web = (root / "web" / "src" / "lib" / "brains.ts").read_text(encoding="utf-8")
    assert re.findall(r"'([^']+)'", re.search(r"BRAINS = \[(.*?)\] as const", api).group(1)) == list(brain.CHOICES)
    assert re.findall(r"'([^']+)'", re.search(r"type Brain = (.*);", web).group(1)) == list(brain.CHOICES)
    for picked in brain.CHOICES.values():
        assert picked["model"] in brain.PRICES  # each shows its cost on the bill


def test_the_studio_settings_agree_with_the_api():
    """The studio falls back on its own defaults when the API sends none, and checks a painter against its own list:
    both must be the API's (api/src/lib/studio-settings.ts)."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "api" / "src" / "lib" / "studio-settings.ts").read_text(encoding="utf-8")

    def ts_list(text: str) -> tuple[str, ...]:
        return tuple(re.findall(r"'([^']+)'", text))

    choices = re.search(r"ART_CHOICES[^=]*= \{\s*claude: \[(.*?)\],\s*meta: \[(.*?)\],", source, re.S)
    assert {"claude": ts_list(choices.group(1)), "meta": ts_list(choices.group(2))} == brain.ART_CHOICES
    art = re.search(r"DEFAULT_ART: ArtSettings = \{ claude: '(\w+)', meta: '(\w+)' \}", source)
    assert art.groups() == (brain.ART_CHOICES["claude"][0], brain.ART_CHOICES["meta"][0])  # each default listed first
    default = re.search(r"DEFAULT_BRAIN: BrainSettings = \{ default: '([\w-]+)', effort: '(\w+)' \}", source)
    assert default.groups() == (brain.DEFAULT_BRAIN, brain.DEFAULT_EFFORT)
    assert ts_list(re.search(r"EFFORTS = \[(.*?)\] as const", source).group(1)) == brain.EFFORTS

    from studio.steps import audio

    assert ts_list(re.search(r"SONG_PROVIDERS = \[(.*?)\] as const", source).group(1)) == audio.SONG_PROVIDERS
    song = re.search(r"DEFAULT_SONG: SongSettings = \{ provider: '(\w+)' \}", source).group(1)
    assert song == audio.song_provider(Path("/nonexistent-job"))  # the studio's own default, with no settings


# --- who paints the art, by the brain and the admin's setting -------------------------------


def test_the_painter_goes_by_the_brains_provider_and_the_admins_setting():
    assert brain.painter() == "gemini"  # Claude's brain, nothing set: Gemini 3.1 Flash Image
    assert brain.painter({"claude": "muse"}) == "muse"
    assert brain.painter({"claude": "svg", "meta": "gemini"}) == "svg"
    assert brain.painter({"claude": "imagen"}) == "gemini"  # not a painter: the default
    with brain.chosen("muse-spark"):
        assert brain.painter() == "muse"  # Muse's brain, nothing set: Muse Image
        assert brain.painter({"claude": "svg", "meta": "gemini"}) == "gemini"
        assert brain.painter({"meta": "svg"}) == "muse"  # the SVG art is Claude's only
    assert brain.ART_CHOICES == {"claude": ("gemini", "muse", "svg"), "meta": ("muse", "gemini")}


def test_the_helper_is_the_brains_whoever_paints(monkeypatch):
    """It checks each picture and boxes what SAM missed: Claude Haiku on Claude's brain, Muse Spark on Muse's."""
    sent = []
    monkeypatch.setitem(brain._clients, "claude", NS(beta=NS(messages=NS(stream=FakeStream(
        message([text('{"seen": true}')], model="claude-haiku-5-5"), sent)))))
    brain.ask("helper", "Any text in it?")
    assert sent[-1]["model"] == "claude-sonnet-5-5"
    fake = FakeResponses(meta_response("seen"), looks=0)
    monkeypatch.setitem(brain._clients, "meta", NS(responses=fake))
    monkeypatch.setenv("MODEL_API_KEY", "k")
    with brain.chosen("muse-wonder"):
        brain.ask("helper", "Any text in it?")
    assert fake.created[-1]["model"] == "muse-spark-1.3"


@pytest.mark.parametrize("brain_name, art, keys, problem", [
    (None, None, {"GEMINI_API_KEY", "MODEL_API_KEY"}, None),
    (None, None, {"MODEL_API_KEY"}, "the art needs GEMINI_API_KEY: Gemini 3.1 Flash Image paints it"),
    (None, None, set(), "the art needs GEMINI_API_KEY and MODEL_API_KEY: Gemini 3.1 Flash Image paints it and SAM 3.1"),
    (None, {"claude": "muse"}, {"MODEL_API_KEY"}, None),
    (None, {"claude": "svg"}, set(), None),  # Claude draws it: no other key
    ("muse-spark", None, {"GEMINI_API_KEY"}, "the art needs MODEL_API_KEY: Muse Image paints it"),
    ("muse-spark", {"meta": "gemini"}, {"MODEL_API_KEY"}, "the art needs GEMINI_API_KEY: Gemini 3.1 Flash Image"),
])
def test_the_art_needs_its_painters_key_and_metas_for_sam(monkeypatch, brain_name, art, keys, problem):
    for var in ("GEMINI_API_KEY", "MODEL_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var in keys:
        monkeypatch.setenv(var, "k")
    with brain.chosen(brain_name):
        found = brain.art_problem(art)
    assert found is None if problem is None else (problem in found and found.endswith(
        "(an admin can pick another painter on /admin/studio)"))


def test_sam_says_it_needs_the_meta_key_on_claudes_brain(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(brain.BrainError, match=r"^SAM 3.1 \(and Muse Image, when it paints\) needs MODEL_API_KEY"):
        brain.meta_key()
