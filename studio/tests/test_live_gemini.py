"""Gemini 3.1 Flash Image live: one picture of one scene, checked as the art step checks it. About 7 cents, so it
runs only when asked for: STUDIO_LIVE=1 GEMINI_API_KEY=... python -m pytest studio/tests/test_live_gemini.py -s"""

import os

import pytest

from studio import gemini, ledger, media
from studio.steps import art
from studio.tests import sample_media

pytestmark = pytest.mark.skipif(not (os.environ.get("STUDIO_LIVE") == "1" and os.environ.get(gemini.KEY_VAR)),
                                reason=f"needs STUDIO_LIVE=1 and {gemini.KEY_VAR} (spends Gemini credit)")


def test_gemini_paints_a_scene_the_art_step_can_fit(tmp_path):
    job = sample_media.write_job(tmp_path / "sample")
    bundle = sample_media.bundle()
    scene = next(s for s in bundle.scenes if s.id == "overview")
    picture = gemini.paint(job, "art overview", art.paint_prompt(bundle, scene))
    pixels = art.fit(picture)
    assert pixels.shape == (media.ART_HEIGHT, media.ART_WIDTH, 3)
    out = tmp_path / "overview.png"
    out.write_bytes(picture)
    [call] = ledger.read(job)
    print(f"\n{out}: {call['input_tokens']} in, {call['output_tokens']} out, ${call['cost_usd']:.4f}, {call['seconds']}s")
    assert call["model"] == gemini.MODEL and 0.05 < call["cost_usd"] < 0.09
