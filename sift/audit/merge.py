"""Merge per-root analyze reports into one library report.

Each library root is analyzed independently into its own `audit_report.json`
(local duplicate/scene grouping, per-folder cached scores). This step stitches
those per-root reports into the single combined report that `sift index`
(build_db) ingests, doing the three things that only make sense library-wide:

  1. Dense global renumbering of dup_group / scene_group / face cluster_id, so
     ids from different roots never collide (each root numbers from 0).
  2. A fixed, persisted sharpness basis, so per-image sharpness/combined scores
     are comparable across folders and stay put across rescans instead of
     drifting with each set's own min/max.
  3. Global face clustering over the content-hash-keyed embedding cache, so the
     same person is one cluster across the whole library — without re-reading a
     single pixel.

Everything here is a pure data transform over JSON + the embedding sidecar: no
models, no GPU, no image decode. That's what makes a rescan/move cheap.
"""
import json
from pathlib import Path

from .scoring import sharpness_basis, normalise_with_basis
from .embed_store import EmbedStore, bbox_key
from .faces import cluster_embeddings


def _primary_aesthetic(rec: dict) -> float:
    """The aesthetic term used in `combined`, mirroring cli.py's precedence:
    PARA head, then CLIP-IQA, then a flat 0.5 when neither was scored."""
    if rec.get("para_aesthetic") is not None:
        return rec["para_aesthetic"]
    if rec.get("clip_iqa") is not None:
        return rec["clip_iqa"]
    if rec.get("aesthetic") is not None:
        return rec["aesthetic"]
    return 0.5


def _renumber(images: list[tuple[int, dict]], key: str) -> int:
    """Densely remap a grouping column to globally-unique ids while preserving
    each source report's grouping. `images` is a list of (report_index, rec)
    in report order. Returns the number of distinct global ids assigned."""
    maps: dict[int, dict] = {}
    counter = 0
    for ri, rec in images:
        val = rec.get(key)
        if val is None:
            continue
        per_report = maps.setdefault(ri, {})
        if val not in per_report:
            per_report[val] = counter
            counter += 1
        rec[key] = per_report[val]
    return counter


def _recluster_faces(images: list[tuple[int, dict]], store: EmbedStore,
                     eps: float, ref_embeddings: dict | None):
    """Re-cluster every face in the library from cached embeddings.

    Faces whose embedding is in the store are clustered globally (one id per
    person across all roots). Faces with no cached embedding (e.g. a stale
    pre-embedding report) can't be placed globally, so each source report's
    local clusters are kept but offset into a private id range — distinct people
    stay distinct, they just won't merge across folders until re-analyzed.

    Returns (n_clusters, n_embedded, n_unembedded)."""
    embedded: list = []          # (img_idx, face, emb)
    orphan: list = []            # (img_idx, face, report_idx, orig_cid)
    for img_idx, (ri, rec) in enumerate(images):
        faces = rec.get("faces")
        if not faces:
            continue
        chash = rec.get("content_hash")
        cached = store.get_faces(chash) if chash else {}
        for face in faces:
            emb = cached.get(bbox_key(face["bbox"]))
            if emb is not None:
                embedded.append((img_idx, face, emb))
            else:
                orphan.append((img_idx, face, ri, face.get("cluster_id", -1)))

    # Capture per-face names before re-clustering overwrites cluster_id, so any
    # --face-ref names baked into the per-root reports can be carried onto the
    # new global clusters by majority vote (no model reload needed).
    prior_names = [face.get("name") for _, face, _ in embedded]

    labels, cluster_names = cluster_embeddings(
        [e for _, _, e in embedded], eps=eps, ref_embeddings=ref_embeddings)
    n_global = (max(labels) + 1) if labels else 0

    # Fall back to the most common pre-merge name within each cluster when no
    # reference matched it directly.
    from collections import Counter
    votes: dict[int, Counter] = {}
    for cid, name in zip(labels, prior_names):
        if cid >= 0 and name:
            votes.setdefault(cid, Counter())[name] += 1
    for cid, counter in votes.items():
        cluster_names.setdefault(cid, counter.most_common(1)[0][0])

    for (_, face, _), cid in zip(embedded, labels):
        face["cluster_id"] = cid
        face["name"] = cluster_names.get(cid)

    # Park orphan faces above the global id range, grouped by their source
    # report's local cluster so per-folder identity survives (names kept as-is).
    offset_map: dict[tuple, int] = {}
    next_id = n_global
    for _, face, ri, orig in orphan:
        if orig < 0:
            face["cluster_id"] = next_id
            next_id += 1
            continue
        gk = (ri, orig)
        if gk not in offset_map:
            offset_map[gk] = next_id
            next_id += 1
        face["cluster_id"] = offset_map[gk]

    n_clusters = len({f["cluster_id"] for _, rec in images
                      for f in rec.get("faces", []) if f["cluster_id"] >= 0})
    return n_clusters, len(embedded), len(orphan)


