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


def test_compute_phashes_honours_exif_orientation(tmp_path):
    """A portrait frame carries its rotation in the EXIF Orientation tag, not the
    pixels. compute_phashes must hash the upright image (else a portrait reads as
    a 90°-rotated stranger and never dedups against a landscape reframe)."""
    imagehash = pytest.importorskip("imagehash")
    from PIL import Image, ImageDraw, ImageOps

    # Asymmetric content so orientation actually changes the hash.
    img = Image.new("RGB", (320, 240), "black")
    ImageDraw.Draw(img).rectangle([0, 0, 140, 70], fill="white")
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation: rotate 90° CW on display
    path = tmp_path / "portrait.jpg"
    img.save(path, "JPEG", exif=exif)

    hashes, _ = photo_audit.compute_phashes([path])
    upright = imagehash.phash(ImageOps.exif_transpose(Image.open(path)))
    raw = imagehash.phash(Image.open(path))

    assert hashes[path] == upright          # hashed the displayed orientation
    assert (hashes[path] - raw) > 0         # and the tag genuinely changed it


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


# ── assign_dup_groups: phash + CLIP, time-windowed ───────────────────────────

def test_phash_dups_group_regardless_of_time():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    same = imagehash.hex_to_hash("f" * 16)
    # Identical hashes but taken a day apart: a literal duplicate is a duplicate
    # regardless of time, so the (time-independent) phash edge still groups them.
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, {p[0]: same, p[1]: same}, threshold=6,
        times={p[0]: 0.0, p[1]: 86400.0}, dup_window=600.0)
    assert len(groups) == 1
    assert path_to_group[p[0]] == path_to_group[p[1]] == 0


def test_clip_groups_what_phash_misses_within_window():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    # Maximally different hashes (distance 64) -> phash says "unrelated"...
    hashes = {p[0]: imagehash.hex_to_hash("f" * 16),
              p[1]: imagehash.hex_to_hash("0" * 16)}
    # ...but near-identical CLIP embeddings, 100 s apart -> grouped via cosine.
    embs = {p[0]: np.array([1.0, 0.0]), p[1]: np.array([0.999, 0.01])}
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times={p[0]: 0.0, p[1]: 100.0}, dup_window=600.0)
    assert len(groups) == 1
    assert path_to_group[p[0]] == path_to_group[p[1]] == 0


def test_clip_dups_respect_time_window():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    hashes = {p[0]: imagehash.hex_to_hash("f" * 16),
              p[1]: imagehash.hex_to_hash("0" * 16)}
    embs = {p[0]: np.array([1.0, 0.0]), p[1]: np.array([0.999, 0.01])}
    # Same look-alikes, but hours apart and beyond the window -> NOT grouped.
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times={p[0]: 0.0, p[1]: 10000.0}, dup_window=600.0)
    assert groups == []
    assert path_to_group == {}


def test_low_cosine_does_not_group():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    hashes = {p[0]: imagehash.hex_to_hash("f" * 16),
              p[1]: imagehash.hex_to_hash("0" * 16)}
    # Orthogonal embeddings (cosine 0) and distant hashes -> nothing groups.
    embs = {p[0]: np.array([1.0, 0.0]), p[1]: np.array([0.0, 1.0])}
    _, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times={p[0]: 0.0, p[1]: 10.0}, dup_window=600.0)
    assert groups == []


# ── cohesion split: single-linkage chains must not merge a whole shoot ────────

def _ang(deg):
    r = np.deg2rad(deg)
    return np.array([np.cos(r), np.sin(r)])


def test_cohesion_splits_a_chain_into_tight_pairs():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    # Four frames fanned 0/20/40/60 deg: each neighbour pair is cos20=0.94 (>=
    # dup_sim, so single-linkage chains all four), but the ends are cos60=0.50.
    embs = {p[0]: _ang(0), p[1]: _ang(20), p[2]: _ang(40), p[3]: _ang(60)}
    # Mutually distant hashes (>6) so phash neither unions nor pins anything.
    hashes = {p[0]: imagehash.hex_to_hash("0000000000000000"),
              p[1]: imagehash.hex_to_hash("ffff000000000000"),
              p[2]: imagehash.hex_to_hash("0000ffff00000000"),
              p[3]: imagehash.hex_to_hash("00000000ffff0000")}
    times = {x: 0.0 for x in p}
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times=times, dup_window=600.0, dup_cohesion=0.90)
    # The chain shatters into two cohesive pairs; the dissimilar ends never share.
    assert len(groups) == 2
    assert path_to_group[p[0]] == path_to_group[p[1]]
    assert path_to_group[p[2]] == path_to_group[p[3]]
    assert path_to_group[p[0]] != path_to_group[p[3]]


