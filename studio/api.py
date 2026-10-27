"""The popsmosis API, as the studio calls it with the teacher's agent key.

POPSMOSIS_API_URL and POPSMOSIS_API_KEY are the same two variables the skill file's
shell commands read. Only the agent endpoints the studio needs are here
(README, "API"); errors come back as ApiError with the API's own code and message.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

TIMEOUT_S = 60
# Cloudflare in front of the API refuses urllib's default "Python-urllib" agent (error 1010).
USER_AGENT = "popsmosis-studio/1"
# send(method, url, headers, body) -> (status, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


class ApiError(RuntimeError):
    """A request the API refused, or one that never got an answer (status 0)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}  # the error body's other fields, such as a 409's answerId
        super().__init__(f"{status} {code}: {message}" if status else message)


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(0, "UNREACHABLE", f"the API did not answer: {getattr(e, 'reason', e)}") from e


@dataclass
class Api:
    base_url: str
    key: str
    send: Send = _urllib_send

    @classmethod
    def from_env(cls) -> Api:
        url, key = os.environ.get("POPSMOSIS_API_URL"), os.environ.get("POPSMOSIS_API_KEY")
        if not url or not key:
            raise ApiError(0, "NOT_CONFIGURED", "POPSMOSIS_API_URL and POPSMOSIS_API_KEY must both be set")
        return cls(url.rstrip("/"), key)

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, raw = self.send(method, self.base_url + path, headers, data)
        try:
            answer = json.loads(raw) if raw else {}
        except ValueError:
            answer = {}
        if 200 <= status < 300:
            return answer
        error = answer.get("error") if isinstance(answer, dict) else None
        if not isinstance(error, dict):  # a proxy's HTML page, not the API: no body worth repeating
            raise ApiError(status, "HTTP_ERROR", f"the API answered {status}")
        details = {k: v for k, v in error.items() if k not in ("code", "message")}
        raise ApiError(status, str(error.get("code", "HTTP_ERROR")), str(error.get("message", "")), details)

    def download(self, path: str) -> bytes:
        """The bytes at an API path, such as an attachment's url."""
        status, raw = self.send("GET", self.base_url + path, {"Authorization": f"Bearer {self.key}"}, None)
        if status != 200:
            raise ApiError(status, "DOWNLOAD_FAILED", f"GET {path} answered {status}")
        return raw

    # --- the endpoints --------------------------------------------------------

    def claim(self, question_id: str) -> dict:
        """Take or renew the lease on one question; returns it with its attachments."""
        return self.request("POST", f"/api/questions/{question_id}/claim", {})

    def post_answer(self, question_id: str, body: dict) -> dict:
        return self.request("POST", f"/api/questions/{question_id}/answer", body)

    def fail(self, question_id: str, reason: str) -> dict:
        return self.request("POST", f"/api/questions/{question_id}/fail", {"reason": reason})

    # --- the videos (docs/PLAN.md, section 12) ---------------------------------

    def claim_videos(self, answer_id: str, attempt: int | None = None) -> dict:
        """Start an attempt at the videos (no attempt), or renew the one this run holds.

        Returns {answerId, questionId, attempt, bundle, factcheck, video}; 409 when another
        run holds them, they are made, or their attempts are used up.
        """
        return self.request("POST", f"/api/answers/{answer_id}/video/claim",
                            {"attempt": attempt} if attempt is not None else {})

    def videos_queue(self, limit: int = 1) -> list[dict]:
        """This agent's droplets whose render died with its machine, each claimed as its next attempt."""
        return self.request("GET", f"/api/videos/queue?limit={limit}").get("data", [])

    def give_up_videos(self, answer_id: str, reason: str, attempt: int) -> dict:
        return self.request("POST", f"/api/answers/{answer_id}/video/fail", {"reason": reason, "attempt": attempt})
