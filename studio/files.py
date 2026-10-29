"""The files a learner sent with a follow-up, fetched for the teacher by attachment id.

    python -m studio fetch <attachment-id> [<attachment-id> ...]

The skill file has the teacher read a follow-up's files before it answers. It used to do
that with a curl command that named the saved file after the learner's file name, so a
name such as `$(...).png` became part of a shell command. This command takes only ids,
which the API makes, checks each against the id pattern, and names every saved file after
its id: nothing the learner wrote ever reaches a command line.

One line per id:

    FILE <id> <type> <path>   saved; <type> is image/png, image/jpeg, image/gif, image/webp,
                              text/plain, or application/octet-stream for anything else
    EXPIRED <id>              past the time popsmosis keeps files (410)
    GONE <id>                 deleted, or not a file this key may read (404)
    FAILED <id>: <reason>     the API refused or did not answer
    INVALID                   an argument that is not an attachment id (not repeated back)
"""

from __future__ import annotations

import os
from pathlib import Path

from studio.api import Api, ApiError
from studio.run import ID_RE

FILES_DIR = Path(os.environ.get("POPSMOSIS_FILES_DIR", "/tmp/popsmosis-files"))
# Each type as the API serves it (api/src/lib/attachments.ts sniffs the bytes the same way), with the extension
# an image viewer recognises.
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
              "text/plain": ".txt", "application/octet-stream": ".bin"}


def sniff(data: bytes) -> str:
    """What the bytes are, read from the bytes alone."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def fetch(ids: list[str], api: Api | None = None, dest: Path | None = None) -> tuple[list[str], bool]:
    """Fetch each attachment by id into dest. Returns the outcome lines, and whether every fetch got an answer."""
    dest = dest or FILES_DIR
    lines: list[str] = []
    ok = True
    for raw in ids:
        if not ID_RE.fullmatch(raw):
            lines.append("INVALID")
            ok = False
            continue
        try:
            api = api or Api.from_env()
            data = api.download(f"/api/attachments/{raw}")
        except ApiError as e:
            if e.status == 410:
                lines.append(f"EXPIRED {raw}")
            elif e.status == 404:
                lines.append(f"GONE {raw}")
            else:
                lines.append(f"FAILED {raw}: {e.status or e.code}")
                ok = False
            continue
        kind = sniff(data)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{raw}{EXTENSIONS[kind]}"
        path.write_bytes(data)
        lines.append(f"FILE {raw} {kind} {path}")
    return lines, ok