def merge_reports(report_paths, embed_store_path, out_path,
                  eps: float = 0.50, ref_embeddings: dict | None = None,
                  recalibrate_sharpness: bool = False, verbose: bool = True):
    """Combine per-root reports at `report_paths` into one report at `out_path`.

    `embed_store_path` is the shared content-hash embedding cache that every root
    wrote to during analyze; it supplies the face vectors for global clustering.
    `ref_embeddings` is an optional {name: unit_vector} map for naming clusters
    (already embedded — merge stays model-free). The sharpness basis is reused
    from a prior `out_path` unless `recalibrate_sharpness` is set, so scores
    don't drift across rescans.

    Returns the combined report dict (also written to `out_path`)."""
    paths = [Path(p) for p in report_paths]
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]

    # (report_index, record) — copy records so we never mutate inputs on disk.
    images: list[tuple[int, dict]] = []
    for ri, rep in enumerate(reports):
        for rec in rep.get("images", []):
            images.append((ri, dict(rec)))

    if verbose:
        print(f"Merging {len(reports)} report(s) → {len(images)} images")

    n_dup = _renumber(images, "dup_group")
    n_scene = _renumber(images, "scene_group")
    if verbose:
        print(f"  Renumbered: {n_dup} dup groups, {n_scene} scene groups")

    # ── Global sharpness basis (fixed across rescans) ────────────────────────
    raws = [float(rec.get("raw_laplacian") or 0.0) for _, rec in images]
    prior = None
    if not recalibrate_sharpness and Path(out_path).exists():
        try:
            prev = json.loads(Path(out_path).read_text(encoding="utf-8"))
            prior = prev.get("sharpness_basis")
        except (OSError, ValueError):
            prior = None
    if prior and len(prior) == 2:
        lo, hi = float(prior[0]), float(prior[1])
        basis_src = "reused"
    else:
        lo, hi = sharpness_basis(raws)
        basis_src = "computed"
    sharp = normalise_with_basis(raws, lo, hi)
    for (_, rec), s in zip(images, sharp):
        rec["sharpness"] = round(s, 4)
        rec["combined"] = round(0.4 * s + 0.6 * _primary_aesthetic(rec), 4)
        if "combined_clip_iqa" in rec and rec.get("clip_iqa") is not None:
            rec["combined_clip_iqa"] = round(0.4 * s + 0.6 * rec["clip_iqa"], 4)
        if "combined_para" in rec and rec.get("para_aesthetic") is not None:
            rec["combined_para"] = round(0.4 * s + 0.6 * rec["para_aesthetic"], 4)
    if verbose:
        print(f"  Sharpness basis ({basis_src}): lo={lo:.4f} hi={hi:.4f}")

    # ── Global face clustering from the embedding cache ───────────────────────
    n_clusters = n_faces_images = 0
    with EmbedStore(embed_store_path) as store:
        any_faces = any(rec.get("faces") for _, rec in images)
        if any_faces:
            n_clusters, n_emb, n_orphan = _recluster_faces(
                images, store, eps, ref_embeddings)
            n_faces_images = sum(1 for _, rec in images if rec.get("faces"))
            if verbose:
                print(f"  Face clusters: {n_clusters} "
                      f"({n_emb} embedded, {n_orphan} orphan faces)")

    records = [rec for _, rec in images]
    records.sort(key=lambda r: r["combined"])

    base = reports[0] if reports else {}
    combined = {
        "folders":          [rep.get("folder") for rep in reports],
        "folder":           "; ".join(str(rep.get("folder")) for rep in reports),
        "backend":          base.get("backend"),
        "caption_model":    base.get("caption_model"),
        "face_model":       base.get("face_model"),
        "face_expr_model":  base.get("face_expr_model"),
        "scene_model":      base.get("scene_model"),
        "total_images":     len(records),
        "duplicate_groups": n_dup,
        "scene_groups":     n_scene,
        "faces_images":     n_faces_images or None,
        "face_clusters":    n_clusters or None,
        "sharpness_basis":  [lo, hi],
        "images":           records,
    }
    Path(out_path).write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"Merged report saved: {out_path}")
    return combined
