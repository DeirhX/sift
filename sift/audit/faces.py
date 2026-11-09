"""Face detection, identity clustering, and per-face quality.

MTCNN detection + VGGFace2/InceptionResnetV1 embeddings + DBSCAN clustering,
plus per-face region sharpness and the optional CLIP expression-quality score.
"""
from bisect import bisect_left, bisect_right

import cv2
import numpy as np
from PIL import Image
from tqdm import tqdm

from .clip_common import load_openclip_b32, encode_prompt_pairs, bipolar_score
from .scoring import normalise_sharpness
from .imaging import load_rgb, load_upright, exif_orientation

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
# Bumping this forces a whole-library re-detection on the next analyze.
FACE_MODEL = "mtcnn+vggface2+context-v2+upright"
BURST_SECONDS = 8

# ── Acceptance tiers ─────────────────────────────────────────────────────────
# A detection is kept when ANY of these holds. The relative-size floor exists to
# keep bystanders and detector noise out, but on its own it also drops the main
# subject whenever they are framed small. Confidence and context help
# recover those detections while keeping the usual size floor.
#   direct   : prob >= min_prob and width >= min_face_rel          (unchanged)
#   small    : a near-certain detection may be much smaller
SMALL_FACE_PROB = 0.98
SMALL_FACE_REL = 0.015
#   known    : a smaller/less certain candidate that matches someone already
#              accepted in ANOTHER photo. A match in a photo taken around the
#              same time needs only the clustering threshold (they are probably
#              still there); a match anywhere in the library needs more.
#   burst    : re-detected at the spot a neighbouring burst frame saw them
KNOWN_FACE_PROB = 0.95
KNOWN_FACE_WINDOW = 600.0        # seconds: "taken around the same time"
KNOWN_FACE_SIM_NEAR = 0.50
KNOWN_FACE_SIM_ANY = 0.60
# The detector's own pixel floor, so the tiers above have candidates to judge.
# Below this an identity embedding is too unreliable to match anyone.
CANDIDATE_MIN_PX = 40


def _accept_direct(prob: float, rel: float, min_face_rel: float) -> bool:
    return rel >= min_face_rel or (prob >= SMALL_FACE_PROB and rel >= SMALL_FACE_REL)


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-8)


def _known_face_matches(candidates: dict, anchors: list, capture_times: dict) -> list:
    """Candidates that match a person already accepted in another photo.

    candidates : {path: [(box, prob, emb), ...]} — detections no tier accepted
    anchors    : [(path, emb), ...] — every accepted face so far
    Returns [(path, index into candidates[path]), ...]."""
    if not anchors:
        return []
    anchor_paths = [a[0] for a in anchors]
    embs = np.stack([_unit(a[1]) for a in anchors])
    times = np.array([capture_times.get(q) or np.nan for q in anchor_paths], dtype=float)
    out = []
    for p, dets in candidates.items():
        other = np.array([q != p for q in anchor_paths])
        if not other.any():
            continue
        t = capture_times.get(p)
        near = other & (np.abs(times - t) <= KNOWN_FACE_WINDOW) if t else np.zeros_like(other)
        for i, (_box, prob, emb) in enumerate(dets):
            if prob < KNOWN_FACE_PROB:
                continue
            cos = embs @ _unit(emb)
            if ((near.any() and cos[near].max() >= KNOWN_FACE_SIM_NEAR)
                    or cos[other].max() >= KNOWN_FACE_SIM_ANY):
                out.append((p, i))
    return out


def _burst_sources(previews, capture_times, proposals):
    """Find up to two visually close neighboring frames with face proposals."""
    anchors = sorted((p for p in proposals if p in previews), key=capture_times.__getitem__)
    times = [capture_times[p] for p in anchors]
    for p, (size, orientation, preview) in previews.items():
        time = capture_times[p]
        lo = bisect_left(times, time - BURST_SECONDS)
        hi = bisect_right(times, time + BURST_SECONDS)
        candidates = []
        for q in anchors[lo:hi]:
            other_size, other_orientation, other_preview = previews[q]
            if p == q or orientation != other_orientation:
                continue
            aspect_ratio = (size[0] * other_size[1]) / (size[1] * other_size[0])
            if not 0.95 <= aspect_ratio <= 1.05:
                continue
            difference = np.abs(preview - other_preview).mean()
            if difference <= 30:
                candidates.append((difference, abs(capture_times[q] - time), q))
        if candidates:
            candidates.sort(key=lambda item: item[:2])
            yield p, [q for _, _, q in candidates[:2]]


