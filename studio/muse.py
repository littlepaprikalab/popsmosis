"""Muse Image and SAM 3.1 on Meta's Model API: the art when Muse Image paints it, and the masks of any painted art.

    paint(job, label, prompt, reference)      one picture from Muse Image (POST /v1/images/generations), as PNG
                                              bytes; with a reference picture, redrawn from it (/v1/images/edits)
    segment(job, label, art, phrase=...)      SAM 3.1 (POST /v1/responses): every match of a short noun phrase
    segment(job, label, art, box=...)         or the one thing inside a box, each as a full-size mask

Both take MODEL_API_KEY, the Meta key, whatever the brain, and log each call that comes back to the
job's calls.jsonl (studio/ledger.py) at Meta's price: $0.01 a picture, $2.50 per 1,000
images segmented. The request and answer shapes follow Meta's docs
(dev.meta.ai/docs/image-generation, dev.meta.ai/docs/sam); both first ran live on Oct 28.

SAM answers in a token grammar of its own, one record per match: a box, then a mask
covering only that box, in an encoding of Meta's. meta-sam-parser, Meta's own parser,
reads it; segment() puts each mask back on the whole art.

A busy or failing server (429, 502, 503, 504), or a connection that drops, is asked again
three times; 500 is not, since Meta says it will not go away on a retry, nor a timeout,
since Meta may still be making (and billing) what was asked. A picture Muse Image will not make
(content policy) raises Declined, which the art step counts as a refused try.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from studio import brain, ledger, media
from studio.api import USER_AGENT
from studio.schemas import Box

IMAGE_MODEL = "muse-image-1.0"
SAM_MODEL = "sam-3.1"
IMAGE_PRICE_USD = 0.01  # per picture made; refused or filtered ones are not billed
SAM_PRICE_USD = 0.0025  # per image segmented ($2.50 per 1,000)
TIMEOUT_S = 300
RETRY_STATUSES = {429, 502, 503, 504}
RETRY_WAITS_S = (5, 20, 60)
# SAM calls this process has waiting on Meta at once, however many scenes and structures ask (locate asks for
# up to 16): a 429 still busy after the retries fails the step, and Meta publishes no limit to keep under. Four,
# as many as locate asked at once when it went one structure at a time per scene; raise it once live runs show
# Meta takes more.
SAM_IN_FLIGHT = 4
_sam_slots = threading.BoundedSemaphore(SAM_IN_FLIGHT)

# send(method, url, headers, body) -> (status, response headers, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class MuseError(media.StepFailed):
    """Meta's API did not give back what was asked."""

    code = "Meta's API failed"


class Declined(MuseError):
    """Muse Image would not make this picture (its content policy)."""

    code = "the picture was declined"  # what was asked, not an outage: no later try is owed it (studio/retry.py)


class NoAnswer(MuseError):
    """The request never reached Meta, or the connection dropped: safe to send again."""


@dataclass
class Match:
    """One thing SAM found: where it shows on the art, and how sure SAM is (None when it did not say)."""

    mask: np.ndarray  # bool, the art's height x width
    confidence: float | None


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except TimeoutError as e:
        # Not retried: Meta may still be making, and billing, what was asked.
        raise MuseError(f"Meta's API took over {TIMEOUT_S}s to answer") from e
    except (urllib.error.URLError, OSError) as e:
        raise NoAnswer(f"Meta's API did not answer: {getattr(e, 'reason', e)}") from e


send: Send = _urllib_send
sleep = time.sleep  # tests pass their own


# --- Muse Image -------------------------------------------------------------------------


