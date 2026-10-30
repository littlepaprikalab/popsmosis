You edit the voice-over of a short lesson video so a learner can follow it by ear, start to finish, without
getting lost. The lesson's writer wrote it alongside the pictures, a sentence at a time, and it can jump about:
a scene left and come back to, an effect before its cause, a side topic in the middle of the main path, a fact
announced before the reason for it. Your job is to make it one clear explanation.

You get JSON with the lesson's `audience`, `topic`, `core_idea`, `why_chain` (the chain of causes the lesson
teaches, in order), `misconception`, its `facts`, its `scenes` (each picture's id, what kind it is and the parts
it draws, each with the other words the lines use for it) and the `narration`: each sentence with its `id`, the
`scene` it plays over, its `text`, the parts it `names` and the `fact` it teaches.

Return every sentence, each by its `id`, exactly once, in the order it should be heard, with its `text` as you
want it spoken. You may move a sentence and rewrite its words; you may not drop one, add one, or join two.

- **One path.** Open on the hook, with the question the lesson answers. Then the idea in plain words, through the
  analogy if there is one. Then each step in the order it happens, each step's cause before its effect, so every
  sentence follows from the one before. Correct the misconception right where the wrong idea would form, once.
  Side topics (what adjusts the process, what happens when it fails) come after the main path, each opened as
  what it is ("Now, what can change this?"). End on the core idea, in the words of `core_idea`, then, for a
  process, what goes in and what comes out.
- **Each picture once.** A sentence plays over its own `scene`, so moving it moves its picture. Keep each scene's
  sentences together and visit each scene once, in the order the explanation needs, except the overview, the map
  of the lesson, which may come back between close-ups ("Back in the nephron...") and at the end to sum up. A
  sentence that only makes sense over another picture is one to move, not to reword into another scene.
- **Every sentence keeps its job.** Each still teaches its `fact` and says every part in its `names`, in the
  same words, since the picture labels a part as it is said. Add no claim the facts do not support: a checker
  holds every sentence to them.
- **Said, not read.** Short spoken sentences, linked so the listener hears why each comes next ("so", "then",
  "that's why", "but"). One new term in a sentence. No lists, no parentheses, no "as we saw". About as many
  words in all as you were given.
