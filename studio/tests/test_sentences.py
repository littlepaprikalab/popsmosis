from studio.sentences import replace_sentences, split_sentences


def texts(markdown):
    return [s.text for s in split_sentences(markdown)]


def test_splits_a_paragraph_into_sentences():
    assert texts("The brainstem keeps you alive. The cerebellum? It coordinates movement!") == [
        "The brainstem keeps you alive.",
        "The cerebellum?",
        "It coordinates movement!",
    ]


def test_abbreviations_initials_and_numbers_do_not_end_a_sentence():
    md = (
        "Some lobes, e.g. the frontal lobe, plan. Patient H.M. Lost new memories in 1953. "
        "Dr. Penfield mapped it. It has about 86.5 billion neurons. Next sentence."
    )
    assert texts(md) == [
        "Some lobes, e.g. the frontal lobe, plan.",
        "Patient H.M. Lost new memories in 1953.",
        "Dr. Penfield mapped it.",
        "It has about 86.5 billion neurons.",
        "Next sentence.",
    ]


def test_markup_stays_out_and_inline_markdown_stays_in():
    md = """# Brain structures #

**Big picture.** Your brain is built in layers.
It grows from the bottom up.

1. The **brainstem** runs breathing.
   It sits on the spinal cord.
2. The cerebellum coordinates.

- The limbic system handles emotion.

> A quote line. With two sentences.

---

```python
x = 1. Not a sentence.
```
"""
    assert texts(md) == [
        "Brain structures",
        "**Big picture.**",
        "Your brain is built in layers.",
        "It grows from the bottom up.",
        "The **brainstem** runs breathing.",
        "It sits on the spinal cord.",
        "The cerebellum coordinates.",
        "The limbic system handles emotion.",
        "A quote line.",
        "With two sentences.",
    ]


def test_a_table_row_is_one_line_and_the_divider_is_skipped():
    md = "| Structure | Job |\n|---|:---:|\n| Hippocampus | Forms new memories. Lost in H.M. |\n"
    assert texts(md) == ["Structure | Job", "Hippocampus | Forms new memories. Lost in H.M."]


def test_spans_point_at_the_sentence_in_the_markdown():
    md = "## Title\n\n- First item. Second\n  sentence.\n\n| a | b |\n"
    for sentence in split_sentences(md):
        assert " ".join(md[sentence.start : sentence.end].split()) == sentence.text
    assert texts(md) == ["Title", "First item.", "Second sentence.", "a | b"]


def test_replacing_a_sentence_leaves_the_rest_untouched():
    md = "## Layers\n\nThe pons relays signals. The medulla\ncontrols breathing.\n\n- Item one.\n"
    out = replace_sentences(md, {"The medulla controls breathing.": "The medulla controls breathing and heart rate."})
    assert out == "## Layers\n\nThe pons relays signals. The medulla controls breathing and heart rate.\n\n- Item one.\n"
    assert replace_sentences(md, {"Item one.": "Item **one**."}).endswith("- Item **one**.\n")


def test_lines_with_no_words_are_skipped():
    assert texts("***\n\n...\n\n- \n\n|---|---|\n") == []
