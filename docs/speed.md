# Making a droplet faster: what we measured and decided

A droplet takes about half an hour from request to videos. This is where the time goes, what was tried, and what
was decided, so a later change starts from the numbers rather than again from guesses. Measured Oct 31.

## Where the time goes

| Stage | Typical time | Note |
|---|---|---|
| Script call | ~2 min | Opus, high effort: about 12k output tokens with its thinking |
| **Bundle call** | **~10 min** | about 67k output tokens with its thinking: half the whole pipeline |
| Fact-check | ~3–6 min | one call over ~130 lines, up to 3 rounds |
| Audio, then timing | ~4 min | Demucs ~1.5 min, song alignment ~1 min, narration alignment ~40 s (MMS over a Gemini narration) |
| Art, then locate | ~3–5 min | beside audio and timing, so rarely the wait |
| Render | ~3–5 min | the three videos on one set of workers |

Media times run far longer when two jobs share a machine: the timing step's `cpu_turn` notes Demucs and the
aligner each take every core, and two at once took 8 times as long each.

## Decided against, for now

- **The bundle as parallel calls** (lesson and narration beside the song, both from the script): about 5 min
  saved, the biggest gain, but a risk to how the parts hold together. Not now (the owner, Oct 31).
- **Starting the media steps while the fact-check runs** (keeping what it did not change): 3–5 min saved, but
  pictures and songs made from lines the fact-check then changes are paid for and thrown away. Not now (the
  owner, Oct 31): the cost adds up.
- **The fact-check as parallel calls, one per kind** (lyrics, narration, lesson, public text): about 2 min saved
  in its first round, for about $0.10 more a droplet (5% of the text pipeline's ~$2): each call sends the
  checker's instructions (~3,800 tokens) and the droplet's facts again, and calls fired at once cannot share the
  prompt cache. Not worth it now (the owner, Oct 31); revisit if the fact-check's wait starts to matter more than
  its cost. Writing the cache with one call before fanning out would cut the extra.
- **Trimming the fact-check's instructions**: they are about 3,800 tokens and already cached between rounds
  (`brain.py` marks the system prompt `cache_control`), so a third shorter saves under a cent a droplet. Its cost
  is its output and thinking. Most of its rules each answer a failure a test round found (a fact about one case
  supporting a line about another, attitude read as a claim), so the saving is not worth the risk.
- **Lowering the brain's effort** (high to medium): the fastest win if quality holds, but the scorecard runs on
  subagents in the session, which cannot test an API effort setting. Not without a way to measure it.

## Tried

- **The upright hook renders slowly**: it does not. Across whole videos it costs 27 ms a frame against 49 to
  71 ms for the song and the lesson. Its late finish in the render step's summary is the scheduler: the longest
  video's frames go first, and each video's time counts from the start of the step.
- **The aligner loaded once for the step** (`timing._mms`): a Gemini narration and the song each loaded MMS
  (about 9 s once its file is cached). Shipped.
- **Hearing the song's vocals once, every window and every re-checked line read from that one hearing**: a third
  faster (35 s against 52 s on supply and demand), but it re-timed none of the 20 lines that hearing them alone
  had re-timed better: unsure lines went from 22 to 27, and 60 words moved over a tenth of a second. A line heard
  alone sounds different to the model, which is the re-check's whole point. Rejected.

- **The fact audit beside the bundle call** (`audit.begin` and `audit.settle`): the audit needs only the facts,
  which the script writes before the bundle call, and the bundle keeps them (`script.follow`). The bundle step
  begins it on the script's facts and keeps it when the bundle did; its step then finds itself done. The same
  calls at no extra cost, its 1–2 min hidden in the bundle call's 10. Shipped.

- **The aligner at 8 bits** (dynamic quantization of MMS's linear layers): on three Gemini narrations it timed
  every word as the full model did (none moved over 40 ms) and aligned in 26–28 s against 34–35 s, but loading
  and quantizing take 7 s against 3, so the step gains about 5 s. On their three songs it moved 19 to 46 words
  over a tenth of a second and left more lines unsure in two. Not shipped: 5 s is not worth a second model in
  memory. (The narration's ~2 min measured before was other jobs on the same machine, not the aligner.)

## Not yet tried

- **A GPU for the studio worker**: Demucs and the aligner in seconds rather than minutes. A cost and hosting
  decision, not a code change.
