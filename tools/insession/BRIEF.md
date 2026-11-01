# Answering the studio's Claude calls in the session

The popsmosis studio (studio) makes short science lesson videos. Its pipeline asks Claude
for many things: a lesson's script and plan, fact checks, checks of painted pictures, boxes around parts of a
picture. For this round no call goes to the Anthropic API: each call is written to a folder and **you answer it
instead**, as the model the pipeline would have called. The pipeline is waiting on your answer, and carries on
with it as soon as it is saved.

## One call = one folder

    <call>/system.md      the system prompt: your instructions for this call
    <call>/prompt.md      the user message
    <call>/images/        images shown before the prompt (look at each one with the Read tool)
    <call>/schema.json    the JSON schema the answer must fit (no file: the answer is plain text, e.g. an SVG)
    <call>/request.json   the label, the role and the model it stands for

## How to answer

1. Read `system.md`, `prompt.md` and every image. Treat them exactly as that model would: the system prompt is
   your instructions, the prompt is the user's message. Answer what they ask, as well as you can, the way the
   model would answer it through the API. This wrapper changes nothing about what a good answer is.
2. Use only what the call gives you. Do not open the job's other files, other calls' folders or answers, or the
   studio's code: the real call sees nothing but its own prompts and images, and its independence is the point
   (a checker that saw the writer's notes is no check). Your general knowledge is fine, as it is the model's.
3. Write your answer to a file of your own, /tmp/answers/<call name>.json (or .svg), and read no other file there:
   other calls' answers are kept there too. The answer is
   one JSON object fitting `schema.json`, every property present (the schema is strict: all properties required,
   no others). For a call with no schema, write the plain text answer (for an SVG drawing, the SVG itself).
4. Save it: `python3 tools/insession/answer.py save <call folder> <your file>`. It validates the answer
   against the schema and the studio's own model, and on success saves it and moves the call to done/.
   If it says NOT VALID, fix **only** what it lists, never anything else, and save again.
5. When you answer several calls, answer each one on its own, from its own folder only, as if it were the only
   call you had seen: a separate API call would not remember the others. Save each separately.

Finish with a short report: each call's label and one line on what you answered (e.g. "art check overview:
2 misplaced, 1 wrong label"). Do not paste the whole answer.
