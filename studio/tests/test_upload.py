import io
import json
import urllib.request

import pytest

from studio import api as api_module
from studio import jobs, media
from studio.__main__ import main
from studio.api import Api
from studio.steps import upload
from studio.tests import sample_media

API = "https://api.example"
STORAGE = "https://x.supabase.co/storage/v1"
KEY = "sk_pop_" + "0" * 64


class FakeServer:
    """The popsmosis API and Supabase Storage behind one `send`, as studio/api.py calls them."""

    def __init__(self, job):
        self.job = job
        self.calls = []  # (method, url, headers, body)
        self.stored = {}  # "bucket/answer/file" -> (bytes, content type)
        self.fail_put = set()  # files whose PUT storage refuses
        self.flaky_puts = 0  # this many PUTs answer 503 first, then storage recovers
        self.patch_status = 200
        self.upload_url_status = 200
        self.uploaded_at_patch = None

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith(API + "/api/answers/") and url.endswith("/upload-url"):
            assert headers["Authorization"] == f"Bearer {KEY}"
            if self.upload_url_status != 200:
                return self.upload_url_status, json.dumps({"error": {"code": "UNAVAILABLE", "message": "File storage is not configured"}}).encode()
            answer = url.split("/")[-2]
            req = json.loads(body)
            path = f"{req['bucket']}/{answer}/{req['file']}"
            public = f"{STORAGE}/object/public/{path}" if req["bucket"] == "droplets" else None
            return 200, json.dumps({"uploadUrl": f"{STORAGE}/object/upload/sign/{path}?token=t0k", "path": path, "publicUrl": public}).encode()
        if url.startswith(STORAGE + "/object/upload/sign/"):
            path = url.split("/object/upload/sign/")[1].split("?")[0]
            if path.split("/", 2)[2] in self.fail_put:
                return 500, b'{"statusCode":"500","error":"Internal","message":"try later"}'
            if self.flaky_puts:
                self.flaky_puts -= 1
                return 503, b'{"message":"overloaded"}'
            self.stored[path] = (body, headers["Content-Type"])
            return 200, json.dumps({"Key": path}).encode()
        if method == "PATCH" and url.endswith("/video"):
            self.uploaded_at_patch = (self.job / upload.UPLOADED).exists()
            if self.patch_status == 409:
                return 409, b'{"error":{"code":"CONFLICT","message":"Another run has taken over these videos"}}'
            if self.patch_status != 200:
                return self.patch_status, b'{"error":{"code":"SERVER_ERROR","message":"Internal server error"}}'
            return 200, json.dumps({"status": json.loads(body)["status"]}).encode()
        raise AssertionError(f"unexpected {method} {url}")

    def puts(self):
        return [c for c in self.calls if c[0] == "PUT"]

    def patches(self):
        return [json.loads(c[3]) for c in self.calls if c[0] == "PATCH"]


@pytest.fixture
def job(tmp_path):
    """The sample job with its art, stand-in audio and placeholder videos, and a posted lesson."""
    job = sample_media.write_job(tmp_path / "sample", art=True)
    for kind in media.TIMED:
        (job / media.audio_path(kind)).parent.mkdir(exist_ok=True)
        (job / media.audio_path(kind)).write_bytes(f"ID3 {kind}".encode())
    (job / "out").mkdir()
    for video in media.VIDEOS:
        (job / media.video_path(video)).write_bytes(f"mp4 {video}".encode())
        (job / media.poster_path(video)).write_bytes(f"jpg {video}".encode())
    jobs.write(job / "posted.json", {"id": "ans1", "questionId": "q1"})
    return job


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(upload, "RETRY_DELAYS_S", (0, 0))


@pytest.fixture
def server(job):
    return FakeServer(job)


def api_for(server):
    return Api(API, KEY, send=server)