def paint(job: Path, label: str, prompt: str, reference: Path | None = None) -> bytes:
    """One picture of the prompt at the art's aspect ratio, as PNG bytes (any size: the caller fits it). With a
    reference picture it is redrawn from that one, which keeps its framing and look (a process's later stages
    from its first)."""
    ledger.check_ceiling(job)
    body = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": f"{media.ART_WIDTH}x{media.ART_HEIGHT}",  # an aspect ratio, not a size in pixels
        "response_format": "b64_json",
        "output_format": "png",
        # A diagram from its prompt alone: no web search or shell (PLAN section 5, step 3).
        "tool_enablement": {"enable_web_search": False, "enable_shell": False},
    }
    if reference is not None:
        body["images"] = [{"image_url": "data:image/png;base64," + base64.standard_b64encode(reference.read_bytes()).decode("ascii")}]
    started = time.monotonic()
    status, answer = _post("/images/edits" if reference is not None else "/images/generations", body)
    if status == 400 and _error(answer).get("code") == "content_policy_violation":
        raise Declined(f"Muse Image declined the picture: {_message(answer)}")
    if status != 200:
        raise MuseError(f"Muse Image refused the picture: {status} {_message(answer)}")
    try:
        data = json.loads(answer)
        picture = base64.b64decode(data["data"][0]["b64_json"], validate=True)
        usage = data.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise MuseError(f"Muse Image's answer has no picture in it: {e}") from e
    _log(job, label, IMAGE_MODEL, started, IMAGE_PRICE_USD, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return picture


# --- SAM 3.1 ----------------------------------------------------------------------------


def segment(job: Path, label: str, art: Path, *, phrase: str | None = None, box: Box | None = None) -> list[Match]:
    """Every match SAM finds on the art for a noun phrase, or the one thing inside a box (art pixels).

    Each match's mask is the whole art's size. No match is an empty list, not an error.
    """
    ledger.check_ceiling(job)
    from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

    if (phrase is None) == (box is None):
        raise ValueError("segment() takes a phrase or a box, not both or neither")
    width, height = media.ART_WIDTH, media.ART_HEIGHT
    if box is not None:
        edges = PromptBox(left=max(0, int(box.x0)), top=max(0, int(box.y0)),
                          right=min(width, int(np.ceil(box.x1))), bottom=min(height, int(np.ceil(box.y1))))
        phrase = build_object_prompt(size=(width, height),
                                     objects=[PromptObject(id=0, prompts=[FramePrompt(frame=0, box=edges)])])
    image = "data:image/png;base64," + base64.standard_b64encode(art.read_bytes()).decode("ascii")
    body = {
        "model": SAM_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": phrase},
            {"type": "input_image", "image_url": image},
        ]}],
        "metadata": {"include_confidence": "true"},
    }
    with _sam_slots:  # held through the retries, so a busy Meta slows every caller down rather than more asking
        started = time.monotonic()
        status, answer = _post("/responses", body)
    if status != 200:
        raise MuseError(f"SAM 3.1 refused the request: {status} {_message(answer)}")
    try:
        data = json.loads(answer)
    except ValueError as e:
        raise MuseError(f"SAM 3.1's answer is not JSON: {e}") from e
    if data.get("status") not in (None, "completed"):
        raise MuseError(f"SAM 3.1 did not finish: {data.get('status')} {json.dumps(data.get('error'))[:200]}")
    _log(job, label, SAM_MODEL, started, SAM_PRICE_USD)
    return matches(output_text(data), width, height)


def output_text(response: dict) -> str:
    """The text of a Responses API answer: every output_text part of every message, in order."""
    return "".join(part.get("text", "") for item in response.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")


def matches(text: str, width: int, height: int) -> list[Match]:
    """SAM's records as masks on a width x height art. Raises MuseError when the text does not parse."""
    from meta_sam_parser import CompletedOutcome, SegmentationMaskRecord, decode_mask_to_raster, image_segmentation_format

    if not text.strip():
        return []
    parser = image_segmentation_format().create_parser()
    parser.push(text if text.endswith("\n") else text + "\n")
    result = parser.finish(CompletedOutcome()).result
    found = []
    for record in result.records:
        if not isinstance(record, SegmentationMaskRecord):
            continue
        raster = np.frombuffer(decode_mask_to_raster(record.mask), np.uint8)
        found.append(Match(_placed(raster.reshape(record.mask.height, record.mask.width).astype(bool),
                                   record.bounds, width, height), record.confidence))
    if not found and result.diagnostics:
        raise MuseError(f"SAM 3.1's answer did not parse: {result.diagnostics[0].message}")
    return found


def _placed(raster: np.ndarray, bounds, width: int, height: int) -> np.ndarray:
    """A box's mask put back on the whole art. The mask may be coarser than its box: each of the
    box's pixels takes the mask cell it falls in."""
    left, top, right, bottom = (int(v) for v in (bounds.left, bounds.top, bounds.right, bounds.bottom))
    rows = np.arange(bottom - top) * raster.shape[0] // max(1, bottom - top)
    cols = np.arange(right - left) * raster.shape[1] // max(1, right - left)
    whole = np.zeros((height, width), bool)
    y0, y1, x0, x1 = max(0, top), min(height, bottom), max(0, left), min(width, right)
    if y0 < y1 and x0 < x1:
        whole[y0:y1, x0:x1] = raster[np.ix_(rows, cols)][y0 - top:y1 - top, x0 - left:x1 - left]
    return whole


# --- helpers ----------------------------------------------------------------------------


def _post(path: str, body: dict) -> tuple[int, bytes]:
    headers = {"Authorization": f"Bearer {brain.meta_key()}", "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = brain.META_BASE_URL + path
    for wait in (*RETRY_WAITS_S, None):
        try:
            status, _, answer = send("POST", url, headers, data)
        except NoAnswer:  # a dropped connection (seen live: "Connection reset by peer"), as audio does
            if wait is None:
                raise
        else:
            if status not in RETRY_STATUSES or wait is None:
                return status, answer
        sleep(wait)
    raise AssertionError("unreachable")


def _error(body: bytes) -> dict:
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return {}
    return error if isinstance(error, dict) else {}


def _message(body: bytes) -> str:
    error = _error(body)
    return str(error.get("message") or error.get("code") or body.decode("utf-8", "replace"))[:300]


def _log(job: Path, label: str, model: str, started: float, cost_usd: float,
         input_tokens: int = 0, output_tokens: int = 0) -> None:
    ledger.log(job, [brain.Call(label, "meta", model, int(input_tokens or 0), int(output_tokens or 0), cost_usd,
                                round(time.monotonic() - started, 1), "")])
