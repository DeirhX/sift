"""
Command-line entry point for the photo audit pipeline.

Usage:
  sift analyze <folder> [options]

Options:
  --recurse             Include subfolders (default: top-level only)
  --out <path>          JSON report output path (default: <folder>/audit_report.json)
  --dup-threshold <n>   Perceptual-hash Hamming distance for duplicate grouping (default: 6)
  --backend {clip-iqa,para,both}
                        Aesthetic scoring backend (default: para)
                          clip-iqa  CLIP ViT-L/14 bipolar prompt scoring
                          para      rsinema/aesthetic-scorer — CLIP ViT-B/32 fine-tuned on
                                    the PARA dataset; 7 dimension scores (recommended)
                          both      Run both and report side-by-side
  --no-clip             Skip all aesthetic scoring (sharpness + duplicates only)
  --caption             Add natural-language captions + keyword tags
                        Caption: Salesforce/blip-image-captioning-base (~990 MB)
                        Tags:    Qwen3-VL-8B-Instruct (4-bit NF4 on GPU, ~6 GB) —
                                 a vision-language model prompted for concrete
                                 keywords; reads the scene instead of matching a
                                 fixed vocab, so it won't hallucinate absent
                                 subjects the way zero-shot CLIP did
  --top-tags <n>        Max keyword tags per image (default: 12)
  --faces               Detect + cluster faces (requires facenet-pytorch)
                        Uses MTCNN detection + VGGFace2/InceptionResnetV1 embeddings
                        + DBSCAN identity clustering.  Stores bbox, cluster_id, name.
  --face-ref NAME=PATH  Reference photo for a named person (repeatable).
                        e.g.  --face-ref "Aja=aja_reference.jpg"
                        The cluster closest to the reference (cosine dist < 0.40)
                        is automatically labelled with that name.
  --face-expr           Score portrait expression quality per face (CLIP ViT-B/32).
                        Requires --faces. Per-face sharpness is scored automatically
                        with --faces; this adds a coarse pleasant-vs-grimace score.
  --move-junk <path>    Move images scoring below --junk-threshold to this folder
  --junk-threshold <f>  Combined score threshold for --move-junk (0-1, default: 0.25)
  --top <n>             Print top-N worst images to console (default: 30)
  --no-cache            Ignore the previous report and re-score every image

Incremental re-scoring:
  The previous audit_report.json doubles as a cache. On re-run, any image whose
  (mtime, size) are unchanged AND whose cached record already has the outputs
  this run needs is reused verbatim — the heavy aesthetic/caption models only
  run on new or edited files. Sharpness normalisation and duplicate grouping are
  recomputed across the whole set (cheap, no model inference). Faces are global
  (clustering spans all images), so if anything changed they are re-detected for
  the whole folder; an unchanged folder reuses cached faces too.

Scores (all 0-1, higher = better):
  sharpness        Normalised Laplacian variance (blur detection)
  clip_iqa         CLIP-IQA bipolar prompt score (5 pairs averaged)
  para_aesthetic   PARA aesthetic head / 5
  para_*           PARA quality/composition/light/color/dof/content heads / 5
  combined         0.4 * sharpness + 0.6 * primary_aesthetic
                   (primary = PARA aesthetic if available, else CLIP-IQA)

Per-face scores (with --faces; stored on each face in the report):
  sharp            Face-region Laplacian variance, normalised across all faces
  expr             Portrait expression quality (with --face-expr); 0-1, higher
                   = more flattering (pleasant vs awkward/grimace)
  build_db aggregates the largest face per image into image-level
  face_sharp / face_expr / portrait columns for sorting and filtering.
"""
import sys
import json
import argparse
import shutil
from pathlib import Path

from tqdm import tqdm

from .scoring import (laplacian_variance, normalise_sharpness,
                      run_clip_iqa, run_para)
from .tagging import run_caption_and_tags
from .grouping import (compute_phashes, assign_dup_groups, dup_centrality,
                       coarsen_scenes_for_dups, group_scenes, read_capture_time,
                       compute_clip_embeddings)
