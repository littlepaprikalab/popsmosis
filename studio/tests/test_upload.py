import io
import json
import urllib.request

import pytest

from studio import api as api_module
from studio import jobs, knobs, media
from studio.__main__ import main
from studio.api import Api
from studio.steps import upload
from studio.tests import sample_media

API = "https://api.example"
STORAGE = "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"  # R2's S3 API, where the bytes go
MEDIA = "https://media.example"  # the droplets bucket's public address
SUPABASE = "https://x.supabase.co/storage/v1"  # where an API from before the move to R2 signed uploads
KEY = "sk_pop_" + "0" * 64
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".mp3": "audio/mpeg", ".json": "application/json"}
PUBLIC_CACHE = "public, max-age=31536000, immutable"


def r2_error(code, message):
    return f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{message}</Message></Error>'.encode()


def put_path(url):
    """bucket/answer/file of a signed PUT URL."""
    return url.removeprefix(STORAGE + "/").split("?")[0]


class FakeServer:
    """The popsmosis API and R2 behind one `send`, as studio/api.py calls them. Like R2 on a signed URL, a PUT
    whose Content-Type is not the one the API signed into it is refused. With legacy_api, the API is one from
    before the move to R2: it signs Supabase uploads and names no headers."""

    def __init__(self, job):
        self.job = job
        self.calls = []  # (method, url, headers, body)
        self.returned = {}  # upload URL -> the headers the API said to send
        self.signed = {}  # upload URL -> the Content-Type signed into it
        self.legacy_api = False
        self.stored = {}  # "bucket/answer/file" -> (bytes, content type)
        self.fail_put = set()  # files whose PUT storage refuses
        self.flaky_puts = 0  # this many PUTs answer 503 first, then storage recovers
        self.patch_status = 200
        self.upload_url_status = 200
        self.upload_url_error = {"code": "UNAVAILABLE", "message": "File storage is not configured"}
        self.uploaded_at_patch = None

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith(API + "/api/answers/") and url.endswith("/upload-url"):
            assert headers["Authorization"] == f"Bearer {KEY}"
            if self.upload_url_status != 200:
                return self.upload_url_status, json.dumps({"error": self.upload_url_error}).encode()
            answer = url.split("/")[-2]
            req = json.loads(body)
            path = f"{req['bucket']}/{answer}/{req['file']}"
            public = req["bucket"] == "droplets"
            if self.legacy_api:
                return 200, json.dumps({"uploadUrl": f"{SUPABASE}/object/upload/sign/{path}?token=t0k", "path": path,
                                        "publicUrl": f"{SUPABASE}/object/public/{path}" if public else None}).encode()
            content_type = TYPES.get("." + req["file"].rsplit(".", 1)[-1], "application/octet-stream")
            send = {"Content-Type": content_type, **({"Cache-Control": PUBLIC_CACHE} if public else {})}
            upload_url = f"{STORAGE}/{path}?X-Amz-SignedHeaders=content-type%3Bhost&X-Amz-Signature=sig"
            self.signed[upload_url], self.returned[upload_url] = content_type, send
            return 200, json.dumps({"uploadUrl": upload_url, "headers": send, "path": path,
                                    "publicUrl": f"{MEDIA}/{answer}/{req['file']}" if public else None}).encode()
        if url.startswith(SUPABASE + "/object/upload/sign/"):
            path = url.split("/object/upload/sign/")[1].split("?")[0]
            if not headers.get("Content-Type") or headers.get("x-upsert") != "true":
                return 400, b'{"statusCode":"400","error":"Bad Request","message":"x-upsert and a type are needed"}'
            self.stored[path] = (body, headers["Content-Type"])
            return 200, json.dumps({"Key": path}).encode()
        if url.startswith(STORAGE + "/"):
            path = put_path(url)
            if headers.get("Content-Type") != self.signed.get(url):
                return 403, r2_error("SignatureDoesNotMatch", "The request signature we calculated does not match")
            if path.split("/", 2)[2] in self.fail_put:
                return 500, r2_error("InternalError", "try later")
            if self.flaky_puts:
                self.flaky_puts -= 1
                return 503, r2_error("ServiceUnavailable", "overloaded")
            self.stored[path] = (body, headers["Content-Type"])
            return 200, b""
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
    assert {"locations.json", "audio/song.mp3", "audio/narration.mp3", "timing/song_words.json",
            "timing/narration_words.json", "timeline_song.json", "timeline_lesson.json",
            "art/hook.png", "art/overview.png", "art/synthase.png",
            media.mask_path("overview", "Cytochrome b6f")} <= private
    assert not any(p.startswith(("out/", "posted", "bundle", "request", "upload")) for p in private)
    assert server.stored["droplets/ans1/out/song_h.mp4"] == (b"mp4 song", "video/mp4")
    assert server.stored["droplets/ans1/out/lesson_h.jpg"][1] == "image/jpeg"
    assert server.stored["studio/ans1/audio/narration.mp3"][1] == "audio/mpeg"

    # The bytes go to R2 on the signed URL alone, never with the agent key, and with exactly the headers the API
    # named (FakeServer refuses another type, as R2 does): the public files' long cache comes from the API.
    for _, url, headers, _ in server.puts():
        assert "X-Amz-Signature=" in url and "Authorization" not in headers
        assert headers == server.returned[url]
    assert {h["Cache-Control"] for _, u, h, _ in server.puts() if "/droplets/" in u} == {PUBLIC_CACHE}
    assert not any("Cache-Control" in h for _, u, h, _ in server.puts() if "/studio/" in u)

    # The report goes before uploaded.json, so done means READY was sent; it carries every public URL.
    assert server.uploaded_at_patch is False
    [patch] = server.patches()
    breakdown = patch.pop("breakdown")
    # Each scene's art check, with where its picture went, for an admin's review: drawn here, so none was looked at.
    art_checks = patch.pop("artChecks")
    assert art_checks == {"scenes": [{"scene": scene, "model": None, "kept": None, "check": None, "tries": [],
                                      "file": f"art/{scene}.png"} for scene in ("hook", "overview", "synthase")]}
    assert patch == {
        "status": "READY",
        "songVideoUrl": f"{MEDIA}/ans1/out/song_h.mp4",
        "lessonVideoUrl": f"{MEDIA}/ans1/out/lesson_h.mp4",
        "songPosterUrl": f"{MEDIA}/ans1/out/song_h.jpg",
        "lessonPosterUrl": f"{MEDIA}/ans1/out/lesson_h.jpg",
    }
    # With it, the media steps' breakdown: here only this step, timed up to the report.
    assert [(s["step"], s["tries"], s["models"]) for s in breakdown["steps"]] == [("upload", 1, [])]
    record = jobs.read(job / upload.UPLOADED, upload.Uploaded)
    assert record.answer_id == "ans1" and record.missing == []
    assert record.files["audio/song.mp3"].bucket == "studio" and record.files["audio/song.mp3"].public_url is None
    assert line.startswith(f"uploaded {len(server.stored)} file(s) for answer ans1; videos READY: ")


