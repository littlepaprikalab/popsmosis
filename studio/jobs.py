"""jobs/<id>/: one folder per topic, holding every file the steps write."""

from __future__ import annotations

import json
import os
import re
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
    if isinstance(data, BaseModel):
        text = data.model_dump_json(indent=2)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def read(path: Path, model: type[M]) -> M:
    return model.model_validate_json(path.read_text(encoding="utf-8"))
