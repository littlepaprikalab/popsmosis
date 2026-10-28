"""The brain: one ask() that every studio step calls, whatever the provider.

The brain is one of CHOICES, and an admin sets it, never the environment, which holds only the
keys: the default brain and Claude's effort on /admin/studio, or a topic's own brain on /learn
(api/src/lib/studio-settings.ts, api/src/lib/brains.ts). The studio reads them from the API, and
the runners put the one in force with chosen() and choose(). With none in force, as for a local
`python -m studio plan` without --brain, it is Claude Opus 5.5 at high effort. Each brain's
provider has its helper (docs/PLAN.md, section 3):

    brain          provider   model              helper
    claude         claude     claude-opus-5-5    claude-haiku-5-5, effort low
    muse-wonder    meta       muse-wonder-1.0    muse-spark-1.3
    muse-spark     meta       muse-spark-1.3     muse-spark-1.3

Who paints the art goes by the brain's provider, as an admin sets it on /admin/studio too
(painter()): by default Gemini 3.1 Flash Image for Claude's brain and Muse Image for Muse's,
and SAM 3.1 then finds the structures in either. Claude's brain may instead draw the art as
SVG. The helper, which checks each picture and boxes a structure SAM missed, is the brain's.

Prompts and schemas are shared. A schema is a pydantic model, sent as a JSON schema
cut down to what both providers accept (strict_schema), and every answer is
validated against the model before it is returned.

Every call, including one that fails, is appended to `calls` with its tokens,
cost and time, so a run can report what it spent (studio/ledger.py logs it to the job).
"""

from __future__ import annotations

import base64
import contextlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError

ROLES = ("brain", "helper")
PROVIDERS = ("claude", "meta")

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
META_KEY_VAR = "MODEL_API_KEY"
# Who paints the art, by the brain's provider (painter()). An admin changes it on /admin/studio, and the API keeps
# the same lists (api/src/lib/studio-settings.ts): each provider's default is first.
ART_CHOICES = {"claude": ("gemini", "muse", "svg"), "meta": ("muse", "gemini")}
PAINTER_NAMES = {"gemini": "Gemini 3.1 Flash Image", "muse": "Muse Image", "svg": "the brain, as SVG"}
# The keys each painter's art needs: SAM 3.1 finds the structures in a painted picture, on Meta's key.
PAINTER_KEYS = {"gemini": ("GEMINI_API_KEY", META_KEY_VAR), "muse": (META_KEY_VAR,), "svg": ()}

# The brains an admin may set as the default on /admin/studio or pick for one topic on /learn: each one's
# provider and model. The API holds the same keys (api/src/lib/brains.ts); keep the two lists the same.
CHOICES = {
    "claude": {"provider": "claude", "model": "claude-opus-5-5", "name": "Claude Opus 5.5"},
    "muse-wonder": {"provider": "meta", "model": "muse-wonder-1.0", "name": "Muse Wonder 1.0"},
    "muse-spark": {"provider": "meta", "model": "muse-spark-1.3", "name": "Muse Spark 1.3"},
}

# The effort levels Claude accepts (output_config.effort).
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# With none set: the API's defaults too (api/src/lib/studio-settings.ts).
DEFAULT_BRAIN, DEFAULT_EFFORT = "claude", "high"
# What picked the brain and the singer before /admin/studio did. Still set, they stop `run`, `videos` and `recover`
# until they are removed (retired_problem()), so nothing changes without the admin seeing why.
RETIRED_VARS = ("BRAIN_PROVIDER", "STUDIO_BRAIN_MODEL", "STUDIO_BRAIN_EFFORT", "SONG_PROVIDER")

# Where the studio finds its Anthropic key, first match wins. In a Claude Code cloud
# environment a variable named ANTHROPIC_API_KEY never reached the session (the name is
# Claude Code's own), so the studio has a name of its own; ANTHROPIC_API_KEY still works
# on a laptop.
CLAUDE_KEY_VARS = ("POPSMOSIS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")

