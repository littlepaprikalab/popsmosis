"""The popsmosis API, as the studio calls it with the teacher's agent key.

POPSMOSIS_API_URL and POPSMOSIS_API_KEY are the same two variables the skill file's
shell commands read. Only the agent endpoints the studio needs are here
(README, "API"); errors come back as ApiError with the API's own code and message.

The one request that does not go to the API is put_file: the bytes of a video or an
intermediate file go straight to Supabase Storage, on a signed URL the API minted
(upload_url), so neither the Supabase key nor the agent key is ever sent there.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

TIMEOUT_S = 60
# A progress report is for the page only and is sent on a step's way in and out: one the API is slow to take is
# dropped well before it could hold a render up.
PROGRESS_TIMEOUT_S = 5
# Cloudflare in front of the API refuses urllib's default "Python-urllib" agent (error 1010).
USER_AGENT = "popsmosis-studio/1"
# send(method, url, headers, body[, timeout=seconds]) -> (status, response body). Tests pass their own.
Send = Callable[..., tuple[int, bytes]]
# A body bigger than this is streamed in blocks: urllib sends bytes in one sendall, and
# the timeout would then cap the whole upload of a video rather than each block of it.
STREAM_OVER_BYTES = 1024 * 1024


class ApiError(RuntimeError):
    """A request the API refused, or one that never got an answer (status 0)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}  # the error body's other fields, such as a 409's answerId
        super().__init__(f"{status} {code}: {message}" if status else message)


def _urllib_send(method: str, url: str, headers: dict, body: bytes | None, timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT, **headers}
    data: bytes | io.BytesIO | None = body
    if body is not None and len(body) > STREAM_OVER_BYTES:
        data = io.BytesIO(body)  # sent 8 KB at a time; the length keeps it from going chunked
        headers["Content-Length"] = str(len(body))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, raw = self.send(method, self.base_url + path, headers, data, **({"timeout": timeout} if timeout else {}))
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

    def studio_settings(self) -> dict:
        """The settings an admin sets on /admin/studio: {brain: {default, effort}, art: {claude, meta}}. {} from an
        API too old to have them, and the studio's own defaults apply."""
        try:
            return self.request("GET", "/api/studio/settings")
        except ApiError as e:
            if e.status == 404:
                return {}
            raise

    def claim(self, question_id: str) -> dict:
        """Take or renew the lease on one question; returns it with its attachments."""
        return self.request("POST", f"/api/questions/{question_id}/claim", {})

    def post_answer(self, question_id: str, body: dict) -> dict:
        return self.request("POST", f"/api/questions/{question_id}/answer", body)

    def fail(self, question_id: str, reason: str) -> dict:
        return self.request("POST", f"/api/questions/{question_id}/fail", {"reason": reason})

    def report_progress(self, question_id: str, progress: dict) -> None:
        """Where the lesson is, for the page (studio/ledger.py's progress()): the steps running now, the ones
        whose last try failed, the bill so far, and whether the videos come after. 409 unless this agent holds
        the claim and the topic has no lesson yet."""
        self.request("PUT", f"/api/questions/{question_id}/progress", progress, timeout=PROGRESS_TIMEOUT_S)

    # --- the videos (docs/PLAN.md, section 10) ----------------------------------

    def upload_url(self, answer_id: str, bucket: str, file: str) -> dict:
        """A signed URL to PUT one file to, at <bucket>/<answer_id>/<file>: { uploadUrl, path, publicUrl }.
        publicUrl is set for the public "droplets" bucket only. 503 until the API has SUPABASE_URL."""
        return self.request("POST", f"/api/answers/{answer_id}/upload-url", {"bucket": bucket, "file": file})

    def set_video(self, answer_id: str, status: str, *, error: str | None = None, attempt: int | None = None,
                  song_video_url: str | None = None, lesson_video_url: str | None = None,
                  song_poster_url: str | None = None, lesson_poster_url: str | None = None,
                  hook_video_url: str | None = None, hook_poster_url: str | None = None,
                  breakdown: dict | None = None) -> dict:
        """RENDERING, READY or FAILED (with error). A URL left as None keeps the one already saved.
        With the attempt this run holds, 409 unless it is still the render's current attempt.
        breakdown, with READY: the media steps' time, cost and models (studio/ledger.py)."""
        body: dict = {"status": status}
        if attempt is not None:
            body["attempt"] = attempt
        for key, value in (("songVideoUrl", song_video_url), ("lessonVideoUrl", lesson_video_url),
                           ("songPosterUrl", song_poster_url), ("lessonPosterUrl", lesson_poster_url),
                           ("hookVideoUrl", hook_video_url), ("hookPosterUrl", hook_poster_url)):
            if value is not None:
                body[key] = value
        if error is not None:
            body["error"] = error
        if breakdown is not None:
            body["breakdown"] = breakdown
        return self.request("PATCH", f"/api/answers/{answer_id}/video", body)

    def put_file(self, upload_url: str, data: bytes, content_type: str, cache_seconds: int = 3600) -> None:
        """PUT one file's bytes to a signed Supabase Storage URL, as supabase-js's uploadToSignedUrl
        does: the raw bytes, their Content-Type, and the token in the URL as the only credential."""
        headers = {"Content-Type": content_type, "Cache-Control": f"max-age={cache_seconds}", "x-upsert": "true"}
        status, raw = self.send("PUT", upload_url, headers, data)
        if not 200 <= status < 300:
            try:
                message = json.loads(raw).get("message") or json.loads(raw).get("error") or ""
            except (ValueError, AttributeError):
                message = ""
            raise ApiError(status, "UPLOAD_FAILED", f"storage answered {status}" + (f": {message}" if message else ""))
    # --- the videos (docs/PLAN.md, section 12) ---------------------------------

    def claim_videos(self, answer_id: str, attempt: int | None = None) -> dict:
        """Start an attempt at the videos (no attempt), or renew the one this run holds.

        Returns {answerId, questionId, attempt, status, bundle, factcheck}; 409 when another
        run holds them, they are made, or their attempts are used up.
        """
        return self.request("POST", f"/api/answers/{answer_id}/video/claim",
                            {"attempt": attempt} if attempt is not None else {})

    def report_video_progress(self, answer_id: str, attempt: int | None, progress: dict) -> None:
        """Where the render is, for the page: as report_progress, its bill the videos' so far. 409 unless the
        videos are rendering under this attempt."""
        body = {**progress, **({"attempt": attempt} if attempt is not None else {})}
        self.request("PUT", f"/api/answers/{answer_id}/video/progress", body, timeout=PROGRESS_TIMEOUT_S)

    def videos_queue(self, limit: int = 1) -> list[dict]:
        """This agent's droplets whose render died with its machine, each claimed as its next attempt."""
        return self.request("GET", f"/api/videos/queue?limit={limit}").get("data", [])

    def give_up_videos(self, answer_id: str, reason: str, attempt: int, breakdown: dict | None = None) -> dict:
        """Give the videos up; breakdown: what the failed tries took and cost (studio/ledger.py)."""
        body: dict = {"reason": reason, "attempt": attempt}
        if breakdown is not None:
            body["breakdown"] = breakdown
        return self.request("POST", f"/api/answers/{answer_id}/video/fail", body)
