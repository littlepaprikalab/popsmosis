"""Step 9: upload (docs/PLAN.md, sections 5 and 10).

Copies what outlives the job folder to Supabase Storage, then marks the videos READY:

    droplets/<answerId>/out/song_h.mp4 ...    public: the two videos and their posters, for anyone to watch
    studio/<answerId>/art/hook.png ...        private: art, masks, locations.json, audio, timings and
                                              timelines, so a renderer fix can re-render without paying
                                              again for the song and the art

For each file the API mints a signed upload URL (POST /api/answers/:id/upload-url) and
the bytes go straight to Supabase, so the Supabase key stays on the API and the agent
key is never sent to Supabase. The answer is the one in posted.json.

upload.progress.json records each file as it lands, with its hash, so a run that
stopped halfway, or a run after a re-render, uploads only what is new. uploaded.json
is written last, after every file, and then the videos are marked READY with their
public URLs; if that report does not get through, uploaded.json goes again so the
step is not done, and running it again only sends the report.

Setting RENDERING when the media steps start is not this step's: `python -m studio
run` does that when it runs steps 3-9.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from studio import brain, jobs, media
from studio.api import Api, ApiError

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
    """What a re-render needs, as (there, missing). Art and masks are whatever scenes the job has."""
    named = ([media.LOCATIONS]
             + [media.audio_path(k) for k in media.TIMED]
             + [media.timing_path(k) for k in media.TIMED]
             + [media.timeline_path(v) for v in media.VIDEOS])
    there = [p for p in named if (job / p).is_file()]
    missing = [p for p in named if p not in there]
    globbed = sorted(p.relative_to(job).as_posix() for pattern in ("art/*.png", "masks/*/*.png") for p in job.glob(pattern))
    if not any(p.startswith("art/") for p in globbed):
        missing.insert(0, "art/*.png")
    return globbed + there, missing


def run(job: Path, ask=brain.ask, api: Api | None = None) -> str:
    del ask  # no model call in this step
    answer = answer_id(job)
    public = public_files(job)
    private, missing = private_files(job)
    api = api or Api.from_env()

    progress = _progress(job, answer)
    sent = 0
    for bucket, files in ((PRIVATE, private), (PUBLIC, public)):
        for file in files:
            sent += _upload(job, api, progress, bucket, file)

    def url(path: str) -> str | None:
        entry = progress.files.get(path)
        return entry.public_url if entry else None

    uploaded = Uploaded(
        **progress.model_dump(),
        song_video_url=url(media.video_path("song")), lesson_video_url=url(media.video_path("lesson")),
        song_poster_url=url(media.poster_path("song")), lesson_poster_url=url(media.poster_path("lesson")),
        missing=missing,
    )
    jobs.write(job / UPLOADED, uploaded)
    try:
        api.set_video(answer, "READY", song_video_url=uploaded.song_video_url, lesson_video_url=uploaded.lesson_video_url,
                      song_poster_url=uploaded.song_poster_url, lesson_poster_url=uploaded.lesson_poster_url)
    except ApiError as e:
        (job / UPLOADED).unlink(missing_ok=True)  # not done until the page knows: a retry only sends this
        raise media.StepFailed(f"files uploaded, but marking the videos READY failed: {e}") from e

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


def _upload(job: Path, api: Api, progress: Progress, bucket: Bucket, file: str) -> int:
    """Upload one file unless this exact file is already there; 1 if it was sent."""
    data = (job / file).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    done = progress.files.get(file)
    if done and done.bucket == bucket and done.sha256 == digest:
        return 0
    content_type = CONTENT_TYPES.get(Path(file).suffix.lower(), "application/octet-stream")
    try:
        signed = api.upload_url(progress.answer_id, bucket, file)
        api.put_file(signed["uploadUrl"], data, content_type, PUBLIC_CACHE_S if bucket == PUBLIC else PRIVATE_CACHE_S)
    except ApiError as e:
        raise media.StepFailed(f"{bucket}/{file}: {e}") from e
    except (KeyError, TypeError) as e:
        raise media.StepFailed(f"{bucket}/{file}: the API sent no upload URL") from e
    public_url = signed.get("publicUrl") if bucket == PUBLIC else None
    if bucket == PUBLIC and not public_url:
        raise media.StepFailed(f"{bucket}/{file}: the API sent no public URL")
    progress.files[file] = UploadedFile(bucket=bucket, sha256=digest, size=len(data), public_url=public_url)
    jobs.write(job / PROGRESS, progress)
    return 1