from .faces import run_faces
from .hashing import content_hash as compute_content_hash
from .embed_store import EmbedStore

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif'}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--recurse",        action="store_true")
    ap.add_argument("--out",            default=None)
    ap.add_argument("--embed-store",    default=None,
                    help="Shared content-hash embedding cache path. Point every "
                         "root at one library-level store so the merge step can "
                         "re-cluster faces/scenes across folders without "
                         "recompute (default: <out_dir>/.embeddings.sqlite)")
    ap.add_argument("--dup-threshold",  type=int,   default=6)
    ap.add_argument("--dup-sim",        type=float, default=0.92, metavar="F",
                    help="CLIP cosine to treat two shots as near-duplicates "
                         "(default: 0.92; catches what phash misses)")
    ap.add_argument("--dup-window",     type=float, default=10.0, metavar="MIN",
                    help="Max minutes apart for a CLIP-based near-duplicate "
                         "match (default: 10)")
    ap.add_argument("--dup-cohesion",   type=float, default=0.90, metavar="F",
                    help="Min average CLIP cosine to keep a near-duplicate "
                         "group whole; looser components are split so single-"
                         "linkage chains can't merge a whole shoot (default: 0.90)")
    ap.add_argument("--no-scenes",      action="store_true",
                    help="Skip rough scene grouping (only fine near-dup groups)")
    ap.add_argument("--scene-time-gap", type=float, default=60.0, metavar="MIN",
                    help="Minutes between shots that always starts a new scene (default: 60)")
    ap.add_argument("--scene-small-gap", type=float, default=2.0, metavar="MIN",
                    help="Below this gap (minutes) shots always stay in one scene (default: 2)")
    ap.add_argument("--scene-sim",      type=float, default=0.85, metavar="F",
                    help="CLIP cosine similarity for 'same scene' (default: 0.85)")
    ap.add_argument("--backend",        choices=["clip-iqa", "para", "both"],
                    default="para",
                    help="Aesthetic scoring backend (default: para)")
    ap.add_argument("--no-clip",        action="store_true",
                    help="Skip all aesthetic scoring")
    ap.add_argument("--caption",        action="store_true",
                    help="Add BLIP captions + Qwen3-VL keyword tags (slower)")
    ap.add_argument("--top-tags",       type=int, default=12,
                    help="Max keyword tags per image (default: 12)")
    ap.add_argument("--faces",          action="store_true",
                    help="Detect + cluster faces (requires facenet-pytorch)")
    ap.add_argument("--face-ref",       action="append", default=[], metavar="NAME=PATH",
                    help="Named reference photo, e.g. --face-ref 'Aja=photo.jpg'")
    ap.add_argument("--face-min-size",  type=int,   default=80, metavar="PX",
                    help="Min face width in pixels for MTCNN (default: 80)")
    ap.add_argument("--face-min-rel",   type=float, default=0.04, metavar="F",
                    help="Min face width as fraction of image width (default: 0.04)")
    ap.add_argument("--face-eps",       type=float, default=0.50, metavar="F",
                    help="DBSCAN cosine-distance epsilon for clustering (default: 0.50)")
    ap.add_argument("--face-expr",      action="store_true",
                    help="Score portrait expression quality per face (CLIP ViT-B/32). "
                         "Requires --faces. Face-region sharpness is always scored with --faces.")
    ap.add_argument("--move-junk",      default=None)
    ap.add_argument("--junk-threshold", type=float, default=0.25)
    ap.add_argument("--top",            type=int,   default=30)
    ap.add_argument("--no-cache",       action="store_true",
                    help="Ignore the previous report; re-score every image")
    ap.add_argument("--progress-json",  action="store_true",
                    help=argparse.SUPPRESS)
    return ap


