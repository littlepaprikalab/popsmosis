"""The brain: one ask() that every studio step calls, whatever the provider.

BRAIN_PROVIDER picks the models behind the two thinking roles (docs/PLAN.md, section 3):

    role     claude (the default)              meta
    brain    claude-opus-5-5, effort high      muse-wonder-1.0
    helper   claude-haiku-5-5, effort low      muse-spark-1.3

Prompts and schemas are shared. A schema is a pydantic model, sent as a JSON schema
cut down to what both providers accept (strict_schema), and every answer is
validated against the model before it is returned.

Every call, including one that fails, is appended to `calls` with its tokens,
cost and time, so a run can report what it spent.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError

ROLES = ("brain", "helper")

CLAUDE = {
    # Opus 5.5 defaults to effort medium and its thinking cannot be turned off;
    # thinking is billed as output, so max_tokens leaves room for it.
    "brain": {"model": "claude-opus-5-5", "effort": "high", "max_tokens": 64000},
    "helper": {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 16000},
}
META = {
    "brain": {"model": "muse-wonder-1.0"},
    "helper": {"model": "muse-spark-1.3"},
}
META_BASE_URL = "https://api.meta.ai/v1"

# Where the studio finds its Anthropic key, first match wins. In a Claude Code cloud
# environment a variable named ANTHROPIC_API_KEY never reached the session (the name is
# Claude Code's own), so the studio has a name of its own; ANTHROPIC_API_KEY still works
# on a laptop.
CLAUDE_KEY_VARS = ("POPSMOSIS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")

# Server-side refusal fallback: a declined request is re-run, inside the same call,
# on the model Anthropic recommends for that refusal category. Haiku 5.5 has no
# fallback, so only the brain asks for one.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (input, output). Opus 5 and Opus 4.8 are fallback targets.
# Muse Spark's price is not known yet, so its calls report no cost.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "muse-wonder-1.0": (4.00, 20.00),
}

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# The JSON-schema keywords both providers accept (docs/PLAN.md, section 4).
SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "description"}


class BrainError(RuntimeError):
    """A call that came back without a usable answer."""


class Refused(BrainError):
    """The model declined, and so did any fallback."""

    def __init__(self, model: str, category: str | None = None, explanation: str | None = None):
        self.model = model
        self.category = category
        self.explanation = explanation
        detail = f" ({category})" if category else ""
        super().__init__(f"{model} declined the request{detail}" + (f": {explanation}" if explanation else ""))


class Truncated(BrainError):
    """The answer hit max_tokens before it was complete."""


class InvalidOutput(BrainError):
    """The answer did not validate against its schema."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass
class Call:
    label: str
    role: str
    model: str  # the model that answered: after a fallback, not the one asked
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    seconds: float
    stop_reason: str
    fell_back: bool = False


calls: list[Call] = []


def ask(
    role: str,
    prompt: str,
    schema: type[BaseModel] | None = None,
    images: Sequence[str | Path] = (),
    *,
    system: str | None = None,
    label: str | None = None,
) -> dict:
    """Ask one question; return the answer as a dict.

    role is "brain" or "helper". With a schema the answer is that model's fields;
    without one it is {"text": ...}. images are PNG, JPEG, GIF or WebP files shown
    before the prompt. label names the call in `calls`.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, not {role!r}")
    provider = os.environ.get("BRAIN_PROVIDER", "claude")
    if provider == "claude":
        texts = _ask_claude(role, prompt, schema, images, system, label or role)
    elif provider == "meta":
        texts = _ask_meta(role, prompt, schema, images, system, label or role)
    else:
        raise ValueError(f"BRAIN_PROVIDER must be claude or meta, not {provider!r}")
    if schema is None:
        return {"text": texts[0]}
    return parse(schema, texts)


def parse(schema: type[BaseModel], texts: Sequence[str]) -> dict:
    """Validate the first text that fits the schema; raise InvalidOutput if none does."""
    error = None
    for text in texts:
        try:
            return schema.model_validate_json(text).model_dump()
        except ValidationError as e:
            error = error or e
    raise InvalidOutput(f"the answer does not fit {schema.__name__}: {error}", texts[0] if texts else "")


def strict_schema(model: type[BaseModel]) -> dict:
    """The model's JSON schema, cut down to what both providers accept.

    References are inlined, titles dropped, and every object is closed
    (additionalProperties: false) with every property required. Any other keyword
    (a length, a range, a pattern, a default, a union) is a mistake in the model:
    such checks belong in validators, which pydantic runs but does not export.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: dict, seen: tuple[str, ...]) -> dict:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name in seen:
                raise ValueError(f"{model.__name__}: {name} is recursive")
            seen = (*seen, name)
            node = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
        out = {}
        for key, value in node.items():
            if key == "title":
                continue
            if key not in SCHEMA_KEYWORDS:
                raise ValueError(f"{model.__name__}: schema keyword {key!r} is not allowed")
            if key == "properties":
                value = {name: walk(prop, seen) for name, prop in value.items()}
            elif key == "items":
                value = walk(value, seen)
            out[key] = value
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return walk(raw, ())


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _image(path: str | Path) -> tuple[str, str]:
    path = Path(path)
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(f"{path.name}: images must be one of {', '.join(MEDIA_TYPES)}")
    return media_type, base64.standard_b64encode(path.read_bytes()).decode("ascii")


_clients: dict[str, object] = {}


# --- Claude -------------------------------------------------------------------


def claude_key() -> str | None:
    return next((os.environ[name] for name in CLAUDE_KEY_VARS if os.environ.get(name)), None)


