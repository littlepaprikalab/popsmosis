"""jobs/<id>/: one folder per topic, holding every file the steps write."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from studio import knobs

M = TypeVar("M", bound=BaseModel)
ROOT = Path(os.environ.get("STUDIO_JOBS_DIR") or Path(__file__).resolve().parent.parent / "jobs")
# The studio's settings as the API's claim said ({"brain": ..., "song": ..., "art": ..., "knobs": ...};
# api/src/lib/studio-settings.ts): who paints the art and who sings, and the knobs, as an admin set them on /admin/studio.
SETTINGS = "settings.json"


def new_id(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "topic"
    return f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


def write(path: Path, data: BaseModel | dict | list) -> None:
    """Write the whole file or none of it: a step is done when its file exists, so a run
    stopped mid-write must not leave half a file that reads as a finished step."""
    if isinstance(data, BaseModel):
        text = data.model_dump_json(indent=2)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    # Its own partial file per writer: two threads (the art and audio branches renewing the
    # claim) may write the same file at once, and each must replace it whole.
    partial = path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.partial")
    partial.write_text(text + "\n", encoding="utf-8")
    os.replace(partial, path)


def read(path: Path, model: type[M]) -> M:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def studio_settings(job: Path) -> dict:
    """The studio's settings the job was last claimed with (SETTINGS); {} for none, and the defaults apply."""
    try:
        data = json.loads((job / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def keep_studio_settings(job: Path, studio) -> None:
    """Save the settings a claim carried, for the steps after it, and put their knobs in force (studio/knobs.py).
    A claim without them (an older API) keeps what is there."""
    knobs.use(studio)
    if isinstance(studio, dict) and studio != studio_settings(job):
        write(job / SETTINGS, studio)