# Server-side refusal fallback: a declined request is re-run, inside the same call,
# on the model Anthropic recommends for that refusal category. Haiku 5.5 has no
# fallback, so only the brain asks for one, and not when it runs on Haiku.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens (input, output). Opus 5 and Opus 4.8 are fallback targets. A Muse
# Spark Contributor model is Muse Spark at a discount for letting Meta train on all it is
# sent (dev.meta.ai/docs/pricing-rate-limits).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "muse-wonder-1.0": (4.00, 20.00),
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-spark-1.1": (1.25, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.20),
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
    credits: float | None = None  # what the provider charged in its own credits (ElevenLabs), when it says


calls: list[Call] = []
_local = threading.local()  # the calls each thread is collecting (collect())
# The brain in force while a runner works on a topic (a key of CHOICES) and Claude's effort, as the API said; None:
# the defaults. One per process, not per thread: the art and audio branches of `videos` run in threads of their own,
# and both need it.
_chosen: str | None = None
_effort: str | None = None


@contextlib.contextmanager
def chosen(name: str | None, effort: str | None = None):
    """Run the block on a brain (a key of CHOICES) at an effort (Claude's); None for either is its default.
    choose() inside the block lasts until the block ends."""
    global _chosen, _effort
    outer = _chosen, _effort
    _chosen, _effort = name or None, effort or None
    try:
        yield
    finally:
        _chosen, _effort = outer


def choose(name: str | None, effort: str | None = None) -> None:
    """Put a brain in force from here on, as `run` does once the claim says which it is. A name CHOICES does not
    have is taken too, and fails every call, with settings_problem() saying why."""
    global _chosen, _effort
    _chosen, _effort = name or None, effort or None


def chosen_name() -> str | None:
    """The brain put in force, or None when none is (the default)."""
    return _chosen


def current() -> dict:
    """The brain in force (CHOICES): the one chosen, else Claude Opus 5.5."""
    name = _chosen or DEFAULT_BRAIN
    if name not in CHOICES:
        raise BrainError(f"the brain {name!r} is not one this studio has ({', '.join(CHOICES)}): update the studio")
    return CHOICES[name]


def _who() -> str:
    """The brain in force, by name, for an error message."""
    return current()["name"]


def retired_problem() -> str | None:
    """Why a variable that used to pick the brain, still set in the environment, must go first; or None. The
    studio no longer reads them, so a run that went on would quietly change from the brain they named to the one
    on /admin/studio."""
    left = [name for name in RETIRED_VARS if os.environ.get(name, "").strip()]
    if not left:
        return None
    them = "it" if len(left) == 1 else "them"
    return (f"{', '.join(left)} {'is' if len(left) == 1 else 'are'} still set, but the studio no longer reads {them}: "
            f"the brain and the song are set on /admin/studio now. Set them there, then remove {them} from the "
            "environment")


def choice_for_model(model: str | None) -> str | None:
    """The brain (a key of CHOICES) a model id belongs to: the one that wrote a lesson, as the answer records it.
    A fallback model or a dated id goes by its family; None for a model none of them is."""
    if not model:
        return None
    for name, picked in CHOICES.items():
        if model == picked["model"]:
            return name
    families = {"claude-": "claude", "muse-wonder": "muse-wonder", "muse-spark": "muse-spark"}
    return next((name for prefix, name in families.items() if model.startswith(prefix)), None)


def _record(call: Call) -> None:
    calls.append(call)
    sink = getattr(_local, "sink", None)
    if sink is not None:
        sink.append(call)


@contextlib.contextmanager
def collect():
    """The calls this thread makes inside the block, apart from any other thread's: the art and
    audio branches of `python -m studio videos` ask at the same time."""
    outer = getattr(_local, "sink", None)
    _local.sink = mine = []
    try:
        yield mine
    finally:
        _local.sink = outer
        if outer is not None:
            outer.extend(mine)


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
    if provider() == "claude":
        texts = _ask_claude(role, prompt, schema, images, system, label or role)
    else:
        texts = _ask_meta(role, prompt, schema, images, system, label or role)
    if schema is None:
        return {"text": texts[0]}
    return parse(schema, texts)


def provider() -> str:
    """The provider of the brain in force: claude or meta. Who paints the art goes by it (painter())."""
    return current()["provider"]


def painter(art: dict | None = None) -> str:
    """Who paints the art for the brain in force: gemini, muse or svg. art is the admin's setting as the API's claim
    said ({"claude": ..., "meta": ...}); a provider it leaves out, or a painter that provider may not use, gets the
    default: Gemini 3.1 Flash Image for Claude's brain, Muse Image for Muse's."""
    allowed = ART_CHOICES[provider()]
    picked = (art or {}).get(provider()) if isinstance(art, dict) else None
    return picked if picked in allowed else allowed[0]


