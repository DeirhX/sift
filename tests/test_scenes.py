"""Unit tests for photo_audit's scene grouping + EXIF capture time, and the
scene-aware duplicate nesting. All model-free: scenes are driven by synthetic
timestamps + optional embeddings/hashes."""
from pathlib import Path

import numpy as np
import pytest

import photo_audit


def _paths(*names):
    return [Path(n) for n in names]


# ── group_scenes: time-gap segmentation ──────────────────────────────────────

def test_big_gap_always_splits():
    p = _paths("a.jpg", "b.jpg")
    # 2 hours apart, no visual signal -> different scenes -> both singletons.
    times = {p[0]: 0.0, p[1]: 7200.0}
    scene_of, n = photo_audit.group_scenes(p, times, big_gap=3600, small_gap=120)
    assert n == 0                      # two singletons, no multi-member scene
    assert scene_of[p[0]] is None and scene_of[p[1]] is None


def test_tight_burst_stays_together():
    p = _paths("a.jpg", "b.jpg", "c.jpg")
    times = {p[0]: 0.0, p[1]: 30.0, p[2]: 60.0}   # all within small_gap
    scene_of, n = photo_audit.group_scenes(p, times, big_gap=3600, small_gap=120)
    assert n == 1
    assert scene_of[p[0]] == scene_of[p[1]] == scene_of[p[2]] == 0


def test_medium_gap_splits_when_visually_dissimilar():
    p = _paths("a.jpg", "b.jpg")
    times = {p[0]: 0.0, p[1]: 600.0}              # 10 min: between small and big
    # Orthogonal embeddings -> cosine 0 < 0.85 -> split.
    embs = {p[0]: np.array([1.0, 0.0]), p[1]: np.array([0.0, 1.0])}
    scene_of, n = photo_audit.group_scenes(p, times, embeddings=embs,
                                           big_gap=3600, small_gap=120, sim=0.85)
    assert n == 0


def test_medium_gap_stays_when_visually_similar():
    p = _paths("a.jpg", "b.jpg")
    times = {p[0]: 0.0, p[1]: 600.0}
    # Near-identical embeddings -> cosine ~1 >= 0.85 -> same scene.
    embs = {p[0]: np.array([1.0, 0.0]), p[1]: np.array([0.999, 0.01])}
    scene_of, n = photo_audit.group_scenes(p, times, embeddings=embs,
                                           big_gap=3600, small_gap=120, sim=0.85)
    assert n == 1
    assert scene_of[p[0]] == scene_of[p[1]] == 0


def test_phash_fallback_when_no_embeddings():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    times = {p[0]: 0.0, p[1]: 600.0}
    # Identical hashes -> distance 0 <= phash_dist -> similar -> same scene.
    hashes = {p[0]: imagehash.hex_to_hash("f" * 16),
              p[1]: imagehash.hex_to_hash("f" * 16)}
    scene_of, n = photo_audit.group_scenes(p, times, hashes=hashes,
                                           big_gap=3600, small_gap=120, phash_dist=18)
    assert n == 1


def test_time_order_is_used_not_input_order():
    # Input order is reversed vs capture order; segmentation must sort by time.
    p = _paths("late.jpg", "early.jpg", "mid.jpg")
    times = {p[0]: 200.0, p[1]: 0.0, p[2]: 100.0}
    scene_of, n = photo_audit.group_scenes(p, times, big_gap=3600, small_gap=600)
    # All within small_gap of their time-neighbour -> one scene.
    assert n == 1
    assert all(scene_of[x] == 0 for x in p)


# ── read_capture_time ─────────────────────────────────────────────────────────

def test_read_capture_time_parses_exif(tmp_path):
    pytest.importorskip("PIL.Image")
    # Build EXIF the easy way via PIL's Exif object.
    from PIL import Image as PILImage
    img = PILImage.new("RGB", (8, 8), (10, 20, 30))
    exif = img.getexif()
    # 0x9003 lives in the Exif sub-IFD.
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = "2021:07:15 12:34:56"
    fp = tmp_path / "with_exif.jpg"
    img.save(fp, exif=exif)
    ct = photo_audit.read_capture_time(fp)
    assert ct is not None
    # Round-trip the same local time and compare.
    from datetime import datetime
    assert ct == pytest.approx(
        datetime.strptime("2021:07:15 12:34:56", "%Y:%m:%d %H:%M:%S").timestamp())


def test_read_capture_time_none_without_exif(tmp_path):
    from PIL import Image as PILImage
    fp = tmp_path / "no_exif.jpg"
    PILImage.new("RGB", (8, 8), (1, 2, 3)).save(fp)
    assert photo_audit.read_capture_time(fp) is None


# ── assign_dup_groups: nesting within scenes ─────────────────────────────────

def test_dups_nest_within_scenes():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    # a,b identical and in scene 0; c,d identical but in *different* scenes.
    same = imagehash.hex_to_hash("f" * 16)
    hashes = {p[0]: same, p[1]: same, p[2]: same, p[3]: same}
    scene_of = {p[0]: 0, p[1]: 0, p[2]: 1, p[3]: 2}
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, scene_of=scene_of)
    # Only a,b group (same scene). c and d are alone in their scenes.
    assert len(groups) == 1
    assert {x.name for x in groups[0]} == {"a.jpg", "b.jpg"}
    assert p[2] not in path_to_group and p[3] not in path_to_group


def test_dups_global_when_no_scenes():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    same = imagehash.hex_to_hash("f" * 16)
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, {p[0]: same, p[1]: same}, threshold=6, scene_of=None)
    assert len(groups) == 1
    assert path_to_group[p[0]] == path_to_group[p[1]] == 0
