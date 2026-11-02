# Running the studio with every Claude call answered in the session

Used for the Nov 1 round (docs/test-rounds.md, "In-session round, Nov 1: five topics end to end"). The studio's own
steps run as they always do; only `ask` differs: each Claude call is written to a folder and waits there until a
subagent in the Claude Code session saves an answer. No call reaches the Anthropic API.

    tools/insession/start.sh <job-id> ...          # jobs/<job-id>/, start to finish: a scorecard topic's id, or any
                                                   # topic whose jobs/<job-id>/request.json is written first
    python tools/insession/answer.py groups        # the calls waiting, by topic and kind, for one subagent each
    python tools/insession/answer.py claim <call>  # handed to a subagent
    python tools/insession/answer.py wait          # block until calls have come in and been still for a while
    python tools/insession/answer.py spend         # what each job has spent, from its calls.jsonl
    python tools/insession/restep.py <job> art locate timeline render   # some steps of any job folder again

- `drive.py` runs one topic: the bundle (with its script, early audit, learner test and narration edit), the
  audit and the fact-check, then writes the job's settings.json (`{"song": {"provider": "gemini"}, "art":
  {"claude": "muse"}}`) and makes the videos as `python -m studio make` does. It refuses to start while an
  Anthropic or ElevenLabs key (or a retired setting) is set; `start.sh` unsets them.
- An answer is found by the call's label and a hash of its system prompt, prompt, schema and images, so running a
  step again reuses every answer it was given. Muse Image's pictures and SAM 3.1's masks are cached by request
  under `_insession/paid/`, so a step run again never pays twice; Lyria and Gemini TTS are not cached (a song the
  timing step deletes must be sung again). Before every paid call the round's spend is checked against
  `GUARD_USD`.
- Subagents read `BRIEF.md`, answer from the call's own folder only and save with `answer.py save`, which checks
  the answer against the call's JSON schema and the studio's own model: fix only what it lists.
- `sheet.sh <video> <out.png> [every_s] [cols]` tiles a video's frames, each stamped with its time.
