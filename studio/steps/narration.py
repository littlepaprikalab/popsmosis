"""Part of step 1: the narration editor, who makes the voice-over one explanation.

The brain writes the narration a sentence at a time alongside everything else the bundle holds (the scenes, the
song, a direction for every line), and round 18's came out in pieces: the kidney lesson went from the overview to
its close-up and back three times, said a urine test finds diabetes before saying why glucose gets into urine, and
broke off the filtrate's path for a hormone. One more brain call (prompts/narration.md) hears the whole voice-over
with the facts and the scenes and returns it as one path: every sentence, by its id, in the order it should be
heard, reworded where it needs to be. Each sentence keeps its scene, its fact and the parts it names, so the
pictures, the reveals and the fact-check line up as before; the fact-check holds the rewording to the facts.

Kept only where code can stand behind it: every sentence back exactly once, none empty, about as many words in all.
Otherwise, and when the call fails, the narration stands as the writer wrote it. narration.json keeps both. Its
call is on the bundle step's bill, labelled "narration edit"; `python -m studio step narration <job>` runs it on a
job's bundle by itself, and the fact-check must then run again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from studio import brain, checks
from studio.jobs import write
from studio.schemas import Bundle, Strict

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "narration.md").read_text(encoding="utf-8")
RECORD = "narration.json"
WORDS_OFF = 0.25  # the edited narration may run this share more or fewer words than the writer's


class EditedSentence(Strict):
    id: str
    text: str


class NarrationEdit(Strict):
    """What the editor returns: every sentence of the narration, by id, in the order it should be heard."""

    lines: list[EditedSentence]


def payload(bundle: Bundle) -> dict:
    scenes = {d.line_id: d.scene for d in bundle.direction}
    return {
        "audience": bundle.audience, "topic": bundle.topic, "core_idea": bundle.core_idea,
        "why_chain": bundle.why_chain, "misconception": bundle.misconception,
        "facts": [{"id": f.id, "text": f.text} for f in bundle.facts],
        "scenes": [{"id": s.id, "parts": [{"name": x.name, "also": x.aliases} for x in s.structures]}
                   for s in bundle.scenes],
        "narration": [{"id": n.id, "scene": scenes.get(n.id, ""), "text": n.text, "names": n.names,
                       "fact": n.fact_id} for n in bundle.narration],
    }


def problem(bundle: Bundle, edited: list[EditedSentence]) -> str:
    """Why the edit cannot be kept, or ""."""
    ids = [n.id for n in bundle.narration]
    back = [e.id for e in edited]
    if sorted(back) != sorted(ids):
        lost, extra = sorted(set(ids) - set(back)), sorted(set(back) - set(ids))
        return (f"it did not return every sentence exactly once (left out {lost or 'none'}, unknown or twice "
                f"{extra or [i for i in set(back) if back.count(i) > 1] or 'none'})")
    if any(not e.text.strip() for e in edited):
        return "it left a sentence empty"
    before = sum(len(n.text.split()) for n in bundle.narration)
    after = sum(len(e.text.split()) for e in edited)
    if abs(after - before) > WORDS_OFF * before:
        return f"it ran {after} words for the writer's {before}"
    return ""


def edit(bundle: Bundle, ask, job: Path | None = None) -> Bundle:
    """The bundle with its narration edited into one path, or the bundle itself when the edit is not kept."""
    try:
        answer = ask("brain", json.dumps(payload(bundle), ensure_ascii=False, indent=1), NarrationEdit,
                     system=PROMPT, label="narration edit")
        edited = NarrationEdit.model_validate(answer).lines
    except Exception as e:  # an edit that fails leaves the narration as written, never fails the droplet
        return _kept(job, bundle, f"the call failed: {type(e).__name__}: {e}")
    why = problem(bundle, edited)
    if why:
        return _kept(job, bundle, why)
    written = {n.id: n for n in bundle.narration}
    narration = [written[e.id].model_copy(update={"text": " ".join(e.text.split())}) for e in edited]
    order = {n.id: k for k, n in enumerate(narration)}
    # A narration sentence's direction follows it: the lyrics' first, then the narration's in its new order.
    direction = ([d for d in bundle.direction if d.line_id not in order]
                 + sorted((d for d in bundle.direction if d.line_id in order), key=lambda d: order[d.line_id]))
    out = bundle.model_copy(update={"narration": narration, "direction": direction})
    if job is not None:
        write(job / RECORD, {"kept": True, "before": _lines(bundle), "after": _lines(out)})
    return out


def _kept(job: Path | None, bundle: Bundle, why: str) -> Bundle:
    print(f"narration: kept as written: {why}", file=sys.stderr)
    if job is not None:
        write(job / RECORD, {"kept": False, "why": why, "before": _lines(bundle)})
    return bundle


def _lines(bundle: Bundle) -> list[dict]:
    return [{"id": n.id, "text": n.text} for n in bundle.narration]


def run(job: Path, ask=brain.ask) -> str:
    """`python -m studio step narration <job>`: edit the job's bundle's narration by itself. The fact-check's
    verdicts no longer match the sentences, so factcheck.json is removed."""
    bundle = checks.load(job / "bundle.json")
    edited = edit(bundle, ask, job)
    if edited is bundle:
        return "narration kept as written"
    edited, report = checks.review(edited)
    write(job / "bundle.json", edited)
    write(job / "checks.json", report)
    (job / "factcheck.json").unlink(missing_ok=True)
    moved = sum(a.id != b.id for a, b in zip(bundle.narration, edited.narration))
    reworded = sum(n.text != {m.id: m.text for m in bundle.narration}[n.id] for n in edited.narration)
    return f"narration edited: {moved} sentences in new places, {reworded} reworded"