def claude_request(
    role: str, prompt: str, schema: type[BaseModel] | None, images: Sequence[str | Path], system: str | None
) -> dict:
    """The keyword arguments for client.beta.messages.stream()."""
    config = CLAUDE[role]
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        for media_type, data in map(_image, images)
    ]
    content.append({"type": "text", "text": prompt})
    output_config: dict = {"effort": config["effort"]}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": strict_schema(schema)}
    request = {
        "model": config["model"],
        "max_tokens": config["max_tokens"],
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }
    if system:
        request["system"] = system
    if role == "brain":
        request["fallbacks"] = "default"
        request["betas"] = [FALLBACK_BETA]
    return request


def _ask_claude(role, prompt, schema, images, system, label) -> list[str]:
    import anthropic

    request = claude_request(role, prompt, schema, images, system)
    if "claude" not in _clients:
        # The studio's calls bill to the API account behind this key, never to
        # whatever other credential the machine happens to hold.
        key = claude_key()
        if not key:
            raise BrainError(f"BRAIN_PROVIDER=claude needs {' or '.join(CLAUDE_KEY_VARS)}")
        _clients["claude"] = anthropic.Anthropic(api_key=key)
    started = time.monotonic()
    try:
        # Streamed so a long answer never trips the SDK's request timeout.
        with _clients["claude"].beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise BrainError(f"{request['model']}: {e}") from e
    calls.append(claude_call(message, request["model"], role, label, time.monotonic() - started))
    return claude_texts(message)


def claude_call(message, asked_model: str, role: str, label: str, seconds: float) -> Call:
    """Tokens and cost for one message, summed over every model that ran it.

    After a fallback, usage.iterations has one entry per attempt (each billed at its
    own model's price) and the top-level usage covers only the last one.
    """
    usage = message.usage
    hops = usage.iterations or [usage]
    input_tokens = output_tokens = 0
    total: float | None = 0.0
    for hop in hops:
        # A declined attempt may not name its model: that is the one asked.
        model = getattr(hop, "model", None) or (message.model if hop is usage else asked_model)
        cache_write = hop.cache_creation_input_tokens or 0
        cache_read = hop.cache_read_input_tokens or 0
        input_tokens += hop.input_tokens + cache_write + cache_read
        output_tokens += hop.output_tokens
        if total is None or model not in PRICES:
            total = None
            continue
        price_in, price_out = PRICES[model]
        # Cache writes bill at 1.25x input and reads at about 0.1x; the studio sets
        # no cache breakpoints, so both are normally zero.
        billed_in = hop.input_tokens + 1.25 * cache_write + 0.1 * cache_read
        total += (billed_in * price_in + hop.output_tokens * price_out) / 1_000_000
    fell_back = any(getattr(hop, "type", None) == "fallback_message" for hop in usage.iterations or [])
    return Call(label, role, message.model, input_tokens, output_tokens, total, round(seconds, 1),
                message.stop_reason or "", fell_back)


def claude_texts(message) -> list[str]:
    """The answer's text, after checking why the model stopped.

    After a mid-answer fallback the content holds the declined model's partial
    text, a `fallback` block, then the fallback model's text. The answer is
    normally the text after the last fallback block; if the fallback continued
    the partial instead, the whole text is the answer, so both are offered.
    """
    if message.stop_reason == "refusal":
        details = message.stop_details
        raise Refused(message.model, getattr(details, "category", None), getattr(details, "explanation", None))
    if message.stop_reason == "max_tokens":
        raise Truncated(f"{message.model} hit max_tokens ({message.usage.output_tokens} output tokens)")
    if message.stop_reason != "end_turn":
        raise BrainError(f"{message.model} stopped with {message.stop_reason!r}")
    blocks = message.content
    last_fallback = max((i for i, block in enumerate(blocks) if block.type == "fallback"), default=-1)
    after = "".join(block.text for block in blocks[last_fallback + 1 :] if block.type == "text")
    whole = "".join(block.text for block in blocks if block.type == "text")
    return [after] if after == whole else [after, whole]


# --- Meta ---------------------------------------------------------------------
# Untested until MODEL_API_KEY exists. Muse takes OpenAI-style chat completions;
# whether it accepts an effort setting is not known yet, so none is sent.


def meta_request(
    role: str, prompt: str, schema: type[BaseModel] | None, images: Sequence[str | Path], system: str | None
) -> dict:
    """The keyword arguments for client.chat.completions.create()."""
    content: list[dict] = [
        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
        for media_type, data in map(_image, images)
    ]
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": content})
    request = {"model": META[role]["model"], "messages": messages}
    if schema is not None:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": strict_schema(schema), "strict": True},
        }
    return request


def _ask_meta(role, prompt, schema, images, system, label) -> list[str]:
    import openai

    request = meta_request(role, prompt, schema, images, system)
    if "meta" not in _clients:
        key = os.environ.get("MODEL_API_KEY")
        if not key:
            raise BrainError("BRAIN_PROVIDER=meta needs MODEL_API_KEY")
        _clients["meta"] = openai.OpenAI(api_key=key, base_url=META_BASE_URL)
    started = time.monotonic()
    try:
        response = _clients["meta"].chat.completions.create(**request)
    except openai.APIError as e:
        raise BrainError(f"{request['model']}: {e}") from e
    seconds = time.monotonic() - started
    choice = response.choices[0]
    model = response.model or request["model"]
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    calls.append(Call(label, role, model, input_tokens, output_tokens, cost(model, input_tokens, output_tokens),
                      round(seconds, 1), choice.finish_reason or ""))
    if choice.message.refusal or choice.finish_reason == "content_filter":
        raise Refused(model, explanation=choice.message.refusal)
    if choice.finish_reason == "length":
        raise Truncated(f"{model} hit its output limit ({output_tokens} output tokens)")
    return [choice.message.content or ""]
