"""A stand-in popsmosis API for the operator simulation (tools/sim/README.md): in memory, on 127.0.0.1.

It serves the agent endpoints the studio and the operator call, with the rules the real API keeps
(api/src/routes/questions.ts, api/src/routes/videos.ts, api/src/routes/answers.ts):

    the topic queue and its lease (QUESTION_CLAIM_MINUTES), the claim and its renewal, the runs a topic is
    started on (a claim with newRun counts one; QUESTION_MAX_RUNS, then it is given up), a topic given up,
    the one answer per topic (409 with answerId when it is this agent's own), the videos' claim and its
    attempts (VIDEO_MAX_ATTEMPTS), a render gone quiet (VIDEO_STALE_MINUTES) or videos never claimed handed
    on by the videos queue, a render quiet on its last attempt marked FAILED, the videos given up, READY, and a
    signed upload URL whose PUT lands on /r2/... here.

Time is the simulation's: real time plus whatever advance() has added, so a lease can lapse or a render go
stale between two heartbeats without waiting an hour.

Faults. Each rule matches "METHOD /path" by a regular expression and answers the next `times` requests that
match (after letting `skip` through) with an HTTP status, or with "drop" (the connection closed with no
answer: the studio reads that as the API not answering). The status comes with an HTML page, as from the proxy
in front of a service that is down (Render's, Cloudflare's), unless `api` says the API itself refused, with its
JSON error. With "commit" the request is carried out first and its answer lost, as when a reply goes missing on
the way back.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SETTINGS = {
    "brain": {"default": "claude", "effort": "high"},
    "song": {"provider": "gemini"},
    "art": {"claude": "svg", "meta": "muse"},
    "spend": {"topicUsd": 20},
    "knobs": {},
}


class Fault:
    def __init__(self, match: str, status: int | str = 503, times: int = 1, skip: int = 0, commit: bool = False,
                 api: bool = False):
        self.pattern, self.status, self.times, self.skip, self.commit = re.compile(match), status, times, skip, commit
        self.api = api
        self.seen = 0
        self.fired = 0

    def take(self, line: str) -> bool:
        if not self.pattern.search(line) or self.fired >= self.times:
            return False
        self.seen += 1
        if self.seen <= self.skip:
            return False
        self.fired += 1
        return True


class State:
    def __init__(self, settings: dict | None = None, claim_minutes: float = 20, stale_minutes: float = 60,
                 max_attempts: int = 2, max_runs: int = 3):
        self.lock = threading.RLock()
        self.offset = 0.0
        self.settings = json.loads(json.dumps(settings or SETTINGS))
        self.claim_minutes, self.stale_minutes, self.max_attempts = claim_minutes, stale_minutes, max_attempts
        self.max_runs = max_runs
        self.questions: dict[str, dict] = {}
        self.answers: dict[str, dict] = {}
        self.attachments: dict[str, bytes] = {}
        self.files: dict[str, int] = {}  # r2 path -> bytes stored
        self.faults: list[Fault] = []
        self.log: list[str] = []  # every request, as "METHOD /path status"
        self.events: list[str] = []  # what changed, in words
        self.agent = "agent-1"

    def now(self) -> float:
        return time.time() + self.offset

    def advance(self, minutes: float) -> None:
        with self.lock:
            self.offset += minutes * 60
            self.events.append(f"clock +{minutes:g} min")

    def ask(self, content: str, attachments: list[tuple[str, str, bytes]] = ()) -> str:
        """A learner asks for a topic; attachments are (name, mime, bytes)."""
        with self.lock:
            qid = "q" + uuid.uuid4().hex[:12]
            listed = []
            for name, mime, data in attachments:
                aid = "a" + uuid.uuid4().hex[:10]
                self.attachments[aid] = data
                listed.append({"id": aid, "name": name, "mimeType": mime, "size": len(data),
                               "url": f"/api/attachments/{aid}", "expired": False})
            self.questions[qid] = {"id": qid, "content": content, "status": "PENDING", "attachments": listed,
                                   "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "claimedAt": None,
                                   "claimedBy": None, "failedReason": None, "answerId": None, "progress": None,
                                   "runs": 0}
            self.events.append(f"topic {qid} asked")
            return qid

    # --- what a scenario reads at the end ---------------------------------------------------

    def droplet(self, qid: str) -> dict:
        with self.lock:
            q = self.questions[qid]
            a = self.answers.get(q["answerId"]) if q["answerId"] else None
            return {"question": q["status"], "failedReason": q["failedReason"],
                    "answer": a and a["id"], "videoStatus": a and a["videoStatus"],
                    "videoAttempts": a and a["videoAttempts"], "videoError": a and a["videoError"],
                    "videoUrls": a and {k: v for k, v in a.items() if k.endswith("VideoUrl") and v},
                    "model": a and a.get("model"), "lessonBreakdown": a and a.get("breakdown"), "runs": q["runs"],
                    "videoBreakdown": a and a.get("videoBreakdown")}

    # --- the routes ---------------------------------------------------------------------------

    def _lease_free(self, q: dict) -> bool:
        return q["claimedAt"] is None or q["claimedAt"] < self.now() - self.claim_minutes * 60

    def _question_out(self, q: dict) -> dict:
        out = {k: q[k] for k in ("id", "content", "status", "attachments", "createdAt")}
        out["answer"] = None
        out["studio"] = self.settings
        out["brain"] = None
        out["runs"], out["maxRuns"] = q["runs"], self.max_runs
        return out

    def _runs_used(self, q: dict) -> None:
        q["status"], q["progress"] = "FAILED", None
        q["failedReason"] = (f"given up after {q['runs']} runs, each of which ended before the lesson was posted."
                             if q["runs"] != 1 else "given up after 1 run, which ended before the lesson was posted.")
        self.events.append(f"topic {q['id']} given up: its runs are used")

    def route(self, method: str, path: str, query: dict, body: dict | None, raw: bytes) -> tuple[int, dict | bytes | None]:
        now = self.now()
        m = re.fullmatch
        if method == "GET" and path == "/api/studio/settings":
            return 200, self.settings
        if method == "GET" and path == "/api/questions/queue":
            limit = int(query.get("limit", ["1"])[0])
            for q in self.questions.values():  # its runs used and none going on: given up
                if q["status"] == "PENDING" and q["runs"] >= self.max_runs and self._lease_free(q):
                    self._runs_used(q)
            free = [q for q in self.questions.values() if q["status"] == "PENDING" and self._lease_free(q)][:limit]
            for q in free:
                q["claimedAt"], q["claimedBy"] = now, self.agent
                self.events.append(f"queue handed out {q['id']}")
            return 200, {"data": [self._question_out(q) for q in free]}
        if method == "GET" and path == "/api/replies/queue":
            return 200, {"data": []}
        if (found := m(r"/api/questions/([^/]+)/claim", path)) and method == "POST":
            q = self.questions.get(found.group(1))
            if not q:
                return 404, _error("NOT_FOUND", "Question not found")
            if q["status"] == "PENDING" and (q["claimedBy"] == self.agent or self._lease_free(q)):
                if (body or {}).get("newRun"):
                    if q["runs"] >= self.max_runs:
                        self._runs_used(q)
                        return 409, _error("CONFLICT", "Question was marked failed")
                    q["runs"] += 1
                    self.events.append(f"topic {q['id']}: run {q['runs']} of {self.max_runs}")
                q["claimedAt"], q["claimedBy"] = now, self.agent
                return 200, self._question_out(q)
            return 409, _error("CONFLICT", {"ANSWERED": "Question already has an answer",
                                            "FAILED": "Question was marked failed"}.get(
                q["status"], "Another agent holds the claim on this question"))
        if (found := m(r"/api/questions/([^/]+)/progress", path)) and method == "PUT":
            q = self.questions.get(found.group(1))
            if not q or q["status"] != "PENDING":
                return 409, _error("CONFLICT", "Question already has an answer")
            q["progress"] = body
            return 204, None
        if (found := m(r"/api/attachments/([^/]+)", path)) and method == "GET":
            data = self.attachments.get(found.group(1))
            return (200, data) if data is not None else (404, _error("NOT_FOUND", "gone"))
        if (found := m(r"/api/questions/([^/]+)/fail", path)) and method == "POST":
            q = self.questions.get(found.group(1))
            if not q:
                return 404, _error("NOT_FOUND", "Question not found")
            if q["status"] != "PENDING":
                return 409, _error("CONFLICT", "Question already has an answer" if q["status"] == "ANSWERED"
                                   else "Question was already marked failed")
            q["status"], q["failedReason"] = "FAILED", (body or {}).get("reason", "")
            self.events.append(f"topic {q['id']} given up: {q['failedReason'][:80]}")
            return 200, {"id": q["id"], "status": "FAILED", "failedReason": q["failedReason"]}
        if (found := m(r"/api/questions/([^/]+)/answer", path)) and method == "POST":
            q = self.questions.get(found.group(1))
            if not q:
                return 404, _error("NOT_FOUND", "Question not found")
            if q["status"] == "ANSWERED":
                return 409, {"error": {"code": "CONFLICT", "message": "Question already has an answer",
                                       "answerId": q["answerId"]}}
            if q["status"] == "FAILED":
                return 409, _error("CONFLICT", "Question was marked failed")
            if q["claimedBy"] != self.agent:
                return 409, _error("CONFLICT", "Claim this question before answering it")
            aid = "ans" + uuid.uuid4().hex[:10]
            self.answers[aid] = {"id": aid, "questionId": q["id"], "content": body.get("content"),
                                 "bundle": body.get("bundle"), "factcheck": body.get("factcheck"),
                                 "model": body.get("model"), "breakdown": body.get("breakdown"),
                                 "videoStatus": None, "videoAttempts": 0, "videoUpdatedAt": None, "videoError": None,
                                 "songVideoUrl": None, "lessonVideoUrl": None, "hookVideoUrl": None,
                                 "videoBreakdown": None, "createdAt": now}
            q["status"], q["answerId"] = "ANSWERED", aid
            self.events.append(f"topic {q['id']} answered as {aid}")
            return 201, {"id": aid, "questionId": q["id"]}
        if (found := m(r"/api/answers/([^/]+)/video/claim", path)) and method == "POST":
            a = self.answers.get(found.group(1))
            if not a:
                return 404, _error("NOT_FOUND", "Answer not found")
            attempt = (body or {}).get("attempt")
            renewed = False
            if attempt is not None and a["videoStatus"] == "RENDERING" and a["videoAttempts"] == attempt:
                a["videoUpdatedAt"], renewed = now, True
            elif (a["videoAttempts"] < self.max_attempts and (
                    a["videoStatus"] in (None, "FAILED") or (
                    a["videoStatus"] == "RENDERING" and a["videoUpdatedAt"] < now - self.stale_minutes * 60))):
                a.update(videoStatus="RENDERING", videoUpdatedAt=now, videoError=None,
                         videoAttempts=a["videoAttempts"] + 1)
                self.events.append(f"videos of {a['questionId']}: attempt {a['videoAttempts']} started")
            else:
                if a["videoStatus"] == "READY":
                    return 409, _error("CONFLICT", "The videos are already made")
                if a["videoStatus"] == "RENDERING":
                    return 409, {"error": {"code": "CONFLICT", "message": "Another run is making these videos",
                                           "attempt": a["videoAttempts"]}}
                return 409, _error("CONFLICT", f"The videos were given up after {a['videoAttempts']} attempt(s)")
            return 200, self._claim_out(a, renewed)
        if (found := m(r"/api/answers/([^/]+)/video/progress", path)) and method == "PUT":
            a = self.answers.get(found.group(1))
            if not a or a["videoStatus"] != "RENDERING" or (body or {}).get("attempt") != a["videoAttempts"]:
                return 409, _error("CONFLICT", "not this attempt's")
            return 204, None
        if method == "GET" and path == "/api/videos/queue":
            limit = int(query.get("limit", ["1"])[0])
            stale_before = now - self.stale_minutes * 60
            for a in self.answers.values():  # quiet on its last attempt: no later run will take it
                if a["videoStatus"] == "RENDERING" and a["videoUpdatedAt"] < stale_before \
                        and a["videoAttempts"] >= self.max_attempts:
                    a.update(videoStatus="FAILED", videoError="The machine making them went away before they were "
                             f"finished, on their last attempt ({a['videoAttempts']} of {self.max_attempts}).")
                    self.events.append(f"videos of {a['questionId']} failed: quiet on their last attempt")
            videos_on = str(self.settings.get("knobs", {}).get("STUDIO_VIDEOS", "on")).lower() not in ("off", "0", "false", "no")
            stale = [a for a in self.answers.values() if a["videoAttempts"] < self.max_attempts and (
                (a["videoStatus"] == "RENDERING" and a["videoUpdatedAt"] < stale_before)
                or (videos_on and a["videoStatus"] is None and a["bundle"] is not None
                    and stale_before - 24 * 3600 < a["createdAt"] < stale_before))][:limit]
            for a in stale:
                a.update(videoStatus="RENDERING", videoUpdatedAt=now, videoAttempts=a["videoAttempts"] + 1)
                self.events.append(f"videos of {a['questionId']}: recovered as attempt {a['videoAttempts']}")
            return 200, {"data": [self._claim_out(a, False) for a in stale]}
        if (found := m(r"/api/answers/([^/]+)/video/fail", path)) and method == "POST":
            a = self.answers.get(found.group(1))
            if not a or a["videoStatus"] != "RENDERING" or (body or {}).get("attempt") != a["videoAttempts"]:
                return 409, _error("CONFLICT", "not this attempt's")
            a.update(videoStatus="FAILED", videoError=(body or {}).get("reason"))
            self.events.append(f"videos of {a['questionId']} given up: {str(a['videoError'])[:80]}")
            return 200, {"status": "FAILED", "error": a["videoError"]}
        if (found := m(r"/api/answers/([^/]+)/upload-url", path)) and method == "POST":
            a = self.answers.get(found.group(1))
            if not a:
                return 404, _error("NOT_FOUND", "Answer not found")
            bucket, file = body["bucket"], body["file"]
            where = f"{bucket}/{a['id']}/{file}"
            return 200, {"uploadUrl": f"{self.base_url}/r2/{where}", "headers": {"Content-Type": "application/octet-stream"},
                         "path": where, "publicUrl": f"{self.base_url}/r2/{where}" if bucket == "droplets" else None}
        if path.startswith("/r2/") and method == "PUT":
            self.files[path[4:]] = len(raw)
            return 200, None
        if (found := m(r"/api/answers/([^/]+)/video", path)) and method == "PATCH":
            a = self.answers.get(found.group(1))
            if not a:
                return 404, _error("NOT_FOUND", "Answer not found")
            if a["videoStatus"] == "READY":
                return 409, _error("CONFLICT", "These videos are READY; they can no longer change")
            if body.get("attempt") is not None and body["attempt"] != a["videoAttempts"]:
                return 409, _error("CONFLICT", "a newer attempt holds these videos")
            for key in ("songVideoUrl", "lessonVideoUrl", "hookVideoUrl"):
                if body.get(key):
                    a[key] = body[key]
            a["videoStatus"] = body["status"]
            if body["status"] == "READY":
                a["videoBreakdown"] = body.get("breakdown")
                self.events.append(f"videos of {a['questionId']} READY")
            return 200, {"id": a["id"], "videoStatus": a["videoStatus"]}
        return 404, _error("NOT_FOUND", f"no route {method} {path}")

    def _claim_out(self, a: dict, renewed: bool) -> dict:
        out = {"answerId": a["id"], "questionId": a["questionId"], "attempt": a["videoAttempts"],
               "maxAttempts": self.max_attempts, "status": a["videoStatus"], "brain": None, "model": a.get("model"),
               "studio": self.settings}
        if not renewed:
            out.update(bundle=a["bundle"], factcheck=a["factcheck"])
        return out


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


class _Handler(BaseHTTPRequestHandler):
    state: State  # set by serve()

    def log_message(self, *args):  # quiet
        pass

    def _handle(self):
        state = self.state
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = None
        if raw and not split.path.startswith("/r2/"):
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        line = f"{self.command} {split.path}"
        with state.lock:
            fault = next((f for f in state.faults if f.take(line)), None)
            status, answer = (None, None)
            if fault is None or fault.commit:
                status, answer = state.route(self.command, split.path, parse_qs(split.query), body, raw)
            if fault is not None:
                state.log.append(f"{line} -> fault {fault.status}" + (f" (after {status})" if fault.commit else ""))
                status = fault.status
                answer = _error("UNAVAILABLE", "simulated fault") if fault.api else (
                    f"<html><body><h1>{status} Service Unavailable</h1></body></html>".encode())
            else:
                state.log.append(f"{line} -> {status}")
        if status == "drop":
            self.close_connection = True
            self.connection.close()
            return
        if isinstance(answer, bytes):
            data, kind = answer, "text/html" if answer.startswith(b"<html>") else "application/octet-stream"
        else:
            data, kind = (json.dumps(answer).encode() if answer is not None else b""), "application/json"
        self.send_response(int(status))
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = _handle


def serve(state: State) -> ThreadingHTTPServer:
    """Start the API on a free port in a thread of its own; state.base_url says where."""
    handler = type("Handler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, name="fake api", daemon=True).start()
    return server
