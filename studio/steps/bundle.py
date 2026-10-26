"""Step 1: the bundle. One brain call writes everything the topic alone can give.

Writes bundle.json, after the code checks (studio/checks.py) have repaired what they
can, and checks.json. A new bundle makes any earlier fact-check stale, so this step
removes it.
"""

from __future__ import annotations

from pathlib import Path

from studio import brain, checks
from studio.jobs import read, write
from studio.schemas import Bundle, Request

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "bundle.md").read_text(encoding="utf-8")


def run(job: Path, ask=brain.ask) -> Bundle:
    request = read(job / "request.json", Request)
    try:
        answer = ask("brain", user_prompt(request), Bundle, request.images, system=PROMPT, label="bundle")
    except brain.InvalidOutput as e:
        (job / "bundle.invalid.json").write_text(e.raw, encoding="utf-8")
        raise
    bundle, report = checks.review(Bundle.model_validate(answer))
    for stale in ("factcheck.json", "bundle.draft.json", "bundle.invalid.json"):
        (job / stale).unlink(missing_ok=True)
    write(job / "bundle.json", bundle)
    write(job / "checks.json", report)
    return bundle


def user_prompt(request: Request) -> str:
    parts = [f"Topic: {request.topic}"]
    if request.course:
        parts.append(f"Course: {request.course}")
    if request.request:
        parts.append(f"The learner's request, in their words: {request.request}")
    if request.images:
        parts.append(f"The learner sent {len(request.images)} photo(s) of their notes, shown above.")
    if request.study_guide:
        parts.append(f"Study guide:\n<study_guide>\n{request.study_guide}\n</study_guide>")
    return "\n\n".join(parts)
