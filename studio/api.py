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
# send(method, url, headers, body) -> (status, response body). Tests pass their own.
Send = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


class ApiError(RuntimeError):
    """A request the API refused, or one that never got an answer (status 0)."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"{status} {code}: {message}" if status else message)


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
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
        error = error if isinstance(error, dict) else {}
        raise ApiError(status, str(error.get("code", "HTTP_ERROR")), str(error.get("message", raw[:200].decode("utf-8", "replace"))))

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
