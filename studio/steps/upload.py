"""Step 9: upload (docs/PLAN.md, sections 5 and 10).

Copies what outlives the job folder to Cloudflare R2, then marks the videos READY:

    droplets/<answerId>/a<n>/out/song_h.mp4 ...   public: the two videos and their posters, for anyone to watch
    studio/<answerId>/a<n>/art/hook.png ...       private: art, masks, locations.json, audio, timings and
                                                  timelines, so a renderer fix can re-render without paying
                                                  again for the song and the art

a<n> is the attempt at the videos this machine holds (videos.json, studio/videos.py).
Each attempt writes its own files, so a run whose render went quiet and was handed to
another cannot overwrite the new attempt's videos, and a re-render gets new URLs that
no viewer or CDN has cached. The READY report carries the attempt too, and the API
takes it only from the attempt that holds the render. A job with no videos.json (a
step run by hand) uploads without the a<n> folder.

For each file the API mints a signed upload URL (POST /api/answers/:id/upload-url) and
the bytes go straight to R2 with the headers the API signed into it (the file's type, and
for a public file how long it may be cached), so the R2 key stays on the API and the
agent key is never sent to R2. The answer is the one in posted.json.

The files are an explicit list built from studio/media.py, the bundle's scenes (art)
and locations.json (masks), never a glob of the folder: the job also holds lock and log
files, and jobs.write's .<name>.partial files mid-write.

upload.progress.json records each file as it lands, with its hash, so a run that
stopped halfway, or a run after a re-render, uploads only what is new. Then the videos
are marked READY with their public URLs and posters, and only after that is
uploaded.json written: done means the page knows. If the report does not get through
the step fails, and running it again only sends the report. The READY report also
carries the videos' breakdown (studio/ledger.py): each media step's time, cost and
models, this step's up to the report, for the page to show under the videos.

upload_file() uploads one file on its own, for a run that copies each intermediate as
its step finishes (the job folder dies with the routine's machine).

A request that fails for a reason a moment may fix (no answer, 429, a 5xx) is tried
again twice, a few seconds apart, before the step fails: the runner gives the videos up
after a second failed step, and one storage blip should not count as one.

RENDERING and FAILED are not this step's to send: whatever runs steps 3-9 owns them.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from studio import brain, jobs, ledger, media
from studio.api import Api, ApiError
from studio.schemas import Bundle, Locations

UPLOADED = "uploaded.json"
PROGRESS = "upload.progress.json"
CLAIM = "videos.json"  # {answerId, attempt}, written by studio/videos.py
RETRY_DELAYS_S = (2.0, 6.0)  # waits before the second and third try of a transient failure
T = TypeVar("T")
PUBLIC, PRIVATE = "droplets", "studio"
Bucket = Literal["droplets", "studio"]
# What an API from before the move to R2 needed the studio to send as the type; the API names it since.
LEGACY_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".mp3": "audio/mpeg", ".json": "application/json"}


class UploadedFile(BaseModel):
    bucket: Bucket
    remote: str  # where it is in the bucket, under <answerId>/
    sha256: str
    size: int
    public_url: str | None = None


class Progress(BaseModel):
    answer_id: str
    attempt: int | None = None  # the attempt the files belong to; None for a step run by hand
    files: dict[str, UploadedFile] = {}


class Uploaded(Progress):
    song_video_url: str
    lesson_video_url: str
    song_poster_url: str | None = None
    lesson_poster_url: str | None = None
    hook_video_url: str | None = None  # the hook cut's, when it was made
    hook_poster_url: str | None = None
    missing: list[str] = []  # intermediate files the job did not have, so a re-render may need them made again


def is_done(job: Path) -> bool:
    try:
        jobs.read(job / UPLOADED, Uploaded)
        return True
    except (OSError, ValueError, ValidationError):
        return False


class _Posted(BaseModel):
    id: str


def answer_id(job: Path) -> str:
    try:
        answer = jobs.read(job / "posted.json", _Posted)
    except (OSError, ValueError, ValidationError) as e:
        raise media.StepFailed("the lesson is not posted yet (no posted.json with its answer id)") from e
    return answer.id


def held_attempt(job: Path, answer: str) -> int | None:
    """The attempt at this answer's videos this machine holds, or None when it holds none."""
    try:
        claim = json.loads((job / CLAIM).read_text(encoding="utf-8"))
        return int(claim["attempt"]) if claim.get("answerId") == answer else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def remote_path(path: str, attempt: int | None) -> str:
    """Where a job file goes in its bucket, under <answerId>/."""
    return f"a{attempt}/{path}" if attempt else path


