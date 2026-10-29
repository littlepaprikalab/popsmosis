from studio import post
from studio.api import ApiError


class FakeApi:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def request(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        return {"id": "r9"}

    def post_answer(self, question_id, body):
        self.calls.append(("POST", f"/api/questions/{question_id}/answer", body))
        if self.error:
            raise self.error
        return {"id": "a9"}


def test_reply_posts_the_file_as_written_and_nothing_reaches_a_shell(tmp_path):
    text = 'Protons flow "down" $(their) `gradient`; it\'s the rotor that turns.\n'
    file = tmp_path / "reply.md"
    file.write_text(text)
    api = FakeApi()
    assert post.reply("ans1", "rep1", file, api=api) == ("POSTED", "r9")
    assert api.calls == [("POST", "/api/answers/ans1/replies", {"content": text.strip(), "replyToId": "rep1"})]


def test_answer_posts_with_the_model(tmp_path):
    file = tmp_path / "answer.md"
    file.write_text("I can only help with what you are learning. Try: the water cycle.")
    api = FakeApi()
    assert post.answer("q1", "claude-opus-5-5", file, api=api) == ("POSTED", "a9")
    assert api.calls[0][2] == {"content": "I can only help with what you are learning. Try: the water cycle.",
                               "model": "claude-opus-5-5"}


def test_refuses_before_sending_what_the_api_would_refuse(tmp_path):
    long = tmp_path / "long.md"
    long.write_text("x" * (post.REPLY_MAX + 1))
    empty = tmp_path / "empty.md"
    empty.write_text("   \n")
    api = FakeApi()
    assert post.reply("ans1", "rep1", long, api=api)[0] == "REFUSED"
    assert post.reply("ans1", "rep1", empty, api=api)[0] == "REFUSED"
    assert post.reply("ans1", "rep1", tmp_path / "missing.md", api=api)[0] == "REFUSED"
    assert post.reply("../x", "rep1", empty, api=api)[0] == "REFUSED"
    assert post.answer("q1", "", empty, api=api)[0] == "REFUSED"
    assert api.calls == []


def test_says_what_the_api_answered(tmp_path):
    file = tmp_path / "reply.md"
    file.write_text("An answer.")
    cases = [
        (ApiError(409, "CONFLICT", "You have already replied to that message"), "DONE"),
        (ApiError(404, "NOT_FOUND", "replyToId does not belong to this answer"), "DONE"),
        (ApiError(400, "VALIDATION_ERROR", "content looks like it holds a key or a secret, so it was not posted"), "REFUSED"),
        (ApiError(0, "UNREACHABLE", "the API did not answer"), "FAILED"),
        (ApiError(401, "UNAUTHORIZED", "Invalid API key"), "FAILED"),
    ]
    for error, result in cases:
        assert post.reply("ans1", "rep1", file, api=FakeApi(error))[0] == result, error
    assert post.EXIT_CODES["DONE"] == 0 and post.EXIT_CODES["REFUSED"] == 1
