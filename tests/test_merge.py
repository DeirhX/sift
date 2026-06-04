"""Unit tests for the per-root report merge step (sift.audit.merge).

All model-free: face clustering runs over synthetic embeddings written straight
into the content-hash embedding cache, group renumbering and the sharpness basis
are pure data transforms over JSON.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from sift.audit.merge import merge_reports, _renumber, _primary_aesthetic
from sift.audit.embed_store import EmbedStore


def _img(path, chash, *, raw=100.0, dup=None, scene=None, para=0.6, faces=None):
    rec = {
        "path": path, "filename": Path(path).name,
        "content_hash": chash, "raw_laplacian": raw,
        "para_aesthetic": para, "dup_group": dup, "scene_group": scene,
        "sharpness": 0.0, "combined": 0.0,
    }
    if faces is not None:
        rec["faces"] = faces
    return rec


def _write_report(path, folder, images):
    path.write_text(json.dumps({
        "folder": folder, "backend": "clip-b32",
        "face_model": "mtcnn+vggface2", "scene_model": "exif+clip-b32",
        "images": images,
    }), encoding="utf-8")
    return path


# ── dense global renumbering ──────────────────────────────────────────────────

def test_renumber_makes_group_ids_globally_unique_and_dense():
    # Two reports, each numbering its own groups from 0 — must not collide.
    images = [
        (0, {"dup_group": 0}), (0, {"dup_group": 0}), (0, {"dup_group": 1}),
        (1, {"dup_group": 0}), (1, {"dup_group": 1}), (1, {"dup_group": None}),
    ]
    n = _renumber(images, "dup_group")
    assert n == 4
    ids = [rec["dup_group"] for _, rec in images]
    assert ids == [0, 0, 1, 2, 3, None]            # dense, per-report preserved


def test_renumber_ignores_none():
    images = [(0, {"scene_group": None}), (0, {"scene_group": None})]
    assert _renumber(images, "scene_group") == 0
    assert all(rec["scene_group"] is None for _, rec in images)


# ── primary aesthetic precedence (mirrors cli.py) ─────────────────────────────

def test_primary_aesthetic_precedence():
    assert _primary_aesthetic({"para_aesthetic": 0.8, "clip_iqa": 0.2}) == 0.8
    assert _primary_aesthetic({"clip_iqa": 0.3}) == 0.3
    assert _primary_aesthetic({"aesthetic": 0.4}) == 0.4
    assert _primary_aesthetic({}) == 0.5


# ── sharpness basis: comparable across folders, stable across rescans ─────────

def test_sharpness_uses_one_global_basis(tmp_path):
    r1 = _write_report(tmp_path / "a.json", str(tmp_path / "A"),
                       [_img("A/1.jpg", "h1", raw=10.0),
                        _img("A/2.jpg", "h2", raw=1000.0)])
    r2 = _write_report(tmp_path / "b.json", str(tmp_path / "B"),
                       [_img("B/1.jpg", "h3", raw=100.0)])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1, r2], tmp_path / ".embeddings.sqlite", out, verbose=False)

    by_hash = {im["content_hash"]: im for im in rep["images"]}
    # The global min/max anchor 0 and 1; the middle folder's value sits between.
    assert by_hash["h1"]["sharpness"] == pytest.approx(0.0)
    assert by_hash["h2"]["sharpness"] == pytest.approx(1.0)
    assert 0.0 < by_hash["h3"]["sharpness"] < 1.0
    assert rep["sharpness_basis"][0] < rep["sharpness_basis"][1]


def test_sharpness_basis_is_reused_on_rescan(tmp_path):
    out = tmp_path / "merged.json"
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0),
                        _img("A/2.jpg", "h2", raw=1000.0)])
    first = merge_reports([r1], tmp_path / ".embeddings.sqlite", out, verbose=False)
    basis = first["sharpness_basis"]

    # Rescan adds a brighter-than-max image; with the basis frozen it clamps to 1
    # and the basis itself does not move (existing scores stay put).
    r2 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0),
                        _img("A/2.jpg", "h2", raw=1000.0),
                        _img("A/3.jpg", "h3", raw=50000.0)])
    second = merge_reports([r2], tmp_path / ".embeddings.sqlite", out, verbose=False)
    assert second["sharpness_basis"] == basis
    by_hash = {im["content_hash"]: im for im in second["images"]}
    assert by_hash["h3"]["sharpness"] == pytest.approx(1.0)   # clamped, not rescaled


def test_recalibrate_sharpness_recomputes_basis(tmp_path):
    out = tmp_path / "merged.json"
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0),
                        _img("A/2.jpg", "h2", raw=1000.0)])
    first = merge_reports([r1], tmp_path / ".embeddings.sqlite", out, verbose=False)
    r2 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0),
                        _img("A/2.jpg", "h2", raw=1000.0),
                        _img("A/3.jpg", "h3", raw=50000.0)])
    second = merge_reports([r2], tmp_path / ".embeddings.sqlite", out,
                           recalibrate_sharpness=True, verbose=False)
    assert second["sharpness_basis"] != first["sharpness_basis"]


def test_combined_uses_recomputed_sharpness(tmp_path):
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0, para=0.5),
                        _img("A/2.jpg", "h2", raw=1000.0, para=0.5)])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1], tmp_path / ".embeddings.sqlite", out, verbose=False)
    by_hash = {im["content_hash"]: im for im in rep["images"]}
    # combined = 0.4*sharp + 0.6*para. h1 sharp=0 -> 0.3 ; h2 sharp=1 -> 0.7.
    assert by_hash["h1"]["combined"] == pytest.approx(0.3)
    assert by_hash["h2"]["combined"] == pytest.approx(0.7)


# ── global face clustering across roots ───────────────────────────────────────

def _face(bbox, cid=-1, name=None):
    return {"bbox": bbox, "prob": 0.99, "cluster_id": cid, "name": name, "sharp": 0.5}


def test_same_person_merges_across_roots(tmp_path):
    pytest.importorskip("sklearn")
    store_path = tmp_path / ".embeddings.sqlite"
    # Person A vectors (near [1,0,...]) live in both roots; person B in root 2.
    a1 = np.array([1.0, 0.02, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    a2 = np.array([0.98, 0.0, 0.01, 0, 0, 0, 0, 0], dtype=np.float32)
    b1 = np.array([0.0, 0.0, 0.0, 1.0, 0, 0, 0, 0], dtype=np.float32)
    with EmbedStore(store_path) as st:
        st.put_faces("h1", [([0, 0, 10, 10], a1)])
        st.put_faces("h2", [([0, 0, 10, 10], a2)])
        st.put_faces("h3", [([0, 0, 10, 10], b1)])

    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", faces=[_face([0, 0, 10, 10], cid=0)])])
    r2 = _write_report(tmp_path / "b.json", "B",
                       [_img("B/1.jpg", "h2", faces=[_face([0, 0, 10, 10], cid=0)]),
                        _img("B/2.jpg", "h3", faces=[_face([0, 0, 10, 10], cid=1)])])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1, r2], store_path, out, verbose=False)

    by_hash = {im["content_hash"]: im for im in rep["images"]}
    ca = by_hash["h1"]["faces"][0]["cluster_id"]
    cb = by_hash["h2"]["faces"][0]["cluster_id"]
    cc = by_hash["h3"]["faces"][0]["cluster_id"]
    assert ca == cb                # same person across roots -> one cluster
    assert ca != cc                # different person -> different cluster
    assert {ca, cc} == {0, 1}      # dense ids
    assert rep["face_clusters"] == 2


def test_face_names_carry_over_by_majority_vote(tmp_path):
    pytest.importorskip("sklearn")
    store_path = tmp_path / ".embeddings.sqlite"
    a1 = np.array([1.0, 0.02, 0, 0], dtype=np.float32)
    a2 = np.array([0.98, 0.0, 0.01, 0], dtype=np.float32)
    with EmbedStore(store_path) as st:
        st.put_faces("h1", [([0, 0, 10, 10], a1)])
        st.put_faces("h2", [([0, 0, 10, 10], a2)])
    # One root tagged the person "Alice" via --face-ref; the other left it blank.
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1",
                             faces=[_face([0, 0, 10, 10], cid=0, name="Alice")])])
    r2 = _write_report(tmp_path / "b.json", "B",
                       [_img("B/1.jpg", "h2", faces=[_face([0, 0, 10, 10], cid=0)])])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1, r2], store_path, out, verbose=False)
    names = {im["faces"][0]["name"] for im in rep["images"]}
    assert names == {"Alice"}      # carried onto the merged cluster for both


def test_orphan_faces_without_embeddings_stay_distinct(tmp_path):
    pytest.importorskip("sklearn")
    store_path = tmp_path / ".embeddings.sqlite"
    # Only h1 has an embedding; h2/h3 are a stale pre-embedding report.
    with EmbedStore(store_path) as st:
        st.put_faces("h1", [([0, 0, 10, 10], np.array([1.0, 0.0], dtype=np.float32))])
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", faces=[_face([0, 0, 10, 10], cid=0)])])
    r2 = _write_report(tmp_path / "b.json", "B",
                       [_img("B/1.jpg", "h2", faces=[_face([1, 1, 9, 9], cid=0)]),
                        _img("B/2.jpg", "h3", faces=[_face([2, 2, 8, 8], cid=1)])])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1, r2], store_path, out, verbose=False)
    cids = sorted(im["faces"][0]["cluster_id"] for im in rep["images"])
    # 3 distinct clusters: 1 embedded + 2 orphan locals parked separately.
    assert len(set(cids)) == 3
    assert cids == [0, 1, 2]


# ── output shape / meta ───────────────────────────────────────────────────────

def test_merged_report_meta_and_sorting(tmp_path):
    r1 = _write_report(tmp_path / "a.json", "A",
                       [_img("A/1.jpg", "h1", raw=10.0, para=0.9, dup=0),
                        _img("A/2.jpg", "h2", raw=1000.0, para=0.1, dup=0)])
    r2 = _write_report(tmp_path / "b.json", "B",
                       [_img("B/1.jpg", "h3", raw=100.0, para=0.5, scene=0)])
    out = tmp_path / "merged.json"
    rep = merge_reports([r1, r2], tmp_path / ".embeddings.sqlite", out, verbose=False)

    assert out.exists()
    assert rep["total_images"] == 3
    assert rep["folders"] == ["A", "B"]
    assert "A" in rep["folder"] and "B" in rep["folder"]
    # Records sorted ascending by combined (worst first), matching analyze output.
    combineds = [im["combined"] for im in rep["images"]]
    assert combineds == sorted(combineds)