def test_running_again_sends_only_what_changed(job, server):
    upload.run(job, api=api_for(server))
    server.calls.clear()
    (job / media.video_path("song")).write_bytes(b"mp4 song, re-rendered")
    line = upload.run(job, api=api_for(server))
    assert [put_path(c[1]) for c in server.puts()] == ["droplets/ans1/out/song_h.mp4"]
    assert "(" in line and "unchanged)" in line
    assert len(server.patches()) == 1


def test_a_storage_failure_fails_the_step_and_a_retry_picks_up_where_it_stopped(job, server):
    server.fail_put = {"out/song_h.mp4"}
    with pytest.raises(media.StepFailed, match=r"droplets/out/song_h.mp4: .*storage answered 500: InternalError: try later"):
        upload.run(job, api=api_for(server))
    assert not upload.is_done(job) and server.patches() == []
    stored_before = set(server.stored)

    server.fail_put.clear()
    server.calls.clear()
    upload.run(job, api=api_for(server))
    # Uploads run side by side, so which others went before the failure varies; the retry sends the song and only
    # what was not stored the first time.
    again = [put[1] for put in server.puts()]
    assert "droplets/ans1/out/song_h.mp4" in [put_path(url) for url in again]
    assert len(again) == len({path for path in server.stored} - stored_before)
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
    (job / media.audio_path("narration")).unlink()
    line = upload.run(job, api=api_for(server))
    assert "songPosterUrl" not in server.patches()[0]
    assert jobs.read(job / upload.UPLOADED, upload.Uploaded).missing == ["audio/narration.mp3"]
    assert line.endswith("not in the job: audio/narration.mp3")


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
    api.put_file(f"{STORAGE}/droplets/a/out/song_h.mp4?X-Amz-Signature=s", big,
                 {"Content-Type": "video/mp4", "Cache-Control": PUBLIC_CACHE})
    assert seen["method"] == "PUT"
    assert seen["headers"]["User-agent"] == api_module.USER_AGENT
    assert seen["headers"]["Content-type"] == "video/mp4" and seen["headers"]["Content-length"] == str(len(big))
    assert seen["headers"]["Cache-control"] == PUBLIC_CACHE
    assert "Authorization" not in seen["headers"] and "X-upsert" not in seen["headers"]
    assert isinstance(seen["data"], io.BytesIO)

    api.put_file(f"{STORAGE}/studio/a/locations.json?X-Amz-Signature=s", b"{}", {"Content-Type": "application/json"})
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
    assert "studio/ans1/audio/song.mp3" not in [put_path(c[1]) for c in server.puts()]


