# You are the scorecard's judge for one topic

The popsmosis studio's scorecard has a judge model read a "droplet" (a short lesson with a song, a narration and
on-screen key lines) and score it against a written rubric. For this run no call goes to the API: **you are that
judge**. You are given two droplets on the same topic, X and Y, made by two versions of the studio. Judge each on its
own against the rubric, as carefully and as strictly as the rubric asks, then say which you prefer for a learner
meeting the topic for the first time, and why.

Use only what the call gives you: do not open the repository, other topics' folders, or anything else. Your general
knowledge is fine (you need it to check accuracy).

In your folder:
- `judge.system.md`: the rubric, your instructions.
- `judge.user.md`: the two droplets.
- `judge.schema.json`: the schema each of your two judgements must fit (every property present, no others).

Write one JSON object to `judge.answer.json` in the folder: {"X": <your judgement of X>, "Y": <of Y>, "prefer": "X" or
"Y", "why": "<a few sentences>"}. Check it from the repository root:
  python3 tools/insession/score.py judge-check <topic>
If it prints anything but OK, fix only what it lists and check again.

Finish by replying with one line: "<topic>: judged, prefers X|Y".