def _box_iou(a, b):
    intersection = (max(0, min(a[2], b[2]) - max(a[0], b[0]))
                    * max(0, min(a[3], b[3]) - max(a[1], b[1])))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def _retry_face(img, expected, anchor_embedding, detect, known_boxes, min_face_size):
    """Detect on a magnified crop, then check position and face identity."""
    x1, y1, x2, y2 = expected
    w, h = x2 - x1, y2 - y1
    left, top = max(0, int(x1 - w)), max(0, int(y1 - h))
    right, bottom = min(img.width, int(x2 + w)), min(img.height, int(y2 + h))
    if right <= left or bottom <= top or min(w, h) <= 0:
        return None
    crop = img.crop((left, top, right, bottom))
    scale = max(1, min(4, 2 * min_face_size / min(w, h)))
    enlarged = crop.resize(tuple(round(d * scale) for d in crop.size), Image.Resampling.BILINEAR)
    sx, sy = enlarged.width / crop.width, enlarged.height / crop.height

    best, best_similarity = None, 0.65
    for box, prob, emb in detect(enlarged, 0):
        restored = [max(0, box[0] / sx + left), max(0, box[1] / sy + top),
                    min(img.width, box[2] / sx + left), min(img.height, box[3] / sy + top)]
        if _box_iou(restored, expected) < 0.25:
            continue
        if any(_box_iou(restored, b) >= 0.25 for b in known_boxes):
            continue
        denom = np.linalg.norm(emb) * np.linalg.norm(anchor_embedding)
        similarity = float(emb @ anchor_embedding / denom) if denom else 0.0
        if similarity >= best_similarity:
            best, best_similarity = (restored, prob, emb), similarity
    return best


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


def run_face_expression(crops: list, device: str, batch_size: int = 32,
                        progress=None) -> list:
    """Score a list of (expanded) face crops for expression quality (0-1, higher
    = more flattering) via zero-shot CLIP ViT-B/32. Returns one float per crop.
    progress(done, total) is called after each batch, if given."""
    import torch

    print(f"\nScoring portrait expression on {len(crops)} faces (CLIP ViT-B/32)...")
    model, prep, tok = load_openclip_b32(device)
    pf, nf = encode_prompt_pairs(model, tok, EXPRESSION_PAIRS, device)

    scores: list = []
    n = len(crops)
    # crops are already-decoded PIL images, so this can't use iter_image_batches.
    for i in tqdm(range(0, n, batch_size), desc="Expression"):
        batch = crops[i:i + batch_size]
        t = torch.stack([prep(c.convert("RGB")) for c in batch]).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            feats = model.encode_image(t).cpu().float()
            feats = feats / feats.norm(dim=-1, keepdim=True)
        for fe in feats:
            scores.append(round(bipolar_score(fe, pf, nf), 4))
        if progress is not None:
            progress(min(i + batch_size, n), n)

    del model
    return scores