def meta_key() -> str:
    """The Meta API key every Meta model here takes: the brain's, Muse Image's and SAM's."""
    key = os.environ.get(META_KEY_VAR)
    if not key:
        who = _who() if provider() == "meta" else "SAM 3.1 (and Muse Image, when it paints)"
        raise BrainError(f"{who} needs {META_KEY_VAR}")
    return key


def art_problem(art: dict | None = None) -> str | None:
    """What the art's painter (painter(art)) needs and this environment does not have, or None. The videos ask
    before they claim anything, as for settings_problem()."""
    try:
        name = painter(art)
    except (ValueError, BrainError) as e:
        return str(e)
    missing = [var for var in PAINTER_KEYS[name] if not os.environ.get(var)]
    if not missing:
        return None
    return (f"the art needs {' and '.join(missing)}: {PAINTER_NAMES[name]} paints it and SAM 3.1 finds its "
            "structures (an admin can pick another painter on /admin/studio)")


def settings_problem() -> str | None:
    """What keeps the brain in force from running here (a name this studio does not have, an effort Claude does
    not take, its provider's key missing), or None. The runners ask about the default brain before they claim
    anything, so a setting left wrong stops the run instead of failing, and giving up, one topic after another."""
    try:
        if provider() == "meta":
            meta_key()
        else:
            claude_brain()
            if not claude_key():
                raise BrainError(f"{_who()} needs {' or '.join(CLAUDE_KEY_VARS)}")
    except BrainError as e:
        return str(e)
    return None


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


def brain_model() -> str:
    """The model of the brain in force."""
    return current()["model"]


def claude_brain() -> tuple[str, str]:
    """Claude's brain's model and effort: the brain in force, at the effort set on /admin/studio (high by default)."""
    effort = _effort or DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise BrainError(f"Claude's effort must be one of {', '.join(EFFORTS)}, not {effort!r} (/admin/studio)")
    return brain_model(), effort


def claude_request(
    role: str, prompt: str, schema: type[BaseModel] | None, images: Sequence[str | Path], system: str | None
) -> dict:
    """The keyword arguments for client.beta.messages.stream()."""
    if role == "brain":
        model, effort = claude_brain()
    else:
        model, effort = CLAUDE[role]["model"], CLAUDE[role]["effort"]
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        for media_type, data in map(_image, images)
    ]
    content.append({"type": "text", "text": prompt})
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": strict_schema(schema)}
    request = {
        "model": model,
        "max_tokens": CLAUDE[role]["max_tokens"],
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }
    if system:
        request["system"] = system
    if role == "brain" and not model.startswith("claude-haiku"):
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
            raise BrainError(f"{_who()} needs {' or '.join(CLAUDE_KEY_VARS)}")
        _clients["claude"] = anthropic.Anthropic(api_key=key)
    started = time.monotonic()
    try:
        # Streamed so a long answer never trips the SDK's request timeout.
        with _clients["claude"].beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise BrainError(f"{request['model']}: {e}") from e
    _record(claude_call(message, request["model"], role, label, time.monotonic() - started))
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
# First run live on Oct 28 (muse-spark-1.3). Muse takes OpenAI-style chat completions. No
# effort is sent: the effort on /admin/studio is Claude's, and Muse keeps its own default.


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
    model = brain_model() if role == "brain" else META[role]["model"]
    request = {"model": model, "messages": messages}
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
        _clients["meta"] = openai.OpenAI(api_key=meta_key(), base_url=META_BASE_URL)
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
    # Priced as answered, or as asked if Meta names the model some other way (a dated id).
    price = cost(model, input_tokens, output_tokens)
    if price is None:
        price = cost(request["model"], input_tokens, output_tokens)
    _record(Call(label, role, model, input_tokens, output_tokens, price, round(seconds, 1), choice.finish_reason or ""))
    if choice.message.refusal or choice.finish_reason == "content_filter":
        raise Refused(model, explanation=choice.message.refusal)
    if choice.finish_reason == "length":
        raise Truncated(f"{model} hit its output limit ({output_tokens} output tokens)")
    return [choice.message.content or ""]
