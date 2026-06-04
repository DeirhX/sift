"""ML / CV task efficacy harness.

Two tiers:

1. **Classical (always run)** — the non-neural tasks (Laplacian sharpness,
   perceptual-hash duplicate detection) have deterministic, synthesizable ground
   truth, so their efficacy is asserted on every run with no model weights.

2. **Neural (opt-in, @pytest.mark.ml)** — PARA, CLIP-IQA, Qwen3-VL tagging, BLIP
   captioning and face detection load multi-GB weights and want a GPU. Skipped
   unless `--run-ml` / `RUN_ML=1`. On synthetic inputs we can only assert the
   *contract* (output range/shape/determinism), which catches load breakage,
   range regressions and silent drift. True *accuracy* needs labeled photos:
   drop them in `tests/ml/fixtures/` with a `labels.json` and `test_golden_set`
   asserts expected tags/captions/faces against them.

Run the neural tier:  pytest tests/ml -m ml --run-ml
"""
import json
from pathlib import Path

import pytest

from sift import audit as photo_audit

FIXTURES = Path(__file__).parent / "fixtures"


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _noise(seed, size=(256, 256)):
    import numpy as np
    return np.random.default_rng(seed).integers(0, 256, (*size, 3), dtype="uint8")


# ── Tier 1: classical, always run ────────────────────────────────────────────

def test_sharpness_ranks_sharp_above_blurred(tmp_path):
    """Laplacian variance must score a crisp image above a blurred copy of it —
    the core premise of the sharpness/blur metric."""
    pytest.importorskip("cv2")
    from PIL import Image, ImageFilter
    img = Image.fromarray(_noise(0))
    sharp, blur = tmp_path / "sharp.jpg", tmp_path / "blur.jpg"
    img.save(sharp, "JPEG", quality=95)
    img.filter(ImageFilter.GaussianBlur(4)).save(blur, "JPEG", quality=95)

    assert photo_audit.laplacian_variance(sharp) > photo_audit.laplacian_variance(blur)


def test_duplicate_detection_groups_recompressed_copy(tmp_path):
    """A re-encoded copy of an image must group with its original (phash is
    robust to JPEG recompression); an unrelated image must stay out."""
    pytest.importorskip("imagehash")
    from PIL import Image
    a, b = Image.fromarray(_noise(1, (128, 128))), Image.fromarray(_noise(2, (128, 128)))
    pa, pa_copy, pb = tmp_path / "a.jpg", tmp_path / "a_copy.jpg", tmp_path / "b.jpg"
    a.save(pa, "JPEG", quality=95)
    a.save(pa_copy, "JPEG", quality=60)            # heavy recompression
    b.save(pb, "JPEG", quality=95)

    hashes, _ = photo_audit.compute_phashes([pa, pa_copy, pb])
    groups = photo_audit.group_duplicates(hashes, threshold=6)
    assert len(groups) == 1
    assert {p.name for p in groups[0]} == {"a.jpg", "a_copy.jpg"}


# ── Tier 2: neural, opt-in ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sample_image(tmp_path_factory):
    from PIL import Image
    p = tmp_path_factory.mktemp("ml") / "sample.jpg"
    Image.fromarray(_noise(0)).save(p, "JPEG", quality=95)
    return p


@pytest.mark.ml
def test_para_scores_in_range_and_deterministic(sample_image):
    pytest.importorskip("torch")
    out1 = photo_audit.run_para([sample_image], _device())
    out2 = photo_audit.run_para([sample_image], _device())
    rec = out1[sample_image]
    assert set(rec) == set(photo_audit.PARA_KEYS)
    assert all(0.0 <= v <= 5.0 for v in rec.values())          # raw head scale
    assert out2[sample_image] == pytest.approx(rec, abs=1e-3)  # no silent drift


@pytest.mark.ml
def test_clip_iqa_in_unit_range(sample_image):
    pytest.importorskip("torch")
    s = photo_audit.run_clip_iqa([sample_image], _device())[sample_image]
    assert 0.0 <= s <= 1.0


@pytest.mark.ml
def test_qwen_tags_obey_the_cleaning_contract(sample_image):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    tags = photo_audit.run_qwen_tags([sample_image], _device(), top_k=8)[sample_image]
    assert isinstance(tags, list) and len(tags) <= 8
    assert tags == [t.lower() for t in tags]                   # lowercased
    assert len(tags) == len(set(tags))                         # deduped
    assert all(1 <= len(t.split()) <= 4 for t in tags)         # keywords, not sentences


@pytest.mark.ml
def test_blip_caption_is_nonempty_text(sample_image):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    rec = photo_audit.run_caption_and_tags([sample_image], _device(), top_k=8)[sample_image]
    assert isinstance(rec["caption"], str) and rec["caption"].strip()


# ── Tier 2b: data-driven accuracy against user-supplied labeled photos ───────

def _labels():
    f = FIXTURES / "labels.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


@pytest.mark.ml
def test_golden_set_accuracy():
    """Assert real model accuracy against labeled fixtures. Skips cleanly until
    you populate tests/ml/fixtures/ (see its README for the labels.json schema)."""
    labels = _labels()
    if not labels:
        pytest.skip("no tests/ml/fixtures/labels.json — add labeled photos to "
                    "assert real tag/caption/face accuracy")
    device = _device()
    for label in labels:
        img = FIXTURES / label["file"]
        assert img.exists(), f"missing fixture image: {img}"

        if "expect_min_faces" in label:
            faces, _, _ = photo_audit.run_faces([img], device)
            n = len(faces.get(img, []))
            assert n >= label["expect_min_faces"], \
                f"{img.name}: detected {n} faces, expected >= {label['expect_min_faces']}"

        if "expect_tags" in label:
            tags = photo_audit.run_qwen_tags([img], device, top_k=20)[img]
            for want in label["expect_tags"]:
                assert any(want.lower() in t for t in tags), \
                    f"{img.name}: expected a tag containing {want!r}, got {tags}"

        if "expect_caption_substr" in label:
            rec = photo_audit.run_caption_and_tags([img], device)[img]
            assert label["expect_caption_substr"].lower() in rec["caption"].lower(), \
                f"{img.name}: caption {rec['caption']!r} missing {label['expect_caption_substr']!r}"