def run_faces(paths, device: str,
              face_refs: dict | None = None,
              min_prob: float = 0.90,
              min_face_size: int = 80,
              min_face_rel: float = 0.04,
              eps: float = 0.50,
              score_expr: bool = False,
              progress=None,
              capture_times: dict | None = None) -> tuple[dict, dict, dict]:
    """
    Detect faces, embed with InceptionResnetV1(VGGFace2), cluster with DBSCAN.

    Parameters
    ----------
    paths         : image paths to process
    device        : 'cuda' or 'cpu'
    face_refs     : {name: Path} — one reference photo per named person
    min_prob      : minimum MTCNN detection confidence (default 0.90)
    min_face_size : face width in pixels the burst re-detection magnifies a
                    missed face up to (default 80). The detector itself looks
                    down to CANDIDATE_MIN_PX so the context tiers have
                    candidates to judge.
    min_face_rel  : face width, as a fraction of image width, above which a
                    detection is accepted on its own (default 0.04). Smaller
                    faces need more evidence — see "Acceptance tiers" above.
    eps           : DBSCAN cosine-distance epsilon for identity clustering (default 0.50)
    capture_times : optional timestamps; enable the burst re-detection and the
                    "same people as the surrounding photos" prior

    Returns
    -------
    face_data  : {path: [{"bbox":[x1,y1,x2,y2], "prob":f,
                           "cluster_id":int, "name":str|None}, ...]}
    img_sizes  : {path: (width, height)}  — upright size, only for images with
                 detected faces. Boxes are in this same upright frame.
    face_embs  : {path: [(bbox, emb), ...]} — L2-normalised VGGFace2 embedding per
                 detected face, bbox matching face_data's, for persisting to the
                 content-hash embedding cache so clustering can run without re-detecting.
    """
    import torch
    from facenet_pytorch import MTCNN, InceptionResnetV1
    from sklearn.cluster import DBSCAN

    print(f"\nInitialising face detector + embedder on {device}...")
    # post_process=True → crops already normalised to [-1, 1] for InceptionResnetV1
    mtcnn  = MTCNN(keep_all=True, device=device,
                   min_face_size=min(min_face_size, CANDIDATE_MIN_PX),
                   margin=14, post_process=True)
    resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)

    # ── Phase 1: detect + embed every face ────────────────────────────────────
    # all_faces entries: (path, embedding_np, bbox, prob,
    #                     face_sharp_raw, expr_crop|None)
    all_faces: list[tuple] = []
    img_sizes: dict = {}
    n_filtered_rel = 0
    known: dict = {}        # path -> boxes accepted so far
    candidates: dict = {}   # path -> detections no tier has accepted yet
    proposals: dict = {}
    previews: dict = {}
    capture_times = capture_times or {}

    def detect(img, min_rel):
        nonlocal n_filtered_rel
        boxes, probs = mtcnn.detect(img)
        if boxes is None:
            return []
        valid = []
        for i, (box, prob) in enumerate(zip(boxes, probs)):
            if prob is None or not float(prob) >= min_prob:
                continue
            if (box[2] - box[0]) / img.width < min_rel:
                n_filtered_rel += 1
                continue
            valid.append(i)
        if not valid:
            return []
        faces = mtcnn.extract(img, boxes[valid], None)
        with torch.no_grad():
            embs = resnet(faces.to(device)).cpu().numpy()
        return [(boxes[i].tolist(), float(probs[i]), emb) for i, emb in zip(valid, embs)]

    def record(p, img, detections):
        img_sizes[p] = img.size
        for box, prob, emb in detections:
            known.setdefault(p, []).append(box)
            sharp_raw = face_laplacian_variance(img, box)
            expr_crop = expand_box(img, box) if score_expr else None
            all_faces.append((p, emb, box, prob, sharp_raw, expr_crop))

    # progress(frac, message): frac is 0..1 of the whole faces stage. Detection +
    # embedding is the bulk, so it owns 0.00–0.60; the later stages report into the
    # remainder so the message tracks what's actually running (the clustering /
    # expression steps used to run silently and looked hung).
    def _p(frac: float, message: str) -> None:
        if progress is not None:
            progress(max(0.0, min(1.0, frac)), message)

    _n_faces = len(paths)
    for _i, p in enumerate(tqdm(paths, desc="Face detection"), 1):
        _p(0.48 * _i / _n_faces, f"Detecting faces {_i}/{_n_faces}")
        try:
            # Detect on the UPRIGHT image. Neither MTCNN nor the embedder is
            # rotation-invariant, so a portrait-orientation frame left as raw
            # sensor pixels (EXIF 6/8) is a sideways face: missed, or embedded
            # as a stranger. Boxes are therefore upright coordinates too — the
            # frame the thumbnails and overlays are drawn in.
            with load_rgb(p) as source:
                orientation = exif_orientation(source)
            img = load_upright(p)
            if capture_times.get(p) and np.isfinite(capture_times[p]):
                preview = img.resize((32, 32), Image.Resampling.BOX)
                previews[p] = (img.size, orientation, np.asarray(preview, dtype=np.int16))
            detections = detect(img, 0)
            rels = [(d[0][2] - d[0][0]) / img.width for d in detections]
            accepted, rest = [], []
            for d, rel in zip(detections, rels):
                (accepted if _accept_direct(d[1], rel, min_face_rel) else rest).append(d)
            n_filtered_rel += len(rest)
            if accepted:
                record(p, img, accepted)
            # Everything else is only a candidate: it earns a place in the
            # report through a match in another photo (see the tiers above).
            if rest:
                candidates[p] = rest
            if p in previews:
                near = [d for d, rel in zip(detections, rels) if rel >= min_face_rel * 0.65]
                if near:
                    proposals[p] = near
        except Exception as e:
            print(f"  face error {p.name}: {e!r}")

    recovered = 0
    retries = list(_burst_sources(previews, capture_times, proposals))
    for i, (p, sources) in enumerate(retries, 1):
        _p(0.48 + 0.12 * i / len(retries), f"Checking burst frames {i}/{len(retries)}")
        known_boxes = known.setdefault(p, [])
        w, h = previews[p][0]
        img = None
        try:
            for q in sources:
                sw, sh = previews[q][0]
                for box, _, emb in proposals[q]:
                    expected = [box[0] * w / sw, box[1] * h / sh,
                                box[2] * w / sw, box[3] * h / sh]
                    if any(_box_iou(expected, b) >= 0.25 for b in known_boxes):
                        continue
                    if img is None:
                        img = load_upright(p)
                    found = _retry_face(img, expected, emb, detect, known_boxes, min_face_size)
                    if found is not None:
                        record(p, img, [found])
                        recovered += 1
        except Exception as e:
            print(f"  burst face error {p.name}: {e!r}")

    # Known-person pass: a candidate too small or uncertain to stand alone is
    # kept when it matches someone accepted in another photo. Repeated because
    # each acceptance is a new anchor for its own neighbours.
    matched = 0
    for _ in range(3):
        _p(0.60, "Matching small faces to known people…")
        for p in candidates:
            boxes = known.get(p, [])
            candidates[p] = [d for d in candidates[p]
                             if not any(_box_iou(d[0], b) >= 0.25 for b in boxes)]
        hits = _known_face_matches(
            candidates, [(f[0], f[1]) for f in all_faces], capture_times)
        if not hits:
            break
        by_path: dict = {}
        for p, i in hits:
            by_path.setdefault(p, []).append(i)
        for p, idxs in by_path.items():
            try:
                img = load_upright(p)
            except Exception as e:
                print(f"  face error {p.name}: {e!r}")
                candidates[p] = []
                continue
            for i in idxs:
                d = candidates[p][i]
                if not any(_box_iou(d[0], b) >= 0.25 for b in known.get(p, [])):
                    record(p, img, [d])
                    matched += 1
            candidates[p] = [d for i, d in enumerate(candidates[p]) if i not in idxs]

    if not all_faces:
        print("  No faces detected.")
        del resnet
        return {}, img_sizes, {}

    print(f"  Detected {len(all_faces)} face instances in {len(img_sizes)} images "
          f"({recovered} recovered from bursts, {matched} small faces matched to "
          f"known people, {n_filtered_rel - matched} candidates left out)")

    # ── Phase 2: DBSCAN on L2-normalised embeddings (cosine metric) ───────────
    _p(0.62, f"Clustering {len(all_faces)} face identities…")
    emb_matrix = np.array([f[1] for f in all_faces])           # (M, 512)
    norms      = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    emb_norm   = emb_matrix / np.maximum(norms, 1e-8)

    # min_samples=1 → singletons become their own cluster (label ≥ 0)
    # rather than noise (-1), so every face is visible in the viewer.
    db     = DBSCAN(eps=eps, min_samples=1, metric='cosine').fit(emb_norm)
    labels = db.labels_

    n_clusters = len(set(labels))
    print(f"  Identity clusters: {n_clusters}")

    # ── Phase 3: cluster centroids ─────────────────────────────────────────────
    cluster_ids       = [cid for cid in set(labels) if cid >= 0]
    cluster_centroids = {
        cid: emb_norm[labels == cid].mean(axis=0)
        for cid in cluster_ids
    }

    # ── Phase 4: match reference photos to cluster IDs ────────────────────────
    cluster_names: dict = {}
    if face_refs:
        _p(0.68, "Matching reference photos…")
        print("  Matching reference photos to clusters...")
        for name, ref_path in face_refs.items():
            try:
                ref_img   = load_upright(ref_path)
                ref_crops = mtcnn(ref_img)
                if ref_crops is None:
                    print(f"    No face found in reference: {ref_path.name}")
                    continue
                if ref_crops.dim() == 3:
                    ref_crops = ref_crops.unsqueeze(0)
                with torch.no_grad():
                    ref_emb = resnet(ref_crops[0:1].to(device)).cpu().numpy()[0]
                ref_norm = ref_emb / max(float(np.linalg.norm(ref_emb)), 1e-8)

                best_cid, best_dist = -1, 1.0
                for cid, centroid in cluster_centroids.items():
                    dist = 1.0 - float(ref_norm @ centroid)
                    if dist < best_dist:
                        best_dist, best_cid = dist, cid

                MATCH_THRESHOLD = 0.40
                if best_cid >= 0 and best_dist < MATCH_THRESHOLD:
                    cluster_names[best_cid] = name
                    print(f"    '{name}' → cluster {best_cid}  (dist={best_dist:.3f})")
                else:
                    print(f"    '{name}' → no match  (best dist={best_dist:.3f})")
            except Exception as e:
                print(f"    Reference error '{name}': {e}")

    # ── Phase 5: face-region sharpness + (optional) expression ─────────────────
    # Normalise sharpness across all detected faces so the score is a relative
    # 0-1 like the global image sharpness.
    _p(0.72, "Scoring face quality…")
    norm_sharp = normalise_sharpness([f[4] for f in all_faces])
    expr_scores = (
        run_face_expression(
            [f[5] for f in all_faces], device,
            progress=lambda i, n: _p(0.72 + 0.22 * (i / max(1, n)),
                                     f"Scoring expressions {i}/{n}"))
        if score_expr else [None] * len(all_faces))

    # ── Phase 6: build per-image face records + embeddings ─────────────────────
    _p(0.96, "Assembling face data…")
    face_data: dict = {}
    face_embs: dict = {}
    for idx, (p, _emb, box, prob, _sr, _ec) in enumerate(all_faces):
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
