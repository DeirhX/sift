"""Face detection, identity clustering, and per-face quality.

MTCNN detection + VGGFace2/InceptionResnetV1 embeddings + DBSCAN clustering,
plus per-face region sharpness and the optional CLIP expression-quality score.
"""
import cv2
import numpy as np
from tqdm import tqdm

from .clip_common import load_openclip_b32, encode_prompt_pairs, bipolar_score
from .scoring import normalise_sharpness
from .imaging import load_rgb

# ── Portrait expression bipolar prompts (CLIP ViT-B/32, on face crops) ────────
# Coarse "is this a flattering expression" signal. CLIP is weak at fine facial
# state, so these stay deliberately high-level (pleasant vs awkward/grimace).
# We intentionally do NOT probe eye state here — closed-eye detection needs
# eyelid landmarks, not CLIP.
EXPRESSION_PAIRS = [
    ("a flattering portrait with a pleasant, natural expression",
     "an unflattering portrait with an awkward, distorted expression"),
    ("a person with a relaxed, natural face",
     "a person making a strange grimace"),
    ("a nicely captured portrait of a person",
     "a person caught at a bad moment with a contorted face"),
]

# Face-crop normalisation size so Laplacian variance is comparable across faces
# of different pixel sizes (detail density, not absolute resolution).
FACE_SHARP_PX = 160


