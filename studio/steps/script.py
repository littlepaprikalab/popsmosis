"""Part of step 1: the script, written before the rest of the bundle so the lesson is one explanation.

Round 18's lessons were written a sentence at a time alongside everything else the bundle holds, and a learner
watching them got lost: the hook gave facts without a question, the misconception came before what it was about
(Caesar corrected before Caesar), a sentence talked about what no picture drew (consuls over four generals, lava
over a volcano with none), side details broke up the path. Now one brain call (prompts/script.md, with the
writer's brief's rules for the audience and the facts) writes the lesson first: the question it answers, the
essence, the facts, a picture for each scene and the narration, passage by passage, over those scenes. The bundle
call is handed the script (as_prompt) and builds the pictures, the direction, the lesson text and the song around
it, and code holds the bundle to it (follow): the essence and the facts word for word, and the narration sentence
for sentence.

When the call fails, the bundle is written without a script, as before. script.json keeps the script and what the
bundle kept of it. Its call is on the bundle step's bill, labelled "script".
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import Field

from studio.jobs import write
from studio.schemas import Bundle, Fact, Strict

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
RECORD = "script.json"
# Taken from the writer's brief (prompts/bundle.md) as they stand. "No song for this droplet" is in the brief only for
# the lesson video alone (studio/steps/bundle.py, NO_SONG).
BRIEF_SECTIONS = ("The audience", "The facts", "No song for this droplet")


class ScriptScene(Strict):
    id: str = Field(description='"hook", "overview", or a short id for a close-up ("tubule-detail")')
    picture: str = Field(description="What it draws and where, every part the narration names over it")


class Passage(Strict):
    scene: str = Field(description="The id of the scene it plays over")
    lines: list[str] = Field(description="Its sentences, in order, one sentence each")


class Script(Strict):
    audience: str
    question: str
    core_idea: str
    why_chain: list[str]
    key_terms: list[str]
    facts: list[Fact]
    misconception: str
    scenes: list[ScriptScene]
    narration: list[Passage]

    def sentences(self) -> list[tuple[str, str]]:
        """Every narration sentence, in order, with the scene it plays over."""
        return [(p.scene, " ".join(line.split())) for p in self.narration for line in p.lines if line.strip()]


def brief_sections(brief: str, titles=BRIEF_SECTIONS) -> str:
    """The `## ` sections of the writer's brief with these titles, as written there."""
    parts = re.split(r"(?m)^(?=## )", brief)
    return "\n".join(p for p in parts if any(p.startswith(f"## {t}\n") for t in titles))


def narration_rules() -> str:
    """The script prompt's rules for the narration, without its heading, for a bundle written with no script
    (studio/steps/bundle.py): the writer's brief keeps none of its own, since the script's narration is spoken."""
    text = (PROMPTS / "script.md").read_text(encoding="utf-8")
    start, end = text.find("\n## The narration\n"), text.find("\nThe rules for the audience")
    return text[start + len("\n## The narration\n"):end].strip() + "\n" if 0 <= start < end else ""


def system_prompt(brief: str) -> str:
    return (PROMPTS / "script.md").read_text(encoding="utf-8") + "\n" + brief_sections(brief)


def write_script(prompt: str, brief: str, ask, images=(), job: Path | None = None) -> Script | None:
    """The lesson's script for this request (the bundle's user prompt), or None when the call fails."""
    try:
        answer = ask("brain", prompt, Script, images, system=system_prompt(brief), label="script")
        script = Script.model_validate(answer)
    except Exception as e:  # the script helps the bundle; a call that fails never fails the droplet
        print(f"script: none, the bundle is written without one: {type(e).__name__}: {e}", file=sys.stderr)
        if job is not None:
            write(job / RECORD, {"script": None, "why": str(e)})
        return None
    problem = _problem(script)
    if problem:
        print(f"script: none, the bundle is written without one: {problem}", file=sys.stderr)
    if job is not None:  # follow() adds what the bundle kept of it
        write(job / RECORD, {"script": script.model_dump(), **({"why": problem} if problem else {})})
    return None if problem else script


def _problem(script: Script) -> str:
    ids = [s.id for s in script.scenes]
    if not script.sentences():
        return "it has no narration"
    if len(set(ids)) != len(ids):
        return "two of its scenes share an id"
    unknown = sorted({p.scene for p in script.narration} - set(ids))
    return f"its narration plays over scenes it does not draw: {unknown}" if unknown else ""


def as_prompt(script: Script) -> str:
    """The script, for the end of the bundle call's user prompt."""
    data = script.model_dump()
    data["narration"] = [{"scene": scene, "id": f"n{k}", "text": text}
                         for k, (scene, text) in enumerate(script.sentences(), start=1)]
    return ("The lesson's script, written before you: it stands, as the brief's section \"The script\" says.\n"
            f"<script>\n{json.dumps(data, ensure_ascii=False, indent=1)}\n</script>")


def follow(bundle: Bundle, script: Script, job: Path | None = None) -> Bundle:
    """The bundle held to its script: the essence and the facts as the script has them, and its narration said as
    the script says it, where the bundle has a narration item for every sentence. What could not be held is kept as
    the bundle wrote it, and script.json says so."""
    update = {"audience": script.audience, "core_idea": script.core_idea, "why_chain": script.why_chain,
              "key_terms": script.key_terms, "misconception": script.misconception}
    kept = {"essence": True}
    kept["facts"] = [f.id for f in bundle.facts] == [f.id for f in script.facts]
    if kept["facts"]:
        update["facts"] = script.facts
    sentences = script.sentences()
    kept["narration"] = len(bundle.narration) == len(sentences)
    if kept["narration"]:
        update["narration"] = [n.model_copy(update={"text": text}) for n, (_, text) in zip(bundle.narration, sentences)]
        scenes = {d.line_id: d.scene for d in bundle.direction}
        kept["scenes_as_written"] = sum(scenes.get(n.id) == scene for n, (scene, _) in zip(bundle.narration, sentences))
        kept["reworded"] = sum(" ".join(n.text.split()) != text for n, (_, text) in zip(bundle.narration, sentences))
    if job is not None:
        write(job / RECORD, {"script": script.model_dump(), "kept": kept})
    if not all(v for k, v in kept.items() if k in ("facts", "narration")):
        print(f"script: the bundle did not keep all of it: {kept}", file=sys.stderr)
    return bundle.model_copy(update=update)