def test_a_storage_blip_is_retried_inside_the_step(job, server):
    server.flaky_puts = 2  # the first two PUTs answer 503
    upload.run(job, api=api_for(server))
    assert upload.is_done(job)


def test_storage_not_set_up_is_not_retried(job, server):
    server.upload_url_status = 503
    with pytest.raises(media.StepFailed):
        upload.run(job, api=api_for(server))
    assert len([c for c in server.calls if c[1].endswith("/upload-url")]) == 1


def test_the_apis_retryable_flag_decides_whether_a_storage_failure_is_retried(job, server):
    # The two 503s the API's upload-url sends once storage is set up (api/src/routes/answers.ts).
    signs = lambda: len([c for c in server.calls if c[1].endswith("/upload-url")])
    server.upload_url_status = 503
    server.upload_url_error = {"code": "UNAVAILABLE", "message": "File storage is misconfigured on this API: "
                               "R2_PUBLIC_URL is not set; uploads need all of R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                               "R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL"}
    with pytest.raises(media.StepFailed, match="R2_PUBLIC_URL is not set"):
        upload.run(job, api=api_for(server))
    assert signs() == 1  # waiting will not fix a refusal

    server.calls.clear()
    server.upload_url_error = {"code": "UNAVAILABLE", "message": "File storage did not give an upload URL "
                               "(storage answered 503); try again", "retryable": True}
    with pytest.raises(media.StepFailed, match="try again"):
        upload.run(job, api=api_for(server))
    assert signs() == 1 + len(upload.RETRY_DELAYS_S)


def test_each_attempt_uploads_to_its_own_folder_and_reports_with_its_attempt(job, server):
    jobs.write(job / "videos.json", {"answerId": "ans1", "attempt": 2})
    upload.run(job, api=api_for(server))
    assert server.stored and all(p.startswith(("droplets/ans1/a2/", "studio/ans1/a2/")) for p in server.stored)
    [report] = server.patches()
    assert report["attempt"] == 2
    assert report["songVideoUrl"] == f"{MEDIA}/ans1/a2/out/song_h.mp4"

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


def test_a_muse_pictures_check_goes_with_the_rerender_kit_and_a_drawing_misses_none(job):
    there, missing = upload.private_files(job)
    assert not any(p.endswith(".json") and p.startswith("art/") for p in there + missing)  # drawn: none
    (job / media.check_path("overview")).write_text("{}")
    there, missing = upload.private_files(job)
    assert media.check_path("overview") in there and media.check_path("hook") not in missing


def test_a_refused_put_says_what_r2_said_and_what_to_check():
    def r2(code, message, status=403):
        return lambda m, u, h, b: (status, r2_error(code, message))

    with pytest.raises(api_module.ApiError, match=r"storage answered 403: SignatureDoesNotMatch: .*check R2_ACCESS_KEY_ID"):
        Api(API, KEY, send=r2("SignatureDoesNotMatch", "The request signature we calculated does not match")).put_file(
            f"{STORAGE}/droplets/a/x.mp4?X-Amz-Signature=s", b"x", {"Content-Type": "video/mp4"})
    with pytest.raises(api_module.ApiError, match=r"storage answered 404: NoSuchBucket: .*R2_ACCOUNT_ID"):
        Api(API, KEY, send=r2("NoSuchBucket", "The specified bucket does not exist.", 404)).put_file(
            f"{STORAGE}/studio/a/x.json?X-Amz-Signature=s", b"x", {"Content-Type": "application/json"})
    with pytest.raises(api_module.ApiError, match=r"^502 UPLOAD_FAILED: storage answered 502$"):
        Api(API, KEY, send=lambda m, u, h, b: (502, b"<html>Bad gateway</html>")).put_file(
            f"{STORAGE}/studio/a/x.json?X-Amz-Signature=s", b"x", {"Content-Type": "application/json"})


def test_storage_out_of_reach_names_the_host_the_network_policy_must_allow():
    def unreachable(method, url, headers, body):
        raise api_module.ApiError(0, "UNREACHABLE", "the API did not answer: Tunnel connection failed: 403 Forbidden")

    with pytest.raises(api_module.ApiError) as e:
        Api(API, KEY, send=unreachable).put_file(f"{STORAGE}/droplets/a/x.mp4?X-Amz-Signature=s", b"x", {"Content-Type": "video/mp4"})
    assert e.value.status == 0
    assert str(e.value) == ("storage did not answer (0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com): "
                            "Tunnel connection failed: 403 Forbidden; the network policy where the studio runs must "
                            "allow this host")


def test_an_api_from_before_r2_still_gets_the_videos_uploaded_to_supabase(job, server):
    # The studio is newer than the API (its redeploy is still running, or failed): it uploads as Supabase took it,
    # and the API moves the files to R2 once it is redeployed.
    server.legacy_api = True
    upload.run(job, api=api_for(server))
    assert upload.is_done(job)
    assert server.stored["droplets/ans1/out/song_h.mp4"] == (b"mp4 song", "video/mp4")
    assert server.stored["studio/ans1/audio/song.mp3"][1] == "audio/mpeg"
    assert server.patches()[0]["songVideoUrl"] == f"{SUPABASE}/object/public/droplets/ans1/out/song_h.mp4"


def test_upload_headers_the_studio_cannot_use_fail_the_step_before_any_put(job, server):
    real = server.__call__

    def bad_headers(method, url, headers, body):
        status, raw = real(method, url, headers, body)
        if url.endswith("/upload-url") and status == 200:
            answer = json.loads(raw)
            answer["headers"] = {"Content-Type": 7}
            raw = json.dumps(answer).encode()
        return status, raw

    with pytest.raises(media.StepFailed, match="upload headers the studio cannot use"):
        upload.run(job, api=Api(API, KEY, send=bad_headers))
    assert server.puts() == []


def test_a_supabase_refusal_is_read_too():
    refuse = lambda m, u, h, b: (400, b'{"statusCode":"413","error":"Payload too large","message":"The object exceeded the maximum allowed size"}')
    with pytest.raises(api_module.ApiError, match=r"storage answered 400: The object exceeded the maximum allowed size$"):
        Api(API, KEY, send=refuse).put_file(f"{SUPABASE}/object/upload/sign/droplets/a/x.mp4?token=t", b"x", {"Content-Type": "video/mp4"})


def test_the_hook_cut_goes_up_with_the_videos_when_it_was_made(job, server):
    (job / "out" / "hook_v.mp4").write_bytes(b"mp4 hook")
    (job / "out" / "hook_v.jpg").write_bytes(b"jpg hook")
    assert {"out/hook_v.mp4", "out/hook_v.jpg"} <= set(upload.public_files(job))
    upload.run(job, api=api_for(server))
    ready = server.patches()[-1]
    assert ready["hookVideoUrl"].endswith("/out/hook_v.mp4") and ready["hookPosterUrl"].endswith("/out/hook_v.jpg")


def test_a_render_picked_up_elsewhere_gets_the_hook_cuts_timeline_too(job):
    assert "timeline_hook.json" not in upload.private_files(job)[0]
    (job / "timeline_hook.json").write_text("{}")
    assert "timeline_hook.json" in upload.private_files(job)[0]


def test_a_painted_pictures_check_goes_with_ready_and_one_left_over_from_another_bundle_does_not(job, server):
    from studio.schemas import Bundle
    from studio.steps import art

    bundle = jobs.read(job / "bundle.json", Bundle)
    overview = next(s for s in bundle.scenes if s.id == "overview")
    check = {"text": [], "misplaced": [], "unnamed": [], "wrong": ["the membrane is drawn flat"], "notes": []}
    tries = [{"try": 1, "declined": "the picture was filtered"}, {"try": 2, **check}]
    # Painted by Muse Image, whatever brain is in force where the upload runs (here Claude's, whose painter is Gemini).
    jobs.write(job / media.check_path("overview"), {"key": art.key(bundle, overview), "model": "muse-image-1.0",
                                                     "prompt": "Educational diagram", "try": 2, "check": check, "tries": tries})
    jobs.write(job / media.check_path("hook"), {"key": "an older bundle's", "model": "muse-image-1.0",
                                                 "try": 1, "check": check, "tries": []})
    jobs.write(job / "videos.json", {"answerId": "ans1", "attempt": 3})
    upload.run(job, api=api_for(server))
    scenes = {s["scene"]: s for s in server.patches()[0]["artChecks"]["scenes"]}
    assert scenes["overview"] == {"scene": "overview", "model": "muse-image-1.0", "kept": 2, "check": check,
                                  "tries": tries, "file": "a3/art/overview.png"}
    assert scenes["hook"]["model"] is None and scenes["hook"]["check"] is None  # not this bundle's picture
    assert "prompt" not in scenes["overview"]


def test_art_checks_more_than_the_api_takes_are_left_out_and_the_videos_still_go_ready(job, server, monkeypatch):
    from studio import review

    monkeypatch.setattr(review, "ART_CHECKS_MAX_BYTES", 100)
    upload.run(job, api=api_for(server))
    [patch] = server.patches()
    assert patch["status"] == "READY" and "artChecks" not in patch


def test_the_lesson_alone_uploads_and_reports_only_the_lesson_video(job, server):
    """STUDIO_VIDEOS=lesson: READY names the lesson video alone, and a song or hook cut left from a run that made them
    stays here."""
    (job / "out" / "hook_v.mp4").write_bytes(b"mp4 hook")
    (job / "out" / "hook_v.jpg").write_bytes(b"jpg hook")
    (job / "timeline_hook.json").write_text("{}")
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    line = upload.run(job, api=api_for(server))
    assert {p for p in server.stored if p.startswith("droplets/")} == {
        "droplets/ans1/out/lesson_h.mp4", "droplets/ans1/out/lesson_h.jpg"}
    private = {p.removeprefix("studio/ans1/") for p in server.stored if p.startswith("studio/")}
    assert {"audio/narration.mp3", "timing/narration_words.json", "timeline_lesson.json"} <= private
    assert not private & {"audio/song.mp3", "timing/song_words.json", "timeline_song.json", "timeline_hook.json"}
    [patch] = server.patches()
    del patch["breakdown"]
    assert [s["scene"] for s in patch.pop("artChecks")["scenes"]] == ["hook", "overview", "synthase"]
    assert patch == {"status": "READY", "lessonVideoUrl": f"{MEDIA}/ans1/out/lesson_h.mp4",
                     "lessonPosterUrl": f"{MEDIA}/ans1/out/lesson_h.jpg"}
    assert jobs.read(job / upload.UPLOADED, upload.Uploaded).song_video_url is None
    assert line.endswith(f"videos READY: {MEDIA}/ans1/out/lesson_h.mp4")


def test_a_song_left_out_says_why_with_ready(job, server):
    from studio import anneal

    anneal.drop_song(job, "the song failed at audio (x)", "the ElevenLabs account is out of credit")
    upload.run(job, api=api_for(server))
    assert server.patches()[-1]["status"] == "READY"
    assert server.patches()[-1]["error"] == "The song could not be made: the ElevenLabs account is out of credit."


def test_an_api_from_before_ready_took_a_note_still_gets_ready_without_it(job, server):
    from studio import anneal

    anneal.drop_song(job, "the song failed at audio", "")
    answer = server.__call__

    def older(method, url, headers, body):
        if method == "PATCH" and "error" in json.loads(body) and json.loads(body)["status"] == "READY":
            server.calls.append((method, url, headers, body))
            return 400, b'{"error":{"code":"VALIDATION_ERROR","message":"error is only for status FAILED"}}'
        return answer(method, url, headers, body)

    upload.run(job, api=Api(API, KEY, send=older))
    assert [p.get("error") for p in server.patches()][-2:] == ["The song could not be made.", None]
    assert server.patches()[-1]["status"] == "READY" and (job / upload.UPLOADED).exists()


def test_videos_with_their_song_send_no_note(job, server):
    upload.run(job, api=api_for(server))
    assert "error" not in server.patches()[-1]
