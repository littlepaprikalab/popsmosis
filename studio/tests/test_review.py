import json

from studio import review


def test_code_checks_are_checks_json_as_written(tmp_path):
    report = {"problems": [{"line_id": "n4", "rule": "not-in-scene", "detail": "names DNA", "repaired": False}]}
    (tmp_path / "checks.json").write_text(json.dumps(report))
    assert review.code_checks(tmp_path) == report


def test_code_checks_that_cannot_be_read_or_are_too_big_are_left_out_never_failing_the_post(tmp_path, monkeypatch, capsys):
    assert review.code_checks(tmp_path) is None  # a bundle from before the checks
    (tmp_path / "checks.json").write_text("{not json")
    assert review.code_checks(tmp_path) is None
    (tmp_path / "checks.json").write_text(json.dumps({"problems": "none"}))
    assert review.code_checks(tmp_path) is None
    (tmp_path / "checks.json").write_text(json.dumps({"problems": [{"rule": "x", "detail": "y" * 200}]}))
    monkeypatch.setattr(review, "CHECKS_MAX_BYTES", 100)
    assert review.code_checks(tmp_path) is None
    assert "more than the API takes" in capsys.readouterr().err


def test_art_checks_without_a_bundle_are_left_out(tmp_path):
    assert review.art_checks(tmp_path, {}) is None
