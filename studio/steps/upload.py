"""Step 9: upload (docs/PLAN.md, sections 5 and 10).

Copies what outlives the job folder to Supabase Storage, then marks the videos READY:

    droplets/<answerId>/out/song_h.mp4 ...    public: the two videos and their posters, for anyone to watch
    studio/<answerId>/art/hook.png ...        private: art, masks, locations.json, audio, timings and
                                              timelines, so a renderer fix can re-render without paying
                                              again for the song and the art

For each file the API mints a signed upload URL (POST /api/answers/:id/upload-url) and
the bytes go straight to Supabase, so the Supabase key stays on the API and the agent
key is never sent to Supabase. The answer is the one in posted.json.

The files are an explicit list built from studio/media.py, the bundle's scenes (art)
and locations.json (masks), never a glob of the folder: the job also holds lock and log
files, and jobs.write's .<name>.partial files mid-write.

upload.progress.json records each file as it lands, with its hash, so a run that
stopped halfway, or a run after a re-render, uploads only what is new. Then the videos
are marked READY with their public URLs and posters, and only after that is
uploaded.json written: done means the page knows. If the report does not get through
the step fails, and running it again only sends the report.

upload_file() uploads one file on its own, for a run that copies each intermediate as
its step finishes (the job folder dies with the routine's machine).

RENDERING and FAILED are not this step's to send: whatever runs steps 3-9 owns them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from studio import brain, jobs, media
from studio.api import Api, ApiError
from studio.schemas import Bundle, Locations

UPLOADED = "uploaded.json"
PROGRESS = "upload.progress.json"
PUBLIC, PRIVATE = "droplets", "studio"
Bucket = Literal["droplets", "studio"]

CONTENT_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".mp3": "audio/mpeg", ".json": "application/json"}
# A re-render overwrites the public files in place; a short cache lets viewers see the new one soon.
PUBLIC_CACHE_S = 300
PRIVATE_CACHE_S = 3600


class UploadedFile(BaseModel):
    bucket: Bucket
    sha256: str
    size: int
    public_url: str | None = None


class Progress(BaseModel):
    answer_id: str
    files: dict[str, UploadedFile] = {}


class Uploaded(Progress):
    song_video_url: str
    lesson_video_url: str
    song_poster_url: str | None = None
    lesson_poster_url: str | None = None
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


def public_files(job: Path) -> list[str]:
    """The videos, which must both be there, and the posters that are."""
    missing = [media.video_path(v) for v in media.VIDEOS if not (job / media.video_path(v)).is_file()]
    if missing:
        raise media.StepFailed(f"nothing to upload yet: {', '.join(missing)} not rendered")
    posters = [media.poster_path(v) for v in media.VIDEOS if (job / media.poster_path(v)).is_file()]
    return [media.video_path(v) for v in media.VIDEOS] + posters


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
              + [media.timeline_path(v) for v in media.VIDEOS])
    named = list(dict.fromkeys(named))  # the art is named by both
    there = [p for p in named if (job / p).is_file()]
    return there, [p for p in named if p not in there]


def run(job: Path, ask=brain.ask, api: Api | None = None) -> str:
    del ask  # no model call in this step
    answer = answer_id(job)
    public = public_files(job)
    private, missing = private_files(job)
    api = api or Api.from_env()

    sent = 0
    for bucket, files in ((PRIVATE, private), (PUBLIC, public)):
        for file in files:
            sent += upload_file(api, job, answer, bucket, file)[1]

    progress = _progress(job, answer)

    def url(path: str) -> str | None:
        entry = progress.files.get(path)
        return entry.public_url if entry else None

    uploaded = Uploaded(
        **progress.model_dump(),
        song_video_url=url(media.video_path("song")), lesson_video_url=url(media.video_path("lesson")),
        song_poster_url=url(media.poster_path("song")), lesson_poster_url=url(media.poster_path("lesson")),
        missing=missing,
    )
    try:
        api.set_video(answer, "READY", song_video_url=uploaded.song_video_url, lesson_video_url=uploaded.lesson_video_url,
                      song_poster_url=uploaded.song_poster_url, lesson_poster_url=uploaded.lesson_poster_url)
    except ApiError as e:
        raise media.StepFailed(f"files uploaded, but marking the videos READY failed: {e}") from e
    jobs.write(job / UPLOADED, uploaded)  # last: done means READY was sent

    kept = len(public) + len(private) - sent
    line = f"uploaded {sent} file(s) for answer {answer}" + (f" ({kept} unchanged)" if kept else "")
    line += f"; videos READY: {uploaded.song_video_url}, {uploaded.lesson_video_url}"
    return line + (f"; not in the job: {', '.join(missing)}" if missing else "")


def _progress(job: Path, answer: str) -> Progress:
    """What earlier runs uploaded for this answer; nothing, if it was for another one."""
    for name in (PROGRESS, UPLOADED):
        try:
            progress = jobs.read(job / name, Progress)  # uploaded.json reads as one too
        except (OSError, ValueError, ValidationError):
            continue
        if progress.answer_id == answer:
            return progress
    return Progress(answer_id=answer)


def upload_file(api: Api, job: Path, answer_id: str, bucket: Bucket, path: str) -> tuple[UploadedFile, int]:
    """Upload one file of the job to <bucket>/<answer_id>/<path>, unless this exact file is
    already there, and record it in upload.progress.json. Returns the record and 1 if it was
    sent, 0 if it was already there. Raises StepFailed when the API or storage refuses it."""
    progress = _progress(job, answer_id)
    data = (job / path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    done = progress.files.get(path)
    if done and done.bucket == bucket and done.sha256 == digest:
        return done, 0
    content_type = CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
    try:
        signed = api.upload_url(answer_id, bucket, path)
        api.put_file(signed["uploadUrl"], data, content_type, PUBLIC_CACHE_S if bucket == PUBLIC else PRIVATE_CACHE_S)
    except ApiError as e:
        raise media.StepFailed(f"{bucket}/{path}: {e}") from e
    except (KeyError, TypeError) as e:
        raise media.StepFailed(f"{bucket}/{path}: the API sent no upload URL") from e
    public_url = signed.get("publicUrl") if bucket == PUBLIC else None
    if bucket == PUBLIC and not public_url:
        raise media.StepFailed(f"{bucket}/{path}: the API sent no public URL")
    entry = UploadedFile(bucket=bucket, sha256=digest, size=len(data), public_url=public_url)
    progress.files[path] = entry
    jobs.write(job / PROGRESS, progress)
    return entry, 1