def _retrying(what: str, call: Callable[[], T]) -> T:
    """call(), tried again on a failure a moment may fix; StepFailed once it gives up."""
    for wait in (*RETRY_DELAYS_S, None):
        try:
            return call()
        except ApiError as e:
            transient = e.status in (0, 429) or e.status >= 500
            if e.code == "UNAVAILABLE" and not e.details.get("retryable"):
                transient = False  # storage is not set up on the API: waiting will not fix it
            if not transient or wait is None:
                raise media.StepFailed(f"{what}: {e}") from e
            time.sleep(wait)
    raise AssertionError("unreachable")


def public_files(job: Path) -> list[str]:
    """The videos, which must both be there, and the posters that are; the hook cut and its poster when it was made."""
    missing = [media.video_path(v) for v in media.VIDEOS if not (job / media.video_path(v)).is_file()]
    if missing:
        raise media.StepFailed(f"nothing to upload yet: {', '.join(missing)} not rendered")
    posters = [media.poster_path(v) for v in media.VIDEOS if (job / media.poster_path(v)).is_file()]
    hook = [path(media.HOOK, media.HOOK_LAYOUT) for path in (media.video_path, media.poster_path)
            if (job / path(media.HOOK, media.HOOK_LAYOUT)).is_file()]
    return [media.video_path(v) for v in media.VIDEOS] + posters + hook


def private_files(job: Path) -> tuple[list[str], list[str]]:
    """What a re-render needs, as (there, missing): the art of every scene in the bundle,
    every mask locations.json names, and the fixed files of steps 4-7."""
    named = [media.LOCATIONS]
    try:
        named += [media.art_path(scene.id) for scene in jobs.read(job / "bundle.json", Bundle).scenes]
    except (OSError, ValueError, ValidationError):
        pass  # no bundle here: the art is reported missing below
    try:
        locations = jobs.read(job / media.LOCATIONS, Locations)
        named += [s.art for s in locations.scenes] + [m.mask for s in locations.scenes for m in s.structures]
    except (OSError, ValueError, ValidationError):
        pass  # locations.json itself is reported missing
    named += ([media.audio_path(k) for k in media.TIMED]
              + [media.timing_path(k) for k in media.TIMED]
              + [media.timeline_path(v) for v in media.VIDEOS]
              # The hook cut's, when the song has one, so a render picked up on another machine makes it too.
              + ([media.timeline_path(media.HOOK)] if (job / media.timeline_path(media.HOOK)).is_file() else []))
    named = list(dict.fromkeys(named))  # the art is named by both
    there = [p for p in named if (job / p).is_file()]
    # What each Muse Image picture's check found, which the timeline discloses beside it: a timeline made
    # again without them would drop its notice cards. A drawing has none, so none is missing.
    try:
        scenes = jobs.read(job / "bundle.json", Bundle).scenes
    except (OSError, ValueError, ValidationError):
        scenes = []
    there += [p for p in (media.check_path(scene.id) for scene in scenes) if (job / p).is_file()]
    return there, [p for p in named if p not in there]


