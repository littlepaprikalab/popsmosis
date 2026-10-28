"""Live checks against Meta's Model API. They spend Meta credit, so they run only when asked for, with
STUDIO_LIVE=1 and MODEL_API_KEY set: a key in the environment alone never runs them.

    STUDIO_LIVE=1 MODEL_API_KEY=... python -m pytest studio/tests/test_live_meta.py -s

One scene of the sample bundle: up to three Muse Image pictures ($0.01 each), a Muse Spark
check of each, then SAM 3.1 on each of its two structures ($0.0025 a call, twice when the
helper's box is needed). A few cents. The picture is left in pytest's temporary folder
(the path is printed) to look at.
"""

import os

import pytest

from studio import brain, ledger, media, muse
from studio.steps import art, locate
from studio.tests import sample_media

pytestmark = pytest.mark.skipif(not (os.environ.get("STUDIO_LIVE") == "1" and os.environ.get(brain.META_KEY_VAR)),
                                reason=f"needs STUDIO_LIVE=1 and {brain.META_KEY_VAR} (spends Meta credit)")


def test_muse_image_paints_a_scene_spark_checks_it_and_sam_finds_its_structures(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_chosen", "muse-wonder")  # Muse Image paints, Muse Spark checks
    job = sample_media.write_job(tmp_path / "sample")
    bundle = sample_media.bundle()
    scene = next(s for s in bundle.scenes if s.id == "overview")
    (job / "art").mkdir(exist_ok=True)

    painting, tries = art.paint(job, bundle, scene, brain.ask)
    print(f"\n{tries} picture(s); kept {job / media.art_path(scene.id)}; the helper saw {painting.check}")

    found = {s.name: locate.find(job, scene, s, job / media.art_path(scene.id), brain.ask) for s in scene.structures}
    for name, result in found.items():
        print(f"{name}: " + ("not found" if result is None else f"{result[2]}, box {result[1].box}"))
    for row in ledger.read(job):
        print(f"{row['label']}: {row['model']} ${row['cost_usd'] or 0:.4f} {row['seconds']}s")

    assert (job / media.art_path(scene.id)).exists()
    assert [row["model"] for row in ledger.read(job)][0] == muse.IMAGE_MODEL
    assert any(result is not None for result in found.values()), "SAM found none of the scene's structures"