def main():
    args = build_parser().parse_args()

    def emit_progress(phase: str, pct: float, message: str,
                      current: int | None = None, total: int | None = None):
        if not args.progress_json:
            return
        print("SIFT_PROGRESS " + json.dumps({
            "phase": phase, "pct": pct, "message": message,
            "current": current, "total": total,
        }), flush=True)

    folder = Path(args.folder)
    if not folder.exists():
        print(f"Error: {folder} does not exist"); sys.exit(1)

    glob = "**/*" if args.recurse else "*"
    paths = [p for p in folder.glob(glob)
             if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()]
    print(f"Found {len(paths)} images in {folder}")
    emit_progress("scan", 0.03, f"Found {len(paths)} images", 0, len(paths))
    if not paths:
        sys.exit(0)

    use_clip_iqa = not args.no_clip and args.backend in ("clip-iqa", "both")
    use_para     = not args.no_clip and args.backend in ("para",     "both")
    use_scenes   = not args.no_scenes

    out_path = Path(args.out) if args.out else folder / "audit_report.json"

    # ── Incremental cache ────────────────────────────────────────────────────
    # The previous report doubles as the cache: reuse a record verbatim when the
    # file's (mtime, size) are unchanged and it already holds every output this
    # run needs, so the heavy models only touch new/edited files.
    import imagehash

    prev_by_path: dict = {}
    prev_by_hash: dict = {}
    if not args.no_cache and out_path.exists():
        try:
            with open(out_path, encoding="utf-8") as f:
                prev = json.load(f)
            # Only trust the cache when it was produced with the same scoring
            # configuration, so reused records have exactly the expected shape.
            cur_cfg = ("none" if args.no_clip else args.backend,
                       bool(args.caption), bool(args.faces), bool(args.face_expr),
                       bool(use_scenes))
            prev_cfg = (prev.get("backend"),
                        prev.get("caption_model") is not None,
                        prev.get("face_model") is not None,
                        prev.get("face_expr_model") is not None,
                        prev.get("scene_model") is not None)
            if prev_cfg == cur_cfg:
                prev_by_path = {r["path"]: r for r in prev.get("images", [])}
                # Content-hash index: lets a file that moved/renamed reuse its
                # record by identity (same bytes) even though its path changed.
                prev_by_hash = {r["content_hash"]: r for r in prev.get("images", [])
                                if r.get("content_hash")}
            else:
                print(f"  (config changed {prev_cfg} -> {cur_cfg}; re-scoring all)")
        except Exception as e:
            print(f"  (cache unreadable, re-scoring all: {e})")

    sigs: dict = {}
    for p in paths:
        try:
            st = p.stat()
            sigs[p] = (st.st_mtime, st.st_size)
        except OSError:
            sigs[p] = (None, None)

    def _outputs_ok(prev) -> bool:
        """True when a cached record carries every output this run needs — the
        identity-level check shared by path reuse and content-hash reuse."""
        if not prev or "phash" not in prev:
            return False
        if use_para     and "para_aesthetic" not in prev: return False
        if use_clip_iqa and "clip_iqa"       not in prev: return False
        if args.caption and "caption"        not in prev: return False
        if args.faces   and "faces"          not in prev: return False
        if use_scenes   and "scene_group"    not in prev: return False
        return True

    def reusable(p: Path):
        prev = prev_by_path.get(str(p))
        if not _outputs_ok(prev) or prev.get("mtime") is None:
            return None
        mt, sz = sigs[p]
        if sz is None or prev.get("fsize") != sz or abs(prev["mtime"] - mt) > 1e-6:
            return None
        return prev

    cached: dict = {}
    to_process: list = []
    for p in paths:
        prev = reusable(p)
        (cached.__setitem__(p, prev) if prev is not None else to_process.append(p))

    # Content hash (stable identity) for every image: reuse from an unchanged
    # path-cached record, else hash the bytes. Persisted in the report so build_db
    # can skip re-hashing, and used to key the embedding cache below.
    chash: dict = {}
    for p in paths:
        prev = cached.get(p)
        h = prev.get("content_hash") if prev else None
        chash[p] = h or compute_content_hash(p)

    # Content-hash reuse: a file that's new *by path* may be a move/rename of one
    # we already scored. Recover its record by identity so moves cost nothing —
    # only genuinely new/changed bytes fall through to the models.
    if prev_by_hash:
        recovered = []
        for p in to_process:
            prev = prev_by_hash.get(chash[p])
            if _outputs_ok(prev):
                cached[p] = prev
                recovered.append(p)
        if recovered:
            rec_set = set(recovered)
            to_process = [p for p in to_process if p not in rec_set]
            print(f"  Recovered {len(recovered)} moved/renamed file(s) from cache "
                  f"by content hash")

    print(f"\nIncremental: {len(cached)} cached, {len(to_process)} to score "
          f"(of {len(paths)})")

    # Content-hash-keyed embedding cache. Defaults to a sidecar next to the
    # report, but a multi-root library points every root at one shared store
    # (--embed-store) so the merge step can re-cluster across folders. Optional:
    # if it can't be opened we simply recompute everything as before.
    store_path = (Path(args.embed_store) if args.embed_store
                  else out_path.parent / ".embeddings.sqlite")
    try:
        store = EmbedStore(store_path)
    except Exception as e:
        print(f"  (embedding cache unavailable, will recompute: {e})")
        store = None

    def device_for() -> str:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"

    # ── Sharpness (raw reused from cache; only new files read from disk) ──
    print("\nComputing sharpness (Laplacian variance)...")
    raw_sharp: dict = {p: cached[p].get("raw_laplacian", 0.0) for p in cached}
    for p in tqdm(to_process, desc="Sharpness"):
        raw_sharp[p] = laplacian_variance(p)
    norm_sharp = normalise_sharpness([raw_sharp[p] for p in paths])
    sharpness  = {p: s for p, s in zip(paths, norm_sharp)}
    emit_progress("sharpness", 0.15, "Sharpness complete", len(paths), len(paths))

    # ── CLIP-IQA / PARA (new files only) ──
    clip_iqa_scores: dict = {}
    if use_clip_iqa and to_process:
        print()
        clip_iqa_scores = run_clip_iqa(to_process, device_for())

    para_raw: dict = {}
    if use_para and to_process:
        print()
        para_raw = run_para(to_process, device_for())

    # ── Primary aesthetic for combined score (cached or freshly computed) ──
    def primary_aes(p: Path) -> float:
        if p in cached:
            if use_para:     return cached[p].get("para_aesthetic", 0.5)
            if use_clip_iqa: return cached[p].get("clip_iqa", 0.5)
            return 0.5
        if use_para:     return para_raw[p]["aesthetic"] / 5.0
        if use_clip_iqa: return clip_iqa_scores[p]
        return 0.5

    combined = {p: 0.4 * sharpness[p] + 0.6 * primary_aes(p) for p in paths}
    emit_progress("scoring", 0.35, "Scoring complete", len(paths), len(paths))

    # ── Perceptual hashes (reused from cache; only new files hashed) ──
    print("\nComputing perceptual hashes for duplicate detection...")
    hashes: dict = {}
    for p in cached:
        try:
            hashes[p] = imagehash.hex_to_hash(cached[p]["phash"])
        except Exception:
            pass
    new_hashes, new_sizes = compute_phashes(to_process) if to_process else ({}, {})
    hashes.update(new_hashes)
    img_sizes = new_sizes
    emit_progress("duplicates", 0.45, "Perceptual hashes complete", len(paths), len(paths))

    # ── Capture time (EXIF, mtime fallback; reused from cache when present) ──
    capture_time: dict = {}
    for p in paths:
        prev = cached.get(p)
        if prev is not None and prev.get("capture_time") is not None:
            capture_time[p] = prev["capture_time"]
            continue
        ct = read_capture_time(p)
        capture_time[p] = ct if ct is not None else (sigs[p][0] or 0.0)

    # ── Scene grouping + fine near-duplicate groups ──
    # Grouping is global (like face clustering): recomputed every run over the
    # whole set, even when all per-image scores came from cache, because near-dup
    # membership and scene boundaries depend on the full set. CLIP embeddings are
    # reused from the content-hash cache when available (only new/changed images
    # are embedded) and drive both "same scene" and the CLIP-aware near-dup test;
    # phash is the fallback / exact-dup signal.
    embeddings: dict | None = None
    clip_new: list = []
    if use_scenes and not args.no_clip:
        embeddings = {}
        if store is not None:
            cached_clip = store.get_clip({chash[p] for p in paths})
            embeddings = {p: cached_clip[chash[p]]
                          for p in paths if chash[p] in cached_clip}
        clip_new = [p for p in paths if p not in embeddings]
        if clip_new:
            print()
            embeddings.update(compute_clip_embeddings(clip_new, device_for()))
        print(f"  CLIP embeddings: {len(paths) - len(clip_new)} cached, "
              f"{len(clip_new)} computed")
        emit_progress("embeddings", 0.58, "CLIP embeddings complete", len(paths), len(paths))

    # 1) Near-duplicates first — the finest grain. CLIP cosine catches the
    #    "same shot, slight motion" pairs that phash (Hamming) reads as unrelated.
    path_to_group, dup_groups = assign_dup_groups(
        paths, hashes, args.dup_threshold,
        embeddings=embeddings, dup_sim=args.dup_sim,
        times=capture_time, dup_window=args.dup_window * 60.0,
        dup_cohesion=args.dup_cohesion,
    )
    # Centrality per member -> the UI leads each group with its medoid frame.
    dup_central = dup_centrality(dup_groups, embeddings)
    emit_progress("duplicates", 0.68, f"Found {len(dup_groups)} duplicate groups",
                  len(dup_groups), len(dup_groups))

    # 2) Rough scenes, then coarsen so every dup group nests inside one scene
    #    (a near-dup spanning the sequential segmenter's boundary merges them).
    if not use_scenes:
        scene_assign: dict = {p: None for p in paths}
        scene_count = 0
    else:
        scene_assign, _ = group_scenes(
            paths, capture_time,
            embeddings=embeddings, hashes=hashes,
            big_gap=args.scene_time_gap * 60.0,
            small_gap=args.scene_small_gap * 60.0,
            sim=args.scene_sim,
        )
        scene_assign, scene_count = coarsen_scenes_for_dups(
            paths, scene_assign, dup_groups, times=capture_time,
        )

    # ── BLIP captions + Qwen3-VL keyword tags (new files only) ──
    captions: dict = {}
    if args.caption and to_process:
        captions = run_caption_and_tags(to_process, device_for(),
                                         top_k=args.top_tags)

    # ── Face detection + identity clustering ──
    # Clustering is global, so any change forces a whole-folder re-detection;
    # an unchanged folder reuses cached faces.
    face_data: dict = {}
    face_embs: dict = {}
    faces_global = bool(args.faces and to_process)
    if faces_global:
        refs: dict = {}
        for item in args.face_ref:
            if "=" in item:
                name, rpath = item.split("=", 1)
                refs[name.strip()] = Path(rpath.strip())
            else:
                print(f"  Warning: --face-ref '{item}' ignored (expected NAME=PATH)")
        face_data, _, face_embs = run_faces(
            paths, device_for(),
            face_refs=refs or None,
            min_face_size=args.face_min_size,
            min_face_rel=args.face_min_rel,
            eps=args.face_eps,
            score_expr=bool(args.face_expr),
        )

    # ── Build report ──
    def stamp(rec: dict, p: Path) -> dict:
        mt, sz = sigs[p]
        rec["mtime"] = mt
        rec["fsize"] = sz
        rec["content_hash"] = chash[p]
        rec["capture_time"] = capture_time.get(p)
        if p in hashes:
            rec["phash"] = str(hashes[p])
        return rec

    records = []
    for p in paths:
        if p in cached:
            # Reuse all per-image outputs; only recompute set-relative scalars.
            # path/filename may differ from the cached record if the file moved.
            rec = dict(cached[p])
            rec["path"] = str(p)
            rec["filename"] = p.name
            rec["sharpness"] = round(sharpness[p], 4)
            rec["combined"]  = round(combined[p], 4)
            rec["dup_group"] = path_to_group.get(p)
            rec["dup_central"] = dup_central.get(p)
            rec["scene_group"] = scene_assign.get(p)
            if use_para and use_clip_iqa:
                rec["combined_clip_iqa"] = round(
                    0.4 * sharpness[p] + 0.6 * rec.get("clip_iqa", 0.5), 4)
                rec["combined_para"] = round(
                    0.4 * sharpness[p] + 0.6 * rec.get("para_aesthetic", 0.5), 4)
            if faces_global:
                rec["faces"] = face_data.get(p, [])
            records.append(stamp(rec, p))
            continue

        rec = {
            "path":          str(p),
            "filename":      p.name,
            "sharpness":     round(sharpness[p], 4),
            "combined":      round(combined[p], 4),
            "dup_group":     path_to_group.get(p),
            "dup_central":   dup_central.get(p),
            "scene_group":   scene_assign.get(p),
            "raw_laplacian": round(raw_sharp[p], 2),
        }
        # Dimensions for every image (not just faces) so the grid can lay out
        # aspect-correct tiles. Falls back to None if the image failed to open.
        w_h = img_sizes.get(p)
        if w_h:
            rec["imgw"], rec["imgh"] = w_h
        if use_clip_iqa:
            rec["clip_iqa"] = round(clip_iqa_scores.get(p, 0.5), 4)
        if use_para:
            pr = para_raw[p]
            rec["para_aesthetic"]   = round(pr["aesthetic"]   / 5.0, 4)
            rec["para_quality"]     = round(pr["quality"]     / 5.0, 4)
            rec["para_composition"] = round(pr["composition"] / 5.0, 4)
            rec["para_light"]       = round(pr["light"]       / 5.0, 4)
            rec["para_color"]       = round(pr["color"]       / 5.0, 4)
            rec["para_dof"]         = round(pr["dof"]         / 5.0, 4)
            rec["para_content"]     = round(pr["content"]     / 5.0, 4)
            if use_clip_iqa:
                rec["combined_clip_iqa"] = round(
                    0.4 * sharpness[p] + 0.6 * clip_iqa_scores[p], 4)
                rec["combined_para"]     = round(
                    0.4 * sharpness[p] + 0.6 * pr["aesthetic"] / 5.0, 4)
        if not use_clip_iqa and not use_para:
            rec["aesthetic"] = 0.5
        if args.caption and p in captions:
            rec["caption"] = captions[p].get("caption", "")
            rec["tags"]    = captions[p].get("tags", [])
        if args.faces:
            rec["faces"] = face_data.get(p, [])
        records.append(stamp(rec, p))

    records.sort(key=lambda r: r["combined"])

    with open(out_path, "w", encoding="utf-8") as f:
        n_faces_images = sum(1 for r in records if r.get("faces"))
        n_clusters = (
            len({f["cluster_id"] for r in records
                 for f in r.get("faces", []) if f["cluster_id"] >= 0})
            if args.faces else 0
        )
        json.dump({
            "folder":           str(folder),
            "backend":          "none" if args.no_clip else args.backend,
            "caption_model":    "blip-base+qwen3vl-8b-nf4" if args.caption else None,
            "face_model":       "mtcnn+vggface2" if args.faces else None,
            "face_expr_model":  "clip-b32-expr" if (args.faces and args.face_expr) else None,
            "scene_model":      (None if not use_scenes
                                 else "exif+clip-b32" if not args.no_clip
                                 else "exif+phash"),
            "total_images":     len(records),
            "duplicate_groups": len(dup_groups),
            "dup_sim":          args.dup_sim,
            "dup_window_min":   args.dup_window,
            "dup_cohesion":     args.dup_cohesion,
            "scene_groups":     scene_count,
            "faces_images":     n_faces_images if args.faces else None,
            "face_clusters":    n_clusters     if args.faces else None,
            "images":           records,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved: {out_path}")
    emit_progress("report", 0.90, "Report written", len(records), len(records))

    # ── Persist newly computed embeddings to the content-hash-keyed cache ──────
    # CLIP scene vectors and per-face VGGFace2 vectors are pixel-invariant, so
    # caching them by content hash lets future rescans/moves and the global
    # face-clustering / scene-regroup steps reuse them with zero recompute. Only
    # freshly computed vectors are written (cached ones are already stored).
    if store is not None:
        try:
            if embeddings and clip_new:
                store.put_clip((chash[p], embeddings[p])
                               for p in clip_new if p in embeddings)
            for p, items in face_embs.items():
                store.put_faces(chash[p], items)
            if clip_new or face_embs:
                print(f"  Embeddings cached: {len(clip_new)} CLIP, "
                      f"{sum(len(v) for v in face_embs.values())} faces")
        except Exception as e:
            print(f"  (embedding cache write skipped: {e})")
        finally:
            store.close()

    # ── Console summary ──
    print(f"\n{'='*80}")
    print(f"WORST {args.top} images  (sorted by combined score, lower = worse)")
    print(f"{'='*80}")

    show_caption = args.caption and any(captions.get(p, {}).get("caption") for p in paths)

    if args.backend == "both" and use_clip_iqa and use_para:
        hdr = (f"  {'comb':>6}  {'sharp':>6}  {'IQA':>6}  "
               f"{'PARA':>6}  {'qual':>6}  {'comp':>6}  {'dup':>5}  filename")
        print(hdr)
        print("  " + "  ".join(["-"*6]*6) + "  " + "-"*5 + "  " + "-"*30)
        for r in records[:args.top]:
            dup = f"G{r['dup_group']}" if r["dup_group"] is not None else ""
            print(f"  {r['combined']:>6.3f}  {r['sharpness']:>6.3f}"
                  f"  {r.get('clip_iqa',0):>6.3f}"
                  f"  {r.get('para_aesthetic',0):>6.3f}"
                  f"  {r.get('para_quality',0):>6.3f}"
                  f"  {r.get('para_composition',0):>6.3f}"
                  f"  {dup:>5}  {r['filename']}")
            if show_caption and r.get("caption"):
                tags = ", ".join(r.get("tags", [])[:6])
                print(f"  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>5}"
                      f"  \033[2m{r['caption'][:90]}\033[0m")
                if tags:
                    print(f"  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>5}"
                          f"  \033[2mtags: {tags}\033[0m")
    else:
        aes_key = ("clip_iqa"        if use_clip_iqa
                   else "para_aesthetic" if use_para
                   else "aesthetic")
        aes_lbl = "IQA" if use_clip_iqa else ("PARA" if use_para else "aesth")
        print(f"  {'score':>6}  {'sharp':>6}  {aes_lbl:>6}  {'dup':>5}  filename")
        print(f"  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*40}")
        for r in records[:args.top]:
            dup = f"G{r['dup_group']}" if r["dup_group"] is not None else ""
            print(f"  {r['combined']:>6.3f}  {r['sharpness']:>6.3f}  "
                  f"{r.get(aes_key, 0.5):>6.3f}  {dup:>5}  {r['filename']}")
            if show_caption and r.get("caption"):
                tags = ", ".join(r.get("tags", [])[:6])
                print(f"  {'':>6}  {'':>6}  {'':>6}  {'':>5}"
                      f"  \033[2m{r['caption'][:90]}\033[0m")
                if tags:
                    print(f"  {'':>6}  {'':>6}  {'':>6}  {'':>5}"
                          f"  \033[2mtags: {tags}\033[0m")

    if dup_groups:
        print(f"\n{'='*80}")
        print(f"DUPLICATE GROUPS  ({len(dup_groups)} groups — keep highest combined score)")
        print(f"{'='*80}")
        for gid, group in enumerate(dup_groups):
            ranked = sorted(group, key=lambda p: -combined[p])
            print(f"\n  Group {gid}  ({len(group)} images):")
            for p in ranked:
                marker = "KEEP" if p == ranked[0] else "del?"
                cap = ""
                if show_caption and captions.get(p, {}).get("caption"):
                    cap = "  — " + captions[p]["caption"][:60]
                print(f"    [{marker}] {combined[p]:.3f}  {p.name}{cap}")

    # ── Move junk ──
    if args.move_junk:
        junk_dir = Path(args.move_junk)
        junk_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for r in records:
            if r["combined"] < args.junk_threshold:
                src = Path(r["path"])
                dst = junk_dir / src.name
                shutil.move(str(src), str(dst))
                moved += 1
        print(f"\nMoved {moved} images scoring < {args.junk_threshold} to {junk_dir}")

    print(f"\nDone. Total: {len(records)} images, {len(dup_groups)} duplicate groups.")
    emit_progress("done", 1.0, "Analyze complete", len(records), len(records))
