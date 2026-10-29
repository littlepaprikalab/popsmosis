"""What the teacher writes itself, posted from a file it wrote: a follow-up's answer, or the short answer to a request
that is not a topic.

    python -m studio reply <answer-id> <reply-to-id> --file /tmp/popsmosis-reply.md
    python -m studio answer <question-id> --model <model-id> --file /tmp/popsmosis-answer.md

The text never goes on a command line, so nothing in it (or in the learner's words it may echo) is read by a shell,
and posting needs no jq or curl: only the Python the studio runs on. One line of outcome:

    POSTED <id>             posted
    DONE <reason>           nothing to post: answered already, or deleted (409, 404)
    REFUSED: <reason>       the API refused the text (too long, or it looks like a key): write it again
    FAILED: <reason>        the API did not answer, or refused the key
"""

from __future__ import annotations

from pathlib import Path

from studio.api import Api, ApiError
from studio.run import ID_RE

REPLY_MAX = 2000  # api/src/routes/replies.ts, REPLY_MAX_LENGTH
ANSWER_MAX = 5000  # api/src/routes/questions.ts, ANSWER_MAX_LENGTH
MODEL_MAX = 100

POSTED, DONE, REFUSED, FAILED = "POSTED", "DONE", "REFUSED", "FAILED"
EXIT_CODES = {POSTED: 0, DONE: 0, REFUSED: 1, FAILED: 1}


def _text(file: Path, limit: int) -> tuple[str | None, str]:
    """The file's text, or None and why not."""
    try:
        text = file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read {file}: {e.__class__.__name__}"
    if not text:
        return None, f"{file} is empty"
    if len(text) > limit:
        return None, f"{len(text)} characters; at most {limit}"
    return text, ""


def _outcome(e: ApiError) -> tuple[str, str]:
    if e.status == 409:
        return DONE, e.message or "answered already"
    if e.status == 404:
        return DONE, e.message or "deleted"
    if e.status == 400:
        return REFUSED, e.message
    return FAILED, str(e)


def reply(answer_id: str, reply_to_id: str, file: Path, api: Api | None = None) -> tuple[str, str]:
    if not ID_RE.fullmatch(answer_id) or not ID_RE.fullmatch(reply_to_id):
        return REFUSED, "the answer id and the reply id are ids the API gave"
    text, why = _text(file, REPLY_MAX)
    if text is None:
        return REFUSED, why
    try:
        posted = (api or Api.from_env()).request("POST", f"/api/answers/{answer_id}/replies",
                                                 {"content": text, "replyToId": reply_to_id})
    except ApiError as e:
        return _outcome(e)
    return POSTED, str(posted.get("id", ""))


def answer(question_id: str, model: str, file: Path, api: Api | None = None) -> tuple[str, str]:
    if not ID_RE.fullmatch(question_id):
        return REFUSED, "the question id is an id the API gave"
    if not model or len(model) > MODEL_MAX:
        return REFUSED, f"--model is the model id you run on, at most {MODEL_MAX} characters"
    text, why = _text(file, ANSWER_MAX)
    if text is None:
        return REFUSED, why
    try:
        posted = (api or Api.from_env()).post_answer(question_id, {"content": text, "model": model})
    except ApiError as e:
        return _outcome(e)
    return POSTED, str(posted.get("id", ""))
