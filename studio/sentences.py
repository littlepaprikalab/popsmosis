"""Split lesson markdown into sentences the fact-check can judge one by one.

Each sentence keeps its span in the markdown, so a corrected sentence can be
written back in place. Markup that only shapes the page stays out of the
sentences: list markers, heading hashes, quote markers, table pipes and fenced
code. Inline markdown (bold, links) stays in, since it is part of the sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Sentence:
    text: str  # whitespace collapsed to single spaces
    start: int
    end: int


_FENCE = re.compile(r"\s{0,3}(```|~~~)")
_HEADING = re.compile(r"\s{0,3}#{1,6}\s+")
_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d{1,9}[.)])\s+")
_QUOTE = re.compile(r"\s*>\s?")
_RULE = re.compile(r"\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_DIVIDER = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")

# A sentence ends at . ! or ? (and any closing quotes, brackets or emphasis)
# followed by whitespace.
_END = re.compile(r"[.!?]+[\"'’”)\]*_]*(?=\s)")
_WORD_BEFORE_DOT = re.compile(r"[A-Za-z.]+$")
_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "al", "approx", "fig", "no", "dr", "mr", "mrs", "ms", "st", "prof"}


def split_sentences(markdown: str) -> list[Sentence]:
    sentences = []
    for start, end, whole in _blocks(markdown):
        spans = [(start, end)] if whole else _split_block(markdown, start, end)
        for s, e in spans:
            raw = markdown[s:e]
            s += len(raw) - len(raw.lstrip())
            e -= len(raw) - len(raw.rstrip())
            if any(ch.isalnum() for ch in markdown[s:e]):
                sentences.append(Sentence(" ".join(markdown[s:e].split()), s, e))
    return sentences


def replace_sentences(markdown: str, replacements: dict[str, str]) -> str:
    """Rewrite every sentence whose text is a key of `replacements`."""
    for sentence in reversed(split_sentences(markdown)):
        if sentence.text in replacements:
            markdown = markdown[: sentence.start] + replacements[sentence.text] + markdown[sentence.end :]
    return markdown


def _blocks(markdown: str) -> list[tuple[int, int, bool]]:
    """(start, end, whole) for each run of text; `whole` blocks are not split further."""
    blocks: list[tuple[int, int, bool]] = []
    paragraph: list[int] | None = None  # [start, end] of the paragraph or list item being read
    in_fence = False
    pos = 0

    def close():
        nonlocal paragraph
        if paragraph:
            blocks.append((paragraph[0], paragraph[1], False))
        paragraph = None

    for raw_line in markdown.splitlines(keepends=True):
        line_start, pos = pos, pos + len(raw_line)
        line = raw_line.rstrip("\r\n")
        line_end = line_start + len(line)
        if in_fence:
            in_fence = not _FENCE.match(line)
            continue
        if _FENCE.match(line):
            close()
            in_fence = True
        elif not line.strip() or _RULE.match(line):
            close()
        elif m := _HEADING.match(line):
            close()
            blocks.append((line_start + m.end(), line_start + len(line.rstrip().rstrip("#")), True))
        elif line.lstrip().startswith("|"):
            # One table row is one line to check: its cells only make sense together.
            close()
            if not _TABLE_DIVIDER.match(line):
                cells = line.strip(" \t|")
                if cells:
                    offset = line.index(cells)
                    blocks.append((line_start + offset, line_start + offset + len(cells), True))
        elif m := _QUOTE.match(line):
            close()
            blocks.append((line_start + m.end(), line_end, False))
        elif m := _LIST_ITEM.match(line):
            close()
            paragraph = [line_start + m.end(), line_end]
        elif paragraph:
            paragraph[1] = line_end
        else:
            paragraph = [line_start, line_end]
    close()
    return blocks


def _split_block(markdown: str, start: int, end: int) -> list[tuple[int, int]]:
    text = markdown[start:end]
    spans, cut = [], 0
    for m in _END.finditer(text):
        rest = text[m.end() :].lstrip()
        if not rest or rest[0].islower():
            continue
        if m.group().startswith(".") and len(m.group().rstrip("\"'’”)]*_")) == 1:
            word = _WORD_BEFORE_DOT.search(text, 0, m.start())
            if word and _is_abbreviation(word.group()):
                continue
        spans.append((start + cut, start + m.end()))
        cut = m.end()
    spans.append((start + cut, end))
    return spans


def _is_abbreviation(word: str) -> bool:
    # "e.g", "H.M", "U.S": a dot inside; "J": an initial; or a known short form.
    return "." in word or (len(word) == 1 and word.isupper()) or word.lower() in _ABBREVIATIONS