def test_uploads_the_videos_public_and_the_rest_private_then_marks_them_ready(job, server):
    assert not upload.is_done(job)
    line = upload.run(job, api=api_for(server))
    assert upload.is_done(job)

    public = {p for p in server.stored if p.startswith("droplets/")}
    assert public == {f"droplets/ans1/out/{v}_h.{ext}" for v in media.VIDEOS for ext in ("mp4", "jpg")}
    private = {p.removeprefix("studio/ans1/") for p in server.stored if p.startswith("studio/")}
    assert {"locations.json", "audio/song.mp3", "audio/narration.mp3", "audio/frq.mp3", "timing/song_words.json",
            "timing/narration_words.json", "timing/frq_words.json", "timeline_song.json", "timeline_lesson.json",
            "art/hook.png", "art/overview.png", "art/synthase.png",
            media.mask_path("overview", "Cytochrome b6f")} <= private
    assert not any(p.startswith(("out/", "posted", "bundle", "request", "upload")) for p in private)
    assert server.stored["droplets/ans1/out/song_h.mp4"] == (b"mp4 song", "video/mp4")
    assert server.stored["droplets/ans1/out/lesson_h.jpg"][1] == "image/jpeg"
    assert server.stored["studio/ans1/audio/frq.mp3"][1] == "audio/mpeg"

    # The bytes go to Supabase on the signed URL alone: never the agent key.
    for _, url, headers, _ in server.puts():
        assert "token=t0k" in url and "Authorization" not in headers
        assert headers["x-upsert"] == "true"
    assert {h["Cache-Control"] for _, u, h, _ in server.puts() if "/droplets/" in u} == {"max-age=300"}

    # The report goes before uploaded.json, so done means READY was sent; it carries every public URL.
    assert server.uploaded_at_patch is False
    assert server.patches() == [{
        "status": "READY",
        "songVideoUrl": f"{STORAGE}/object/public/droplets/ans1/out/song_h.mp4",
        "lessonVideoUrl": f"{STORAGE}/object/public/droplets/ans1/out/lesson_h.mp4",
        "songPosterUrl": f"{STORAGE}/object/public/droplets/ans1/out/song_h.jpg",
        "lessonPosterUrl": f"{STORAGE}/object/public/droplets/ans1/out/lesson_h.jpg",
    }]
    record = jobs.read(job / upload.UPLOADED, upload.Uploaded)
    assert record.answer_id == "ans1" and record.missing == []
    assert record.files["audio/song.mp3"].bucket == "studio" and record.files["audio/song.mp3"].public_url is None
    assert line.startswith(f"uploaded {len(server.stored)} file(s) for answer ans1; videos READY: ")


def test_running_again_sends_only_what_changed(job, server):
    upload.run(job, api=api_for(server))
    server.calls.clear()
    (job / media.video_path("song")).write_bytes(b"mp4 song, re-rendered")
    line = upload.run(job, api=api_for(server))
    assert [c[1].split("/sign/")[1].split("?")[0] for c in server.puts()] == ["droplets/ans1/out/song_h.mp4"]
    assert "(" in line and "unchanged)" in line
    assert len(server.patches()) == 1


def test_a_storage_failure_fails_the_step_and_a_retry_picks_up_where_it_stopped(job, server):
    server.fail_put = {"out/song_h.mp4"}
    with pytest.raises(media.StepFailed, match=r"droplets/out/song_h.mp4: .*storage answered 500: try later"):
        upload.run(job, api=api_for(server))
    assert not upload.is_done(job) and server.patches() == []
    sent_before = len(server.puts())

    server.fail_put.clear()
    server.calls.clear()
    upload.run(job, api=api_for(server))
    # The private files went the first time; only the public ones from the song on are left.
    assert len(server.puts()) == 4 and sent_before > 4
    assert upload.is_done(job)


