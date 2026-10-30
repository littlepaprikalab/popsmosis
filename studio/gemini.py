"""Gemini 3.1 Flash Image on the Gemini API: the art's pictures when the admin's setting says gemini, the default
for Claude's brain (api/src/lib/studio-settings.ts, brain.painter()).

    paint(job, label, prompt)    one picture (POST /v1beta/models/gemini-3.1-flash-image:generateContent), as image bytes

It takes GEMINI_API_KEY, the key Lyria's songs take, and logs each picture that comes back to the job's calls.jsonl
(studio/ledger.py) at Google's price, from the tokens the answer reports: $0.50 per million in, $60 per million
image tokens out (a 1K picture is 1,120 of them, $0.067) and $3 per million thinking tokens, a few hundred a picture.

The picture is 1K at 16:9, 1376 x 768 as JPEG; the art step fits it to the art on white. No tools are sent, so it
draws from the prompt alone, with no Google Search. Google marks every picture with its invisible SynthID watermark.
SAM 3.1 then finds the structures in it, as in Muse Image's pictures (studio/steps/locate.py).

A busy or failing server (429, 500, 502, 503, 504) is asked again three times. A picture Gemini will not make (a
blocked prompt, a safety stop, or an answer with no picture in it) raises Declined, which the art step counts as a
refused try.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from studio import brain, ledger, media
from studio.api import USER_AGENT

MODEL = "gemini-3.1-flash-image"
NAME = "Gemini 3.1 Flash Image"
API = "https://generativelanguage.googleapis.com/v1beta"
KEY_VAR = "GEMINI_API_KEY"
ASPECT_RATIO = "16:9"  # the art is 1792 x 1024, a little wider: fit() pads the sides
IMAGE_SIZE = "1K"  # 1376 x 768; "2K" is 2752 x 1536 at $0.101 a picture
# USD per million tokens: in, image out, text and thinking out (ai.google.dev/gemini-api/docs/pricing).
PRICE_IN, PRICE_IMAGE_OUT, PRICE_TEXT_OUT = 0.50, 60.0, 3.0
PICTURE_PRICE_USD = 0.067  # a 1K picture, for an answer that reports no tokens
TIMEOUT_S = 300
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS_S = (5, 20, 60)
# Why a candidate stopped without a picture because of what was asked: a refused try, asked again more plainly.
REFUSED = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
           "RECITATION", "IMAGE_RECITATION", "IMAGE_OTHER", "NO_IMAGE", "OTHER"}

# send(method, url, headers, body) -> (status, response headers, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class GeminiError(media.StepFailed):
    """The Gemini API did not give back what was asked."""

    code = "Gemini failed"


class Declined(GeminiError):
    """Gemini would not make this picture."""


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except TimeoutError as e:
        # Not retried: Gemini may still be making, and billing, what was asked.
        raise GeminiError(f"the Gemini API took over {TIMEOUT_S}s to answer") from e
    except (urllib.error.URLError, OSError) as e:
        raise GeminiError(f"the Gemini API did not answer: {getattr(e, 'reason', e)}") from e


send: Send = _urllib_send
sleep = time.sleep  # tests pass their own


def key() -> str:
    value = os.environ.get(KEY_VAR)
    if not value:
        raise GeminiError(f"{NAME} needs {KEY_VAR}")
    return value


def paint(job: Path, label: str, prompt: str, reference: Path | None = None) -> bytes:
    """One picture of the prompt at the art's aspect ratio, as JPEG or PNG bytes (any size: the caller fits it).
    With a reference, the prompt is an edit of that picture ("remove the writing; change nothing else")."""
    parts: list[dict] = []
    if reference is not None:
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(reference.read_bytes()).decode()}})
    parts.append({"text": prompt})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"],
                             "imageConfig": {"aspectRatio": ASPECT_RATIO, "imageSize": IMAGE_SIZE}},
    }
    ledger.check_ceiling(job)
    started = time.monotonic()
    status, answer = _post(f"/models/{urllib.parse.quote(MODEL, safe='')}:generateContent", body)
    if status != 200:
        raise GeminiError(f"{NAME} refused the picture: {status} {_message(answer)}")
    try:
        data = json.loads(answer)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        raise GeminiError(f"{NAME}'s answer is not JSON: {e}") from e
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise Declined(f"{NAME} blocked the prompt ({blocked})")
    candidate = (data.get("candidates") or [{}])[0] or {}
    picture = _picture(candidate)
    if picture is None:
        reason = candidate.get("finishReason") or "none given"
        if reason in REFUSED:
            raise Declined(f"{NAME} made no picture ({reason})")
        raise GeminiError(f"{NAME}'s answer has no picture in it (finish reason: {reason})")
    _log(job, label, started, data.get("usageMetadata"))
    return picture


def _picture(candidate: dict) -> bytes | None:
    """The answer's picture: the last image part that is not a thought (an interim picture, when it thinks)."""
    parts = (candidate.get("content") or {}).get("parts") or []
    images = [part.get("inlineData") or part.get("inline_data") for part in parts
              if isinstance(part, dict) and not part.get("thought")]
    images = [i for i in images if isinstance(i, dict)
              and str(i.get("mimeType") or i.get("mime_type") or "").startswith("image/")]
    if not images:
        return None
    try:
        picture = base64.b64decode(images[-1]["data"], validate=True)
    except (KeyError, ValueError, TypeError) as e:
        raise GeminiError(f"{NAME}'s picture is not readable image data: {e}") from e
    return picture or None


def cost(usage: dict | None) -> tuple[int, int, float]:
    """(tokens in, tokens out, USD) from the answer's usageMetadata: image tokens at the image price, the rest of
    the output (thinking, any text) at the text price. A picture's list price when the answer says nothing."""
    if not isinstance(usage, dict) or not usage.get("candidatesTokenCount"):
        return 0, 0, PICTURE_PRICE_USD
    tokens_in = int(usage.get("promptTokenCount") or 0)
    out = int(usage.get("candidatesTokenCount") or 0)
    thoughts = int(usage.get("thoughtsTokenCount") or 0)  # outside candidatesTokenCount, when it is reported
    image = sum(int(d.get("tokenCount") or 0) for d in usage.get("candidatesTokensDetails") or []
                if isinstance(d, dict) and d.get("modality") == "IMAGE")
    usd = (tokens_in * PRICE_IN + image * PRICE_IMAGE_OUT + (out - image + thoughts) * PRICE_TEXT_OUT) / 1_000_000
    return tokens_in, out + thoughts, usd


def _post(path: str, body: dict) -> tuple[int, bytes]:
    headers = {"x-goog-api-key": key(), "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for wait in (*RETRY_WAITS_S, None):
        status, _, answer = send("POST", API + path, headers, data)
        if status not in RETRY_STATUSES or wait is None:
            return status, answer
        sleep(wait)
    raise AssertionError("unreachable")


def _message(body: bytes) -> str:
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict):
        return " ".join(str(error[k]) for k in ("status", "message") if error.get(k))[:300]
    return body.decode("utf-8", "replace")[:300]


def _log(job: Path, label: str, started: float, usage: dict | None) -> None:
    tokens_in, tokens_out, usd = cost(usage)
    ledger.log(job, [brain.Call(label, "gemini", MODEL, tokens_in, tokens_out, round(usd, 6),
                                round(time.monotonic() - started, 1), "")])
