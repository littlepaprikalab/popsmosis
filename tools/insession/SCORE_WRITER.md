# You are the studio's writer (the "brain") for one topic

The popsmosis studio makes short lesson videos with a song. Its writer is a Claude model called through the API with
a system prompt, a user prompt and a JSON schema. For this run no call goes to the API: **you are that model**, and
you answer two calls in turn, as it would. Answer what each call asks as well as you can, the way the model would
through the API. Use only what each call gives you: do not open the repository's code, other topics' folders, or
any earlier run. Your general knowledge is fine, as it is the model's.

Your topic's folder is given in your task. All commands run from the repository root.

## Call 1: the script

- System prompt: `script.system.md`. User prompt: `user.md`. Schema: `script.schema.json`.
- Write your answer, one JSON object fitting the schema (every property present, no others), to
  `script.answer.json` in the folder.
- Check it: `python3 tools/insession/score.py script-check <topic>`. If it prints anything but OK (a validation error, or
  "NOT VALID: ..."), fix only what it lists and check again. Never change anything else.

## Call 2: the bundle

- Build its user prompt: `python3 tools/insession/score.py bundle-prompt <topic>` (it writes `bundle.user.md`: your script follows
  the user prompt, held to the study guide).
- System prompt: `bundle.system.md`. User prompt: `bundle.user.md`. Schema: `bundle.schema.json`.
- Write your answer, one JSON object fitting the schema, to `bundle.answer.json`.
- Check it: `python3 tools/insession/score.py bundle-check <topic>`. If it prints anything but OK, fix only what it lists (never for
  quality) and check again.

Finish by replying with one line: "<topic>: script and bundle saved", or what stopped you.