def test_a_report_that_does_not_get_through_leaves_the_step_not_done(job, server):
    server.patch_status = 500
    with pytest.raises(media.StepFailed, match="marking the videos READY failed"):
        upload.run(job, api=api_for(server))
    assert not (job / upload.UPLOADED).exists()

    server.patch_status = 200
    server.calls.clear()
    upload.run(job, api=api_for(server))
    assert server.puts() == [] and len(server.patches()) == 1
    assert upload.is_done(job)


def test_nothing_is_uploaded_before_the_videos_and_the_lesson_exist(job, server):
    (job / media.video_path("lesson")).unlink()
    with pytest.raises(media.StepFailed, match="out/lesson_h.mp4 not rendered"):
        upload.run(job, api=api_for(server))
    (job / "posted.json").unlink()
    with pytest.raises(media.StepFailed, match="not posted yet"):
        upload.run(job, api=api_for(server))
    assert server.calls == []


def test_storage_not_set_up_on_the_api_says_so(job, server):
    server.upload_url_status = 503
    with pytest.raises(media.StepFailed, match="503 UNAVAILABLE: File storage is not configured"):
        upload.run(job, api=api_for(server))


def test_posters_are_optional_and_missing_intermediates_are_recorded(job, server):
    for video in media.VIDEOS:
        (job / media.poster_path(video)).unlink()
    (job / media.audio_path("frq")).unlink()
    line = upload.run(job, api=api_for(server))
    assert "songPosterUrl" not in server.patches()[0]
    assert jobs.read(job / upload.UPLOADED, upload.Uploaded).missing == ["audio/frq.mp3"]
    assert line.endswith("not in the job: audio/frq.mp3")


def test_a_progress_file_for_another_answer_is_not_trusted(job, server):
    upload.run(job, api=api_for(server))
    jobs.write(job / "posted.json", {"id": "ans2"})
    (job / upload.UPLOADED).unlink()
    server.stored.clear()
    upload.run(job, api=api_for(server))
    assert all("/ans2/" in p for p in server.stored) and len(server.stored) > 4


def test_a_garbled_uploaded_json_is_not_done(job):
    (job / upload.UPLOADED).write_text('{"answer_id": "ans1"')
    assert not upload.is_done(job)


def test_the_step_runs_from_the_command_line(job, server, monkeypatch, capsys):
    monkeypatch.setattr(jobs, "ROOT", job.parent)
    monkeypatch.setattr(Api, "from_env", classmethod(lambda cls: api_for(server)))
    main(["step", "upload", job.name, "--brief"])
    assert "upload: uploaded" in capsys.readouterr().err
    assert upload.is_done(job)


# --- the client: what actually goes over the wire -------------------------------------


