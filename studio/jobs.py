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

M = TypeVar("M", bound=BaseModel)
ROOT = Path(os.environ.get("STUDIO_JOBS_DIR") or Path(__file__).resolve().parent.parent / "jobs")


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
