"""A small bundle that passes validation, for tests to copy and break."""

from __future__ import annotations

import copy

FACTS = [
    {"id": "f1", "text": "The light reactions happen in the thylakoid membrane.", "source_quote": ""},
    {
        "id": "f2",
        "text": "Only cytochrome b6f pumps protons into the thylakoid lumen; "
        "the other electron carriers pass electrons on without pumping.",
        "source_quote": "",
    },
    {"id": "f3", "text": "ATP synthase uses the flow of protons back to the stroma to make ATP.", "source_quote": ""},
]

CHORUS = "Protons flow and ATP grows"

LYRICS = [  # id, section, text, names, fact
    ("v1-1", "verse", "Light lands on the thylakoid", ["thylakoid"], "f1"),
    ("v1-2", "verse", "Every electron hand-off pumps a proton", [], "f2"),
    ("c1-1", "chorus", CHORUS, [], "f3"),
    ("v2-1", "verse", "Only b6f pumps them in", [], "f2"),
    ("c2-1", "chorus", CHORUS, [], "f3"),
    ("b-1", "bridge", "Feel the gradient build", [], "filler"),
    ("c3-1", "chorus", CHORUS, [], "f3"),
]

NARRATION = [  # id, text, names, fact
    ("n1", "The light reactions happen in the thylakoid membrane.", ["thylakoid"], "f1"),
    ("n2", "ATP synthase uses the returning protons to make ATP.", ["ATP synthase"], "f3"),
]

LESSON = """## The light reactions

The light reactions happen in the thylakoid membrane. Only cytochrome b6f pumps protons.

- ATP synthase makes ATP from the proton flow.

What pumps protons? Ask a follow-up if you're unsure.
"""


def _structure(name: str, color: str) -> dict:
    return {"name": name, "aliases": [], "sam_phrase": f"the {name.lower()}", "color_hex": color}


def bundle_data() -> dict:
    line_ids = [i for i, *_ in LYRICS] + [i for i, *_ in NARRATION]
    scenes = {"n2": "synthase"}  # every other line plays over the overview
    return copy.deepcopy({
        "course": "AP Biology",
        "topic": "the light reactions",
        "audience": "high school",
        "core_idea": "Light energy pumps protons, and their flow back makes ATP.",
        "facts": FACTS,
        "misconception": "Not every carrier pumps protons; only cytochrome b6f does.",
        "scenes": [
            {"id": "hook", "image_prompt": "A chloroplast.", "structures": [_structure("Thylakoid", "#2e8b57")]},
            {
                "id": "overview",
                "image_prompt": "A thylakoid membrane in cross-section.",
                "structures": [_structure("Thylakoid", "#2E8B57"), _structure("Cytochrome b6f", "#E07A2F")],
            },
            {"id": "synthase", "image_prompt": "ATP synthase.", "structures": [_structure("ATP synthase", "#3366CC")]},
        ],
        "lesson_markdown": LESSON,
        "music": {
            "positive_styles": ["upbeat pop", "112 bpm"],
            "negative_styles": ["mumbled vocals"],
            "sections": ["verse", "chorus", "verse", "chorus", "bridge", "chorus"],
        },
        "lyrics": [{"id": i, "section": s, "text": t, "names": n, "fact_id": f} for i, s, t, n, f in LYRICS],
        "narration": [{"id": i, "text": t, "names": n, "fact_id": f} for i, t, n, f in NARRATION],
        "direction": [
            {"line_id": i, "scene": scenes.get(i, "overview"), "camera": {"target": "", "zoom": 1.0},
             "spotlight": "", "particles": [], "carries": "", "caption": ""}
            for i in line_ids
        ],
        "tags": {
            "song": ["#photosynthesis", "#lightreactions", "#apbio", "#studysong", "#pop"],
            "lesson": ["#photosynthesis", "#lightreactions", "#apbio", "#explained"],
        },
    })