def run(job: Path, ask=brain.ask, api: Api | None = None) -> str:
    del ask  # no model call in this step
    started = time.monotonic()
    answer = answer_id(job)
    public = public_files(job)
    private, missing = private_files(job)
    api = api or Api.from_env()
    attempt = held_attempt(job, answer)

    # The first file alone, so storage that is not set up fails on one request; then the rest several at once, since
    # each waits on the network, not on this machine. One that fails stops the ones not yet started and fails the
    # step; a retry sends only what is not there yet.
    todo = [(bucket, file) for bucket, files in ((PRIVATE, private), (PUBLIC, public)) for file in files]
    sent = upload_file(api, job, answer, *todo[0])[1] if todo else 0
    stop = threading.Event()

    def one(item: tuple[str, str]) -> int:
        if stop.is_set():
            return 0
        try:
            return upload_file(api, job, answer, *item)[1]
        except Exception:
            stop.set()
            raise

    with ThreadPoolExecutor(UPLOAD_WORKERS) as pool:
        sent += sum(pool.map(one, todo[1:]))

    progress = _progress(job, answer, attempt)

    def url(path: str) -> str | None:
        entry = progress.files.get(path)
        return entry.public_url if entry else None

    uploaded = Uploaded(
        **progress.model_dump(),
        song_video_url=url(media.video_path("song")), lesson_video_url=url(media.video_path("lesson")),
        song_poster_url=url(media.poster_path("song")), lesson_poster_url=url(media.poster_path("lesson")),
        hook_video_url=url(media.video_path(media.HOOK, media.HOOK_LAYOUT)),
        hook_poster_url=url(media.poster_path(media.HOOK, media.HOOK_LAYOUT)),
        missing=missing,
    )
    breakdown = ledger.try_breakdown(job, "videos", running=("upload", round(time.monotonic() - started, 1)))
    _retrying("files uploaded, but marking the videos READY failed", lambda: api.set_video(
        answer, "READY", attempt=attempt,
        song_video_url=uploaded.song_video_url, lesson_video_url=uploaded.lesson_video_url,
        song_poster_url=uploaded.song_poster_url, lesson_poster_url=uploaded.lesson_poster_url,
        hook_video_url=uploaded.hook_video_url, hook_poster_url=uploaded.hook_poster_url,
        breakdown=breakdown))
    jobs.write(job / UPLOADED, uploaded)  # last: done means READY was sent

    kept = len(public) + len(private) - sent
    line = f"uploaded {sent} file(s) for answer {answer}" + (f" ({kept} unchanged)" if kept else "")
    line += f"; videos READY: {uploaded.song_video_url}, {uploaded.lesson_video_url}"
    return line + (f"; not in the job: {', '.join(missing)}" if missing else "")


def _progress(job: Path, answer: str, attempt: int | None) -> Progress:
    """What earlier runs uploaded for this answer and attempt; nothing, if it was for another."""
    for name in (PROGRESS, UPLOADED):
        try:
            progress = jobs.read(job / name, Progress)  # uploaded.json reads as one too
        except (OSError, ValueError, ValidationError):
            continue
        if progress.answer_id == answer and progress.attempt == attempt:
            return progress
    return Progress(answer_id=answer, attempt=attempt)


UPLOAD_WORKERS = 8
_PROGRESS_LOCK = threading.Lock()


def upload_file(api: Api, job: Path, answer_id: str, bucket: Bucket, path: str) -> tuple[UploadedFile, int]:
    """Upload one file of the job to <bucket>/<answer_id>/[a<attempt>/]<path>, unless this
    exact file is already there, and record it in upload.progress.json. Returns the record
    and 1 if it was sent, 0 if it was already there. Raises StepFailed when the API or
    storage refuses it, after retrying a transient failure."""
    attempt = held_attempt(job, answer_id)
    progress = _progress(job, answer_id, attempt)
    remote = remote_path(path, attempt)
    data = (job / path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    done = progress.files.get(path)
    if done and done.bucket == bucket and done.remote == remote and done.sha256 == digest:
        return done, 0

    def send() -> dict:
        signed = api.upload_url(answer_id, bucket, remote)  # signed per try: a URL is good for two hours
        if not isinstance(signed, dict) or not isinstance(signed.get("uploadUrl"), str):
            raise media.StepFailed(f"{bucket}/{remote}: the API sent no upload URL")
        headers = signed.get("headers")
        if headers is None:
            # An API from before the move to R2 (not redeployed yet, or its deploy failed) signs Supabase uploads and
            # names no headers: send what Supabase took, so the droplet still gets its videos. The API moves them to
            # R2 once it is redeployed (api/src/lib/storage-move.ts).
            headers = {"Content-Type": LEGACY_TYPES.get(Path(path).suffix.lower(), "application/octet-stream"), "x-upsert": "true"}
        if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
            raise media.StepFailed(f"{bucket}/{remote}: the API sent upload headers the studio cannot use: {headers!r}")
        api.put_file(signed["uploadUrl"], data, headers)
        return signed

    signed = _retrying(f"{bucket}/{remote}", send)
    public_url = signed.get("publicUrl") if bucket == PUBLIC else None
    if bucket == PUBLIC and not public_url:
        raise media.StepFailed(f"{bucket}/{remote}: the API sent no public URL")
    entry = UploadedFile(bucket=bucket, remote=remote, sha256=digest, size=len(data), public_url=public_url)
    with _PROGRESS_LOCK:  # uploads run side by side: each adds its record to the file as it stands now
        progress = _progress(job, answer_id, attempt)
        progress.files[path] = entry
        jobs.write(job / PROGRESS, progress)
    return entry, 1