def test_cohesion_keeps_a_tight_burst_whole():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg", "c.jpg")
    # Three frames within 6 deg — every pair well above the cohesion floor.
    embs = {p[0]: _ang(0), p[1]: _ang(3), p[2]: _ang(6)}
    hashes = {p[0]: imagehash.hex_to_hash("0000000000000000"),
              p[1]: imagehash.hex_to_hash("ffff000000000000"),
              p[2]: imagehash.hex_to_hash("0000ffff00000000")}
    times = {x: 0.0 for x in p}
    _, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times=times, dup_window=600.0, dup_cohesion=0.90)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_standalone_weak_pair_survives_cohesion():
    imagehash = pytest.importorskip("imagehash")
    p = _paths("a.jpg", "b.jpg")
    # A lone pair linked at cos ~0.93 (like the DSCF2005_1/2007_1 case): below
    # dup_sim's stricter cousins but above the 0.90 floor -> must stay grouped.
    embs = {p[0]: _ang(0), p[1]: _ang(21)}   # cos21 ~ 0.934
    hashes = {p[0]: imagehash.hex_to_hash("0000000000000000"),
              p[1]: imagehash.hex_to_hash("ffff000000000000")}
    path_to_group, groups = photo_audit.assign_dup_groups(
        p, hashes, threshold=6, embeddings=embs, dup_sim=0.92,
        times={x: 0.0 for x in p}, dup_window=600.0, dup_cohesion=0.90)
    assert len(groups) == 1
    assert path_to_group[p[0]] == path_to_group[p[1]] == 0


def test_dup_centrality_marks_the_medoid():
    p = _paths("a.jpg", "b.jpg", "c.jpg")
    # b sits between a and c -> it's the most central frame.
    embs = {p[0]: _ang(0), p[1]: _ang(8), p[2]: _ang(16)}
    central = photo_audit.dup_centrality([[p[0], p[1], p[2]]], embs)
    assert set(central) == set(p)
    assert central[p[1]] > central[p[0]]
    assert central[p[1]] > central[p[2]]


def test_dup_centrality_empty_without_embeddings():
    p = _paths("a.jpg", "b.jpg")
    assert photo_audit.dup_centrality([[p[0], p[1]]], None) == {}


# ── coarsen_scenes_for_dups: scenes must contain dup groups ───────────────────

def test_coarsen_merges_scenes_spanned_by_a_dup():
    p = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    scene_of = {p[0]: 0, p[1]: 0, p[2]: 1, p[3]: 1}
    # A near-dup group spans scenes 0 and 1 -> both must collapse into one scene.
    scene_assign, n = photo_audit.coarsen_scenes_for_dups(
        p, scene_of, dup_groups=[[p[1], p[2]]])
    assert n == 1
    assert len({scene_assign[x] for x in p}) == 1
    assert all(scene_assign[x] is not None for x in p)


def test_coarsen_pulls_singleton_into_scene():
    p = _paths("a.jpg", "b.jpg", "c.jpg")
    # c started as a lone (None) scene but is a near-dup of b -> joins b's scene.
    scene_of = {p[0]: 0, p[1]: 0, p[2]: None}
    scene_assign, n = photo_audit.coarsen_scenes_for_dups(
        p, scene_of, dup_groups=[[p[1], p[2]]])
    assert n == 1
    assert scene_assign[p[0]] == scene_assign[p[1]] == scene_assign[p[2]] == 0


def test_coarsen_leaves_unrelated_scenes_separate():
    p = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    scene_of = {p[0]: 0, p[1]: 0, p[2]: 1, p[3]: 1}
    # No dup bridges them -> two distinct scenes survive.
    scene_assign, n = photo_audit.coarsen_scenes_for_dups(p, scene_of, dup_groups=[])
    assert n == 2
    assert scene_assign[p[0]] == scene_assign[p[1]]
    assert scene_assign[p[2]] == scene_assign[p[3]]
    assert scene_assign[p[0]] != scene_assign[p[2]]
