"""Unit tests for photo_audit's pure, model-free helpers: sharpness
normalisation and perceptual-hash duplicate grouping."""
from pathlib import Path

import pytest

import photo_audit


# ── normalise_sharpness ──────────────────────────────────────────────────────

def test_normalise_all_equal_returns_midpoint():
    assert photo_audit.normalise_sharpness([5.0, 5.0, 5.0]) == [0.5, 0.5, 0.5]


def test_normalise_spans_zero_to_one():
    out = photo_audit.normalise_sharpness([0.0, 10.0, 100.0])
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert out == sorted(out)            # monotonic in the input order


def test_normalise_is_log_scaled():
    # log1p compresses the top end: the midpoint value maps above 0.5 here.
    out = photo_audit.normalise_sharpness([0.0, 9.0, 99.0])
    assert 0.0 < out[1] < 1.0


def test_normalise_single_value():
    assert photo_audit.normalise_sharpness([3.0]) == [0.5]


# ── group_duplicates ─────────────────────────────────────────────────────────

def _hashes(mapping):
    imagehash = pytest.importorskip("imagehash")
    return {Path(name): imagehash.hex_to_hash(hx) for name, hx in mapping.items()}


def test_group_identical_hashes():
    h = "f" * 16          # 64-bit phash, all ones
    groups = photo_audit.group_duplicates(
        _hashes({"a.jpg": h, "b.jpg": h, "c.jpg": "0" * 16}), threshold=6)
    assert len(groups) == 1
    assert {p.name for p in groups[0]} == {"a.jpg", "b.jpg"}


def test_no_duplicates_when_all_far_apart():
    groups = photo_audit.group_duplicates(
        _hashes({"a.jpg": "0" * 16, "b.jpg": "f" * 16}), threshold=6)
    assert groups == []


def test_singletons_are_never_grouped():
    groups = photo_audit.group_duplicates(_hashes({"only.jpg": "a" * 16}), threshold=6)
    assert groups == []


def test_threshold_controls_grouping():
    # Two hashes differing by 4 bits: grouped at threshold 6, split at threshold 2.
    pair = {"a.jpg": "0" * 16, "b.jpg": "0" * 15 + "f"}   # last nibble 0->f = 4 bits
    assert len(photo_audit.group_duplicates(_hashes(pair), threshold=6)) == 1
    assert photo_audit.group_duplicates(_hashes(pair), threshold=2) == []


# ── _clean_tags (Qwen3-VL keyword post-processing) ───────────────────────────
# The model's raw output is a free-form comma list; _clean_tags is the only pure
# logic on the tagging path (the model call itself can't be unit-tested without
# loading 6 GB of weights), so it carries the bulk of the tag-quality contract.

def test_clean_tags_lowercases_splits_and_strips():
    assert photo_audit._clean_tags("Black Cat, Plush Toy , SOFA", 12) == \
        ["black cat", "plush toy", "sofa"]


def test_clean_tags_dedupes_preserving_order():
    assert photo_audit._clean_tags("cat, dog, cat, bird, dog", 12) == \
        ["cat", "dog", "bird"]


def test_clean_tags_drops_empty_chunks():
    assert photo_audit._clean_tags("cat, , ,dog,", 12) == ["cat", "dog"]


def test_clean_tags_strips_list_markers():
    # Numbered ("1." / "2)"), dash, and bullet list prefixes the model sometimes
    # emits despite being told not to.
    out = photo_audit._clean_tags("1. cat\n2) dog\n- bird\n\u2022 fish", 12)
    assert out == ["cat", "dog", "bird", "fish"]


def test_clean_tags_strips_trailing_period():
    assert photo_audit._clean_tags("forest scene.", 12) == ["forest scene"]


def test_clean_tags_drops_sentence_length_output():
    long = "this is clearly a whole descriptive sentence rather than a keyword"
    assert photo_audit._clean_tags(f"cat, {long}, dog", 12) == ["cat", "dog"]


def test_clean_tags_drops_more_than_four_words():
    assert photo_audit._clean_tags(
        "one two three four, one two three four five", 12) == ["one two three four"]


def test_clean_tags_respects_top_k():
    assert photo_audit._clean_tags("a, b, c, d, e", 3) == ["a", "b", "c"]


def test_clean_tags_handles_empty_and_none():
    assert photo_audit._clean_tags("", 12) == []
    assert photo_audit._clean_tags(None, 12) == []


def test_clean_tags_preserves_leading_digit_words():
    # Regression: the list-marker strip must NOT eat a digit that's part of a
    # word ("4k resolution" -> "k resolution" was the bug).
    assert photo_audit._clean_tags("4k resolution, 35mm, 3d render", 12) == \
        ["4k resolution", "35mm", "3d render"]


def test_clean_tags_splits_on_newlines_and_semicolons():
    assert photo_audit._clean_tags("cat\ndog;bird", 12) == ["cat", "dog", "bird"]


# ── Qwen tagging orchestration (model mocked out) ────────────────────────────

def test_run_qwen_tags_degrades_gracefully_on_load_failure(monkeypatch, tmp_path):
    """A model/load failure must never propagate: every path gets [] and the
    run continues. This is what keeps a broken tagger from killing an audit."""
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("torch")

    def boom(*a, **k):
        raise RuntimeError("no model for you")
    monkeypatch.setattr(transformers.AutoProcessor, "from_pretrained", boom)

    paths = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    out = photo_audit.run_qwen_tags(paths, device="cpu", top_k=12)
    assert out == {paths[0]: [], paths[1]: []}


def test_run_caption_and_tags_merges_qwen_tags(monkeypatch, tmp_path):
    """run_caption_and_tags must attach run_qwen_tags' output to each record even
    when BLIP captioning fails (captioning and tagging are independent paths)."""
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("torch")

    def boom(*a, **k):
        raise RuntimeError("no blip")
    monkeypatch.setattr(transformers.BlipProcessor, "from_pretrained", boom)

    p = tmp_path / "x.jpg"
    monkeypatch.setattr(photo_audit, "run_qwen_tags",
                        lambda paths, device, top_k=12: {paths[0]: ["cat", "sofa"]})

    res = photo_audit.run_caption_and_tags([p], device="cpu", top_k=12)
    assert res[p]["tags"] == ["cat", "sofa"]
    assert res[p]["caption"] == ""          # BLIP failed, but tags still landed


# ── CLI surface (locks the contract webapp/server.py relies on) ──────────────

def test_parser_accepts_top_tags():
    args = photo_audit.build_parser().parse_args(["folder", "--caption", "--top-tags", "5"])
    assert args.top_tags == 5
    assert args.caption is True


def test_parser_top_tags_defaults_to_12():
    assert photo_audit.build_parser().parse_args(["folder"]).top_tags == 12


@pytest.mark.parametrize("flag", ["--tag-min-prob", "--tag-cap-z", "--no-caption-ground"])
def test_parser_rejects_retired_clip_tag_flags(flag):
    # These CLIP-era knobs are gone. server.py never passed them; assert they're
    # truly removed so nobody silently resurrects a dead grounding path.
    with pytest.raises(SystemExit):
        photo_audit.build_parser().parse_args(["folder", flag, "0.1"])
