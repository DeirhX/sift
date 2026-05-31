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