def test_put_file_streams_a_big_body_with_its_length_and_the_studio_agent(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def urlopen(request, timeout):
        seen.update(method=request.get_method(), headers=dict(request.header_items()), data=request.data)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    api = Api(API, KEY)
    big = b"x" * (api_module.STREAM_OVER_BYTES + 1)
    api.put_file(f"{STORAGE}/object/upload/sign/droplets/a/out/song_h.mp4?token=t", big, "video/mp4")
    assert seen["method"] == "PUT"
    assert seen["headers"]["User-agent"] == api_module.USER_AGENT
    assert seen["headers"]["Content-type"] == "video/mp4" and seen["headers"]["Content-length"] == str(len(big))
    assert "Authorization" not in seen["headers"]
    assert isinstance(seen["data"], io.BytesIO)

    api.put_file(f"{STORAGE}/object/upload/sign/studio/a/locations.json?token=t", b"{}", "application/json")
    assert seen["data"] == b"{}"  # a small file goes as it is


def test_set_video_sends_only_what_it_was_given():
    sent = []
    api = Api(API, KEY, send=lambda m, u, h, b: (sent.append((m, u, json.loads(b))), (200, b"{}"))[1])
    api.set_video("ans1", "FAILED", error="render: ffmpeg exited 1")
    api.set_video("ans1", "RENDERING", song_video_url="https://x/s.mp4")
    assert sent == [("PATCH", f"{API}/api/answers/ans1/video", {"status": "FAILED", "error": "render: ffmpeg exited 1"}),
                    ("PATCH", f"{API}/api/answers/ans1/video", {"status": "RENDERING", "songVideoUrl": "https://x/s.mp4"})]


def test_only_the_named_files_go_never_partial_lock_or_stray_ones(job, server):
    (job / "art" / ".hook.png.partial").write_bytes(b"half")
    (job / "art" / "old-scene.png").write_bytes(b"from a bundle this job no longer has")
    (job / "run.lock").write_text("")
    (job / "runs.jsonl").write_text("{}\n")
    upload.run(job, api=api_for(server))
    files = {p.split("/", 2)[2] for p in server.stored}
    assert not any(f.startswith(".") or "/." in f or f.endswith((".partial", ".lock", ".jsonl")) for f in files)
    assert "art/old-scene.png" not in files


def test_upload_file_uploads_one_file_on_its_own_and_skips_it_when_unchanged(job, server):
    api = api_for(server)
    entry, sent = upload.upload_file(api, job, "ans1", "studio", "audio/song.mp3")
    assert sent == 1 and entry.bucket == "studio" and entry.public_url is None
    assert server.stored["studio/ans1/audio/song.mp3"] == (b"ID3 song", "audio/mpeg")
    assert upload.upload_file(api, job, "ans1", "studio", "audio/song.mp3")[1] == 0
    # The step later finds it already there.
    server.calls.clear()
    upload.run(job, api=api)
    assert "studio/ans1/audio/song.mp3" not in [c[1].split("/sign/")[1].split("?")[0] for c in server.puts()]


def test_a_storage_blip_is_retried_inside_the_step(job, server):
    server.flaky_puts = 2  # the first two PUTs answer 503
    upload.run(job, api=api_for(server))
    assert upload.is_done(job)


def test_storage_not_set_up_is_not_retried(job, server):
    server.upload_url_status = 503
    with pytest.raises(media.StepFailed):
        upload.run(job, api=api_for(server))
    assert len([c for c in server.calls if c[1].endswith("/upload-url")]) == 1


def test_each_attempt_uploads_to_its_own_folder_and_reports_with_its_attempt(job, server):
    jobs.write(job / "videos.json", {"answerId": "ans1", "attempt": 2})
    upload.run(job, api=api_for(server))
    assert server.stored and all(p.startswith(("droplets/ans1/a2/", "studio/ans1/a2/")) for p in server.stored)
    [report] = server.patches()
    assert report["attempt"] == 2
    assert report["songVideoUrl"] == f"{STORAGE}/object/public/droplets/ans1/a2/out/song_h.mp4"

    # A later attempt on this machine does not trust the earlier one's progress: it sends everything again.
    server.calls.clear()
    jobs.write(job / "videos.json", {"answerId": "ans1", "attempt": 3})
    (job / upload.UPLOADED).unlink()
    upload.run(job, api=api_for(server))
    assert len(server.puts()) == len([p for p in server.stored if "/a2/" in p])
    assert all("/a3/" in c[1] for c in server.puts())


def test_a_claim_for_another_answer_is_not_this_jobs_attempt(job, server):
    jobs.write(job / "videos.json", {"answerId": "someone-else", "attempt": 4})
    upload.run(job, api=api_for(server))
    assert "attempt" not in server.patches()[0]
    assert not any("/a4/" in p for p in server.stored)


def test_a_run_that_was_taken_over_fails_without_marking_ready_or_retrying(job, server):
    jobs.write(job / "videos.json", {"answerId": "ans1", "attempt": 1})
    server.patch_status = 409
    with pytest.raises(media.StepFailed, match="taken over"):
        upload.run(job, api=api_for(server))
    assert len(server.patches()) == 1 and not upload.is_done(job)
