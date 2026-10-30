"""Part of the fact-check: the songwriter, who rewrites a couplet the fact-check failed, so it stays a song.

The fact-checker writes a correction for every line it fails, but it is a checker: its lyric fixes come out
true and flat, and a chorus fix is sung three times. So the couplets that fail in a round go to one songwriter
call (prompts/songwriter.md), which hears the whole song, its style and tempo, the facts, what the checker found
wrong and the checker's own true version, and writes each couplet again to be true and still sing: as many
lines as it had, each about as long as the line it replaces, rhyming where it rhymed, in the song's voice and
frame.

The fact-check then judges the rewrite like any changed line. Each couplet goes to the songwriter once: if its
rewrite fails too, the checker's correction of it is used, as before there was a songwriter. A rewrite is kept
only where code can stand behind its shape (the same number of lines, none empty, none far longer or shorter
than the line it replaces, at most 12 words a line unless the line it replaces had more) and its names (every
structure a line said, which is what reveals it on screen); elsewhere, and when the call itself fails, the
checker's version is used, and when that fails again, the checker's correction from before the rewrite.

Its call is the fact-check step's, on its bill, labelled "lyric rewrite round <n>".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from studio import brain, checks
from studio.schemas import Bundle, Check, LyricRewrite

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "songwriter.md").read_text(encoding="utf-8")
WORDS_MAX = 12  # a rewritten line's most words, unless the line it replaces had more
SYLLABLES_OFF = 3  # how far a rewritten line may stray from the one it replaces, or 30% of it if more


def rewrite(bundle: Bundle, failing: list[tuple], ask, round_no: int) -> dict[str, str]:
    """failing: (couplet, the checker's verdict on it) for each couplet to rewrite, the couplet a fact-check Line.
    Return each rewrite kept, by the couplet's id, its lines joined by "\\n"."""
    payload = {
        "course": bundle.course, "topic": bundle.topic, "audience": bundle.audience,
        "song_style": ", ".join(bundle.music.positive_styles), "bpm": bundle.music.bpm,
        "core_idea": bundle.core_idea,
        "facts": [{"id": fact.id, "text": fact.text} for fact in bundle.facts],
        "song": [{"id": line.id, "section": line.section, "text": line.text} for line in bundle.lyrics],
        "couplets": [{"id": couplet.id,
                      "lines": [{"id": i, "text": text} for i, text in zip(couplet.members, couplet.text.split("\n"))],
                      "problem": check.problem, "true_version": true_version(check, len(couplet.members))}
                     for couplet, check in failing],
    }
    try:
        answer = ask("brain", json.dumps(payload, ensure_ascii=False, indent=1), LyricRewrite, system=PROMPT,
                     label=f"lyric rewrite round {round_no}")
        written = {c.id: c.lines for c in LyricRewrite.model_validate(answer).couplets}
    except (brain.BrainError, ValidationError) as e:  # the checker's versions stand in
        print(f"factcheck: the songwriter's call failed, so the checker's corrections are used: {e}", file=sys.stderr)
        return {}
    canon = checks.canonical_names([scene.model_dump() for scene in bundle.scenes])
    kept = {}
    for couplet, _ in failing:
        lines = [" ".join(line.split()) for line in written.get(couplet.id, [])]
        if fits(couplet.text.split("\n"), lines, canon) and "\n".join(lines) != couplet.text:
            kept[couplet.id] = "\n".join(lines)
    return kept


def true_version(check: Check, lines: int) -> list[str]:
    """The checker's correction, a line for each line of the couplet; [] when it gave none that fits."""
    for parts in (check.corrected.split("\n"), check.corrected.split(" / ")):
        parts = [" ".join(part.split()) for part in parts]
        if len(parts) == lines and all(parts):
            return parts
    return []


def fits(before: list[str], after: list[str], canon: dict[str, str] | None = None) -> bool:
    """Whether a rewrite keeps the couplet's shape: a line for each line, each near the length of the one it
    replaces, so it is sung in the same bar, and, with the bundle's names (checks.canonical_names), still saying
    every structure the line said, by its name or an alias."""
    if len(after) != len(before) or not all(after):
        return False
    for old, new in zip(before, after):
        if canon and not set(checks.said(old, canon)) <= set(checks.said(new, canon)):
            return False
        if len(new.split()) > max(WORDS_MAX, len(old.split())):
            return False
        if abs(syllables(new) - syllables(old)) > max(SYLLABLES_OFF, round(0.3 * syllables(old))):
            return False
    return True


syllables = checks.syllables  # a rewritten line's syllables, held near the line it replaces