def face_laplacian_variance(pil_img, box) -> float:
    """Laplacian variance of a single face region, normalised to FACE_SHARP_PX so
    a small in-focus face isn't unfairly penalised against a large one. Operates
    on the original-resolution crop (the aligned MTCNN tensor is post-processed
    and would understate blur)."""
    w_img, h_img = pil_img.size
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w_img, x2), min(h_img, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    g = np.asarray(pil_img.crop((x1, y1, x2, y2)).convert("L"))
    h, w = g.shape
    m = max(h, w)
    if m != FACE_SHARP_PX:
        s = FACE_SHARP_PX / m
        g = cv2.resize(g, (max(1, int(w * s)), max(1, int(h * s))))
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def expand_box(pil_img, box, margin: float = 0.4):
    """Expand a face bbox by `margin` on each side (clamped to the image) so an
    expression classifier sees a bit of head/shoulders context, which CLIP reads
    better than a tight face-only crop."""
    w_img, h_img = pil_img.size
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1 = max(0, int(x1 - bw * margin)); y1 = max(0, int(y1 - bh * margin))
    x2 = min(w_img, int(x2 + bw * margin)); y2 = min(h_img, int(y2 + bh * margin))
    return pil_img.crop((x1, y1, x2, y2))


def run_face_expression(crops: list, device: str, batch_size: int = 32) -> list:
    """Score a list of (expanded) face crops for expression quality (0-1, higher
    = more flattering) via zero-shot CLIP ViT-B/32. Returns one float per crop."""
    import torch

    print(f"\nScoring portrait expression on {len(crops)} faces (CLIP ViT-B/32)...")
    model, prep, tok = load_openclip_b32(device)
    pf, nf = encode_prompt_pairs(model, tok, EXPRESSION_PAIRS, device)

    scores: list = []
    # crops are already-decoded PIL images, so this can't use iter_image_batches.
    for i in tqdm(range(0, len(crops), batch_size), desc="Expression"):
        batch = crops[i:i + batch_size]
        t = torch.stack([prep(c.convert("RGB")) for c in batch]).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            feats = model.encode_image(t).cpu().float()
            feats = feats / feats.norm(dim=-1, keepdim=True)
        for fe in feats:
            scores.append(round(bipolar_score(fe, pf, nf), 4))

    del model
    return scores


def cluster_embeddings(emb_norm, eps: float = 0.50, ref_embeddings: dict | None = None,
                       match_threshold: float = 0.40):
    """DBSCAN identity clustering over L2-normalised face embeddings.

    `emb_norm` is an (M, D) array of unit vectors (cosine space). Uses
    min_samples=1 so singletons become their own cluster (label >= 0) rather than
    noise, so every face is visible. Optionally maps named references
    (`{name: unit_vector}`) to the nearest cluster centroid within
    `match_threshold` cosine distance.

    Returns (labels: list[int], cluster_names: {cluster_id: name}). This is the
    one definition of the clustering rule, shared by per-image detection and the
    global merge-time re-clustering so the two can't drift."""
    import numpy as np
    from sklearn.cluster import DBSCAN

    if len(emb_norm) == 0:
        return [], {}
    emb_norm = np.asarray(emb_norm)
    labels = [int(x) for x in
              DBSCAN(eps=eps, min_samples=1, metric="cosine").fit(emb_norm).labels_]

    cluster_names: dict = {}
    if ref_embeddings:
        cluster_ids = sorted({c for c in labels if c >= 0})
        centroids = {cid: emb_norm[[i for i, l in enumerate(labels) if l == cid]].mean(axis=0)
                     for cid in cluster_ids}
        for name, ref in ref_embeddings.items():
            ref = np.asarray(ref, dtype=np.float32)
            n = np.linalg.norm(ref)
            if n > 0:
                ref = ref / n
            best_cid, best_dist = -1, 1.0
            for cid, c in centroids.items():
                dist = 1.0 - float(ref @ c)
                if dist < best_dist:
                    best_dist, best_cid = dist, cid
            if best_cid >= 0 and best_dist < match_threshold:
                cluster_names[best_cid] = name
    return labels, cluster_names


def run_faces(paths, device: str,
              face_refs: dict | None = None,
              min_prob: float = 0.90,
              min_face_size: int = 80,
              min_face_rel: float = 0.04,
              eps: float = 0.50,
              score_expr: bool = False,
              progress=None) -> tuple[dict, dict]:
    """
    Detect faces, embed with InceptionResnetV1(VGGFace2), cluster with DBSCAN.

    Parameters
    ----------
    paths         : image paths to process
    device        : 'cuda' or 'cpu'
    face_refs     : {name: Path} — one reference photo per named person
    min_prob      : minimum MTCNN detection confidence (default 0.90)
    min_face_size : minimum absolute face width in pixels for MTCNN (default 80)
                    Raises the floor so tiny background-crowd faces are never
                    detected in the first place.
    min_face_rel  : minimum face width as fraction of image width (default 0.04)
                    Secondary filter — discards background faces that slip through.
                    e.g. 0.04 means the face must be ≥ 4 % of the image width.
    eps           : DBSCAN cosine-distance epsilon for identity clustering (default 0.50)

    Returns
    -------
    face_data  : {path: [{"bbox":[x1,y1,x2,y2], "prob":f,
                           "cluster_id":int, "name":str|None}, ...]}
    img_sizes  : {path: (width, height)}  — only for images with detected faces
    face_embs  : {path: [(bbox, emb), ...]} — L2-normalised VGGFace2 embedding per
                 detected face, bbox matching face_data's, for persisting to the
                 content-hash embedding cache so clustering can run without re-detecting.
    """
    import torch
    from facenet_pytorch import MTCNN, InceptionResnetV1

    print(f"\nInitialising face detector + embedder on {device}...")
    # post_process=True → crops already normalised to [-1, 1] for InceptionResnetV1
    mtcnn  = MTCNN(keep_all=True, device=device,
                   min_face_size=min_face_size, margin=14, post_process=True)
    resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)

    # ── Phase 1: detect + embed every face ────────────────────────────────────
    # all_faces entries: (path, face_slot_idx, embedding_np, bbox, prob,
    #                     face_sharp_raw, expr_crop|None)
    all_faces: list[tuple] = []
    img_sizes: dict = {}
    n_filtered_rel = 0

    _n_faces = len(paths)
    for _i, p in enumerate(tqdm(paths, desc="Face detection"), 1):
        if progress is not None:
            progress(_i, _n_faces)
        try:
            img     = load_rgb(p).convert("RGB")
            w, h    = img.size
            boxes, probs = mtcnn.detect(img)
            if boxes is None:
                continue

            faces = mtcnn(img)   # (N, 3, 160, 160) float32 [-1,1], or None
            if faces is None:
                continue
            if faces.dim() == 3:          # single face → add batch dim
                faces = faces.unsqueeze(0)

            # Keep only faces that pass probability AND relative-size thresholds
            valid = []
            for i, pv in enumerate(probs):
                if pv is None or float(pv) < min_prob:
                    continue
                x1, y1, x2, y2 = boxes[i]
                face_rel = (x2 - x1) / w if w else 0
                if face_rel < min_face_rel:
                    n_filtered_rel += 1
                    continue
                valid.append(i)

            if not valid:
                continue

            img_sizes[p] = (w, h)
            batch_t = faces[valid].to(device)
            with torch.no_grad():
                embs = resnet(batch_t).cpu().numpy()   # (len(valid), 512)

            for rank, vi in enumerate(valid):
                box = boxes[vi].tolist()
                sharp_raw = face_laplacian_variance(img, box)
                expr_crop = expand_box(img, box) if score_expr else None
                all_faces.append((p, vi, embs[rank], box, float(probs[vi]),
                                  sharp_raw, expr_crop))
        except Exception as e:
            print(f"  face error {p.name}: {e}")

    if not all_faces:
        print("  No faces detected.")
        del resnet
        return {}, img_sizes, {}

    print(f"  Detected {len(all_faces)} face instances in {len(img_sizes)} images "
          f"(filtered out {n_filtered_rel} faces below rel-size {min_face_rel:.2f})")

    # ── Phase 2: embed reference photos (model) so the shared clustering helper
    #            can name clusters without re-loading anything ─────────────────
    emb_matrix = np.array([f[2] for f in all_faces])           # (M, 512)
    norms      = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    emb_norm   = emb_matrix / np.maximum(norms, 1e-8)

    ref_embeddings: dict = {}
    if face_refs:
        print("  Embedding reference photos...")
        for name, ref_path in face_refs.items():
            try:
                ref_img   = load_rgb(ref_path).convert("RGB")
                ref_crops = mtcnn(ref_img)
                if ref_crops is None:
                    print(f"    No face found in reference: {ref_path.name}")
                    continue
                if ref_crops.dim() == 3:
                    ref_crops = ref_crops.unsqueeze(0)
                with torch.no_grad():
                    ref_emb = resnet(ref_crops[0:1].to(device)).cpu().numpy()[0]
                ref_embeddings[name] = ref_emb
            except Exception as e:
                print(f"    Reference error '{name}': {e}")

    # ── Phase 3: cluster + name (single source of truth, shared with merge) ───
    labels, cluster_names = cluster_embeddings(emb_norm, eps=eps,
                                               ref_embeddings=ref_embeddings)
    print(f"  Identity clusters: {len(set(labels))}")
    for cid, name in cluster_names.items():
        print(f"    '{name}' → cluster {cid}")

    # ── Phase 5: face-region sharpness + (optional) expression ─────────────────
    # Normalise sharpness across all detected faces so the score is a relative
    # 0-1 like the global image sharpness.
    norm_sharp = normalise_sharpness([f[5] for f in all_faces])
    expr_scores = (run_face_expression([f[6] for f in all_faces], device)
                   if score_expr else [None] * len(all_faces))

    # ── Phase 6: build per-image face records + embeddings ─────────────────────
    face_data: dict = {}
    face_embs: dict = {}
    for idx, (p, _vi, _emb, box, prob, _sr, _ec) in enumerate(all_faces):
        cid  = int(labels[idx])
        name = cluster_names.get(cid) if cid >= 0 else None
        bbox = [round(v, 1) for v in box]
        rec = {
            "bbox":       bbox,
            "prob":       round(prob, 3),
            "cluster_id": cid,
            "name":       name,
            "sharp":      round(norm_sharp[idx], 4),
        }
        if expr_scores[idx] is not None:
            rec["expr"] = expr_scores[idx]
        face_data.setdefault(p, []).append(rec)
        # L2-normalised embedding (cosine space), aligned with this face's bbox.
        face_embs.setdefault(p, []).append((bbox, emb_norm[idx]))

    del resnet
    return face_data, img_sizes, face_embs
