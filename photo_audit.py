"""
photo_audit.py — Score a photo dump for sharpness + aesthetic quality,
                 group near-duplicates, and optionally caption each image.

Usage:
  python photo_audit.py <folder> [options]

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
                        Tags:    CLIP ViT-B/32 zero-shot against a curated 60-word vocab
  --top-tags <n>        Number of keyword tags to emit per image (default: 8)
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
from datetime import datetime
from pathlib import Path

# Make sure aesthetic_scorer.py is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from PIL import Image
from tqdm import tqdm

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif'}

# ── CLIP-IQA bipolar prompt pairs ────────────────────────────────────────────
QUALITY_PAIRS = [
    ("a sharp, well-focused photograph",
     "a blurry, out-of-focus photograph"),
    ("a well-composed, aesthetically pleasing photograph",
     "a poorly composed, badly framed snapshot"),
    ("a photograph with good exposure and lighting",
     "an overexposed or underexposed photograph"),
    ("a high quality professional photograph",
     "a low quality amateur snapshot"),
    ("an interesting, visually engaging photo",
     "a boring, uninteresting photo"),
]

PARA_KEYS = ["aesthetic", "quality", "composition", "light", "color", "dof", "content"]

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


# ── Sharpness ────────────────────────────────────────────────────────────────

def laplacian_variance(path: Path) -> float:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    if max(h, w) > 1920:
        scale = 1920 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


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


def _bipolar_score(img_feat, pos_feats, neg_feats) -> float:
    """Mean positive-vs-negative softmax probability across prompt pairs."""
    import torch, torch.nn.functional as F
    vals = []
    for pf, nf in zip(pos_feats, neg_feats):
        logits = torch.tensor([float(img_feat @ pf), float(img_feat @ nf)])
        vals.append(F.softmax(logits, dim=0)[0].item())
    return float(np.mean(vals))


def run_face_expression(crops: list, device: str, batch_size: int = 32) -> list:
    """Score a list of (expanded) face crops for expression quality (0-1, higher
    = more flattering) via zero-shot CLIP ViT-B/32. Returns one float per crop."""
    import torch, open_clip

    print(f"\nScoring portrait expression on {len(crops)} faces (CLIP ViT-B/32)...")
    model, _, prep = open_clip.create_model_and_transforms(
        "ViT-B-32-quickgelu", pretrained="openai", device=device)
    tok = open_clip.get_tokenizer("ViT-B-32-quickgelu")
    model.eval()

    pos = tok([f"a photo of {p}" for p, _ in EXPRESSION_PAIRS]).to(device)
    neg = tok([f"a photo of {n}" for _, n in EXPRESSION_PAIRS]).to(device)
    with torch.no_grad(), torch.amp.autocast(device):
        pf = model.encode_text(pos); pf = pf / pf.norm(dim=-1, keepdim=True)
        nf = model.encode_text(neg); nf = nf / nf.norm(dim=-1, keepdim=True)
    pf, nf = pf.cpu().float(), nf.cpu().float()

    scores: list = []
    for i in tqdm(range(0, len(crops), batch_size), desc="Expression"):
        batch = crops[i:i + batch_size]
        t = torch.stack([prep(c.convert("RGB")) for c in batch]).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            feats = model.encode_image(t).cpu().float()
            feats = feats / feats.norm(dim=-1, keepdim=True)
        for fe in feats:
            scores.append(round(_bipolar_score(fe, pf, nf), 4))

    del model
    return scores


def normalise_sharpness(values: list[float]) -> list[float]:
    log_vals = [np.log1p(v) for v in values]
    lo, hi = min(log_vals), max(log_vals)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in log_vals]


# ── CLIP-IQA (ViT-L/14 bipolar prompts) ──────────────────────────────────────

def load_clip_iqa(device: str):
    import torch, open_clip
    print(f"Loading CLIP ViT-L/14 (IQA backend) on {device}...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-L-14", pretrained="openai", device=device
    )
    tokenizer = open_clip.get_tokenizer("ViT-L-14")
    model.eval()
    return model, preprocess, tokenizer


def encode_quality_texts(model, tokenizer, device):
    import torch
    pos_texts = [f"a photo of {p}" for p, _ in QUALITY_PAIRS]
    neg_texts = [f"a photo of {n}" for _, n in QUALITY_PAIRS]
    tokens = tokenizer(pos_texts + neg_texts).to(device)
    with torch.no_grad(), torch.amp.autocast(device):
        feats = model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    feats = feats.cpu().float()
    n = len(QUALITY_PAIRS)
    return feats[:n], feats[n:]


def clip_iqa_score(img_feat, pos_feats, neg_feats) -> float:
    import torch, torch.nn.functional as F
    scores = []
    for pf, nf in zip(pos_feats, neg_feats):
        logits = torch.tensor([(img_feat @ pf.unsqueeze(-1)).item(),
                               (img_feat @ nf.unsqueeze(-1)).item()])
        scores.append(F.softmax(logits, dim=0)[0].item())
    return float(np.mean(scores))


def run_clip_iqa(paths, device, batch_size=32):
    import torch
    from sklearn.preprocessing import normalize

    model, preprocess, tokenizer = load_clip_iqa(device)
    pos_feats, neg_feats = encode_quality_texts(model, tokenizer, device)

    embeddings, valid = [], []
    for i in tqdm(range(0, len(paths), batch_size), desc="CLIP-IQA embed"):
        batch = paths[i:i + batch_size]
        tensors, bvalid = [], []
        for p in batch:
            try:
                img = Image.open(p).convert("RGB")
                tensors.append(preprocess(img))
                bvalid.append(p)
            except Exception as e:
                print(f"  skip {p.name}: {e}")
        if not tensors:
            continue
        t = torch.stack(tensors).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            f = model.encode_image(t).cpu().float().numpy()
        embeddings.extend(f)
        valid.extend(bvalid)

    embs = normalize(np.array(embeddings))
    scores = {}
    for p, e in zip(valid, embs):
        scores[p] = clip_iqa_score(torch.tensor(e), pos_feats, neg_feats)
    for p in paths:
        scores.setdefault(p, 0.5)

    del model
    return scores


# ── PARA / rsinema aesthetic scorer (ViT-B/32) ───────────────────────────────

def load_para_scorer(device: str):
    import torch
    from transformers import CLIPVisionModel, CLIPProcessor
    from huggingface_hub import hf_hub_download
    from aesthetic_scorer import AestheticScorer

    print(f"Loading PARA aesthetic scorer (ViT-B/32) on {device}...")
    model_path = hf_hub_download("rsinema/aesthetic-scorer", "model.pt")
    processor  = CLIPProcessor.from_pretrained("rsinema/aesthetic-scorer")

    payload = torch.load(model_path, map_location=device, weights_only=False)
    if isinstance(payload, dict):
        # The saved state dict's backbone.* keys are the CLIPVisionTransformer's
        # (embeddings/encoder/...). transformers <5 nested that under
        # CLIPVisionModel.vision_model; 5.x flattened it onto the model itself.
        # Use whichever exists so the keys line up across versions.
        vision = CLIPVisionModel.from_pretrained("openai/clip-vit-base-patch32")
        backbone = getattr(vision, "vision_model", vision)
        scorer = AestheticScorer(backbone)
        scorer.load_state_dict(payload)
    else:
        scorer = payload

    scorer = scorer.to(device).eval()
    return scorer, processor


def run_para(paths, device, batch_size=32):
    import torch

    scorer, processor = load_para_scorer(device)
    results = {}
    for i in tqdm(range(0, len(paths), batch_size), desc="PARA scoring"):
        batch = paths[i:i + batch_size]
        tensors, bpaths = [], []
        for p in batch:
            try:
                img = Image.open(p).convert("RGB")
                t = processor(images=img, return_tensors="pt")["pixel_values"].squeeze(0)
                tensors.append(t)
                bpaths.append(p)
            except Exception as e:
                print(f"  skip {p.name}: {e}")
        if not tensors:
            continue
        batch_t = torch.stack(tensors).to(device)
        with torch.no_grad():
            out = scorer(batch_t)
        head_lists = [o.squeeze(1).tolist() for o in out]
        for p, *vals in zip(bpaths, *head_lists):
            results[p] = {k: v for k, v in zip(PARA_KEYS, vals)}

    fallback = {k: 2.5 for k in PARA_KEYS}
    for p in paths:
        results.setdefault(p, fallback)

    del scorer
    return results


# ── CLIP tag vocabulary ───────────────────────────────────────────────────────

CLIP_TAG_VOCAB = [
    # Scene type
    "landscape", "cityscape", "street scene", "interior room", "portrait",
    # Nature
    "forest", "mountains", "ocean", "beach", "lake", "river", "desert",
    "field", "garden", "snow and ice", "waterfall", "rocks and cliffs", "sky and clouds",
    # Urban / built environment
    "building and architecture", "bridge", "market or shop", "historical site",
    # People
    "single person", "group of people", "crowd", "child or children", "face close-up",
    # Animals
    "dog", "cat", "bird", "wildlife animal",
    # Light / weather
    "sunset or sunrise", "golden hour", "blue hour", "night scene",
    "foggy or misty", "rainy weather", "overcast sky", "bright sunny day",
    # Photographic style
    "bokeh background blur", "silhouette", "reflection in water",
    "black and white", "aerial or birds eye view", "long exposure",
    # Subject category
    "food or drink", "vehicle or transportation", "boat or ship",
    "sports or action", "festival or event", "abstract pattern",
    "indoor", "outdoor",
]


# ── Captions (BLIP) + keyword tags (CLIP ViT-B/32) ───────────────────────────

def run_caption_and_tags(paths: list[Path], device: str,
                         top_k: int = 8,
                         batch_size_blip: int = 16,
                         batch_size_clip: int = 32) -> dict:
    """
    Returns {path: {"caption": str, "tags": list[str]}} for every path.

    Caption : Salesforce/blip-image-captioning-base  (~990 MB, natively in transformers)
    Tags    : open_clip ViT-B/32  zero-shot against CLIP_TAG_VOCAB (top-k by cosine sim)
    """
    import torch
    import open_clip
    from transformers import BlipProcessor, BlipForConditionalGeneration

    results: dict[Path, dict] = {p: {"caption": "", "tags": []} for p in paths}

    # ── BLIP captions ──────────────────────────────────────────────────────────
    print(f"\nLoading BLIP captioning model on {device}...")
    try:
        blip_proc  = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        blip_model = BlipForConditionalGeneration.from_pretrained(
            "Salesforce/blip-image-captioning-base"
        ).to(device).eval()

        for i in tqdm(range(0, len(paths), batch_size_blip), desc="BLIP captions"):
            batch_paths = paths[i:i + batch_size_blip]
            images, bpaths = [], []
            for p in batch_paths:
                try:
                    images.append(Image.open(p).convert("RGB"))
                    bpaths.append(p)
                except Exception as e:
                    print(f"  skip {p.name}: {e}")
            if not images:
                continue

            inputs = blip_proc(images=images, return_tensors="pt").to(device)
            with torch.no_grad():
                out_ids = blip_model.generate(
                    **inputs, max_new_tokens=64,
                    num_beams=4, length_penalty=1.0,
                )
            captions = blip_proc.batch_decode(out_ids, skip_special_tokens=True)
            for p, cap in zip(bpaths, captions):
                results[p]["caption"] = cap.strip()

        del blip_model
        if device == "cuda":
            torch.cuda.empty_cache()

    except Exception as e:
        print(f"  BLIP captioning failed: {e}")

    # ── CLIP ViT-B/32 zero-shot keyword tags ───────────────────────────────────
    print(f"\nLoading CLIP ViT-B/32 for keyword tagging on {device}...")
    try:
        clip_model, _, clip_prep = open_clip.create_model_and_transforms(
            "ViT-B-32-quickgelu", pretrained="openai", device=device
        )
        clip_tok = open_clip.get_tokenizer("ViT-B-32-quickgelu")
        clip_model.eval()

        # Encode the full vocabulary once
        vocab_tokens = clip_tok(
            [f"a photo of {t}" for t in CLIP_TAG_VOCAB]
        ).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            vocab_feats = clip_model.encode_text(vocab_tokens)
            vocab_feats = vocab_feats / vocab_feats.norm(dim=-1, keepdim=True)
        vocab_feats = vocab_feats.cpu().float()   # (V, D)

        for i in tqdm(range(0, len(paths), batch_size_clip), desc="CLIP tags"):
            batch_paths = paths[i:i + batch_size_clip]
            tensors, bpaths = [], []
            for p in batch_paths:
                try:
                    img = Image.open(p).convert("RGB")
                    tensors.append(clip_prep(img))
                    bpaths.append(p)
                except Exception as e:
                    print(f"  skip {p.name}: {e}")
            if not tensors:
                continue

            t = torch.stack(tensors).to(device)
            with torch.no_grad(), torch.amp.autocast(device):
                img_feats = clip_model.encode_image(t).cpu().float()
                img_feats = img_feats / img_feats.norm(dim=-1, keepdim=True)

            sims = img_feats @ vocab_feats.T   # (B, V)
            topk_idx = torch.topk(sims, k=min(top_k, len(CLIP_TAG_VOCAB)), dim=1).indices
            for p, idx in zip(bpaths, topk_idx.tolist()):
                results[p]["tags"] = [CLIP_TAG_VOCAB[i] for i in idx]

        del clip_model
    except Exception as e:
        print(f"  CLIP tagging failed: {e}")

    return results


# ── Perceptual hashing / duplicate detection ──────────────────────────────────

def compute_phashes(paths: list[Path]) -> tuple[dict, dict]:
    """Returns (hashes, sizes). Sizes are raw (pre-EXIF-transpose) (w, h),
    matching the coordinate space the face detector uses for its bboxes, so
    the frontend can scale face overlays and lay out aspect-correct tiles."""
    import imagehash
    hashes: dict = {}
    sizes:  dict = {}
    for p in tqdm(paths, desc="Perceptual hashing"):
        try:
            im = Image.open(p)
            sizes[p] = im.size
            hashes[p] = imagehash.phash(im)
        except Exception as e:
            print(f"  hash error {p.name}: {e}")
    return hashes, sizes


def group_duplicates(hashes: dict, threshold: int = 6) -> list[list[Path]]:
    paths = list(hashes.keys())
    visited = set()
    groups = []
    for i, p in enumerate(paths):
        if p in visited:
            continue
        group = [p]
        visited.add(p)
        for j in range(i + 1, len(paths)):
            q = paths[j]
            if q not in visited and (hashes[p] - hashes[q]) <= threshold:
                group.append(q)
                visited.add(q)
        if len(group) > 1:
            groups.append(group)
    return groups


def _cohesion_split(members: list, embeddings: dict, hashes: dict,
                    threshold: int, floor: float) -> list:
    """Split one single-linkage candidate component into cohesive sub-groups.

    Single linkage chains: A~B, B~C, ... collapse into one blob even when the
    endpoints are nothing alike, which is how a whole shoot ends up in one
    "near-duplicate" group. We re-cluster the component with average-linkage
    agglomeration and stop merging once the best inter-cluster average cosine
    drops below `floor`, so loosely-connected chains break apart while tight
    bursts stay whole. phash-matched pairs (exact re-saves) are pinned at
    similarity 1.0 — a literal duplicate must never be split off.

    Returns the list of resulting sub-groups with >= 2 members; singletons drop
    out (they weren't really near-duplicates of anything)."""
    n = len(members)

    def sim(i: int, j: int) -> float:
        a, b = members[i], members[j]
        if (hashes[a] - hashes[b]) <= threshold:
            return 1.0
        if a in embeddings and b in embeddings:
            return float(np.dot(embeddings[a], embeddings[b]))
        return 0.0

    S = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            S[i][j] = S[j][i] = sim(i, j)

    clusters = [[i] for i in range(n)]
    while len(clusters) > 1:
        best, bi, bj = -2.0, -1, -1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                tot = sum(S[x][y] for x in clusters[a] for y in clusters[b])
                avg = tot / (len(clusters[a]) * len(clusters[b]))
                if avg > best:
                    best, bi, bj = avg, a, b
        if best < floor:
            break
        clusters[bi].extend(clusters[bj])
        del clusters[bj]

    return [[members[i] for i in c] for c in clusters if len(c) >= 2]


def assign_dup_groups(paths: list[Path], hashes: dict, threshold: int,
                      embeddings: dict | None = None, dup_sim: float = 0.92,
                      times: dict | None = None, dup_window: float = 600.0,
                      dup_cohesion: float = 0.90
                      ) -> tuple[dict, list]:
    """Assign fine near-duplicate group ids. First a similarity graph joins two
    images when EITHER their perceptual hashes are within `threshold` Hamming
    distance (literal re-saves / crops — matched regardless of time), OR — when
    CLIP `embeddings` are supplied — their cosine similarity is >= `dup_sim` AND
    they were taken within `dup_window` seconds of each other. CLIP catches
    "same shot, slight motion" pairs that phash misses (phash can read ~32/64
    for those); the time window keeps look-alikes from different moments out of
    the same group. phash remains the time-independent exact-dup signal so
    legacy behaviour is preserved when `embeddings` is None.

    The graph's connected components are only *candidates*: single linkage
    chains unrelated frames together, so each multi-frame candidate is then
    re-clustered by `_cohesion_split` and any group whose members aren't
    mutually cohesive (average cosine below `dup_cohesion`) is broken up. This
    keeps the loose-but-genuine pair (linked at, say, cos 0.93) together while
    shattering the 50-frame chains the segmenter used to emit.

    Returns ({path: group_id}, [groups]); only multi-member groups are kept and
    ids/members are ordered by earliest capture time for determinism."""
    items = [p for p in paths if p in hashes]
    parent = {p: p for p in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def t(p):
        return (times or {}).get(p, 0.0)

    has_emb = embeddings is not None
    n = len(items)
    for i in range(n):
        a = items[i]
        for j in range(i + 1, n):
            b = items[j]
            near = (hashes[a] - hashes[b]) <= threshold
            if (not near and has_emb and a in embeddings and b in embeddings
                    and (times is None or abs(t(a) - t(b)) <= dup_window)):
                near = float(np.dot(embeddings[a], embeddings[b])) >= dup_sim
            if near:
                union(a, b)

    from collections import defaultdict
    comps: dict = defaultdict(list)
    for p in items:
        comps[find(p)].append(p)

    # Candidate components -> cohesion-split blobs; tight bursts pass through.
    groups: list = []
    for comp in comps.values():
        if len(comp) <= 1:
            continue
        if has_emb and len(comp) > 2:
            groups.extend(
                _cohesion_split(comp, embeddings, hashes, threshold, dup_cohesion))
        else:
            groups.append(comp)
    groups = [m for m in groups if len(m) > 1]
    groups.sort(key=lambda m: (min(t(p) for p in m), str(min(m, key=str))))

    path_to_group: dict = {}
    for gid, group in enumerate(groups):
        group.sort(key=lambda p: (t(p), str(p)))
        for p in group:
            path_to_group[p] = gid
    return path_to_group, groups


def dup_centrality(groups: list, embeddings: dict | None) -> dict:
    """Per-image centrality within its near-duplicate group: the mean CLIP
    cosine to the group's other members. High = the representative frame the
    rest cluster around; low = an edge frame. The UI leads each group with its
    most-central photo so the hero is never a visual outlier. Returns {} (and
    callers fall back to quality) when embeddings aren't available."""
    if not embeddings:
        return {}
    out: dict = {}
    for g in groups:
        members = [p for p in g if p in embeddings]
        if len(members) < 2:
            continue
        for p in members:
            sims = [float(np.dot(embeddings[p], embeddings[q]))
                    for q in members if q is not p]
            out[p] = round(sum(sims) / len(sims), 4)
    return out


def coarsen_scenes_for_dups(paths: list[Path], scene_of: dict,
                            dup_groups: list, times: dict | None = None
                            ) -> tuple[dict, int]:
    """Coarsen a scene assignment so every near-duplicate group nests inside a
    single scene. Near-dups are the finest grain, so scenes must contain them:
    images sharing an initial (multi-member) scene stay together, and any scenes
    a dup group spans are merged via union-find. A singleton-scene image is
    pulled into a scene only when a dup group ties it there. This fixes the case
    where the sequential segmenter splits a continuous shoot between two genuine
    near-duplicates (which, being scene-bounded, could otherwise never merge).

    Returns ({path: scene_id|None}, n_scenes); lone images keep None."""
    from collections import defaultdict
    parent = {p: p for p in paths}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_scene: dict = defaultdict(list)
    for p in paths:
        s = scene_of.get(p)
        if s is not None:
            by_scene[s].append(p)
    for members in by_scene.values():
        for q in members[1:]:
            union(members[0], q)

    for group in dup_groups:
        g = [p for p in group if p in parent]
        for q in g[1:]:
            union(g[0], q)

    def t(p):
        return (times or {}).get(p, 0.0)

    comps: dict = defaultdict(list)
    for p in paths:
        comps[find(p)].append(p)
    multi = [m for m in comps.values() if len(m) > 1]
    multi.sort(key=lambda m: (min(t(p) for p in m), str(min(m, key=str))))

    scene_assign: dict = {p: None for p in paths}
    for sid, members in enumerate(multi):
        for p in members:
            scene_assign[p] = sid
    return scene_assign, len(multi)


# ── Capture time (EXIF) + scene grouping ──────────────────────────────────────

def read_capture_time(path: Path) -> float | None:
    """Best-effort capture timestamp (epoch seconds) from EXIF: DateTimeOriginal,
    then DateTimeDigitized, then the IFD0 DateTime. Returns None when absent or
    unparseable, so callers can fall back to filesystem mtime."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            dt = None
            try:
                sub = exif.get_ifd(0x8769)          # ExifIFD pointer
                dt = sub.get(0x9003) or sub.get(0x9004)  # DateTimeOriginal/Digitized
            except Exception:
                pass
            dt = dt or exif.get(0x0132)             # IFD0 DateTime
            if not dt:
                return None
            return datetime.strptime(str(dt).strip(), "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def compute_clip_embeddings(paths: list[Path], device: str,
                            batch_size: int = 64) -> dict:
    """L2-normalised CLIP ViT-B/32 image embeddings per path, for semantic scene
    similarity. Standardised on ViT-B/32 regardless of the aesthetic backend so
    scene grouping is consistent. Returns {path: 1-D float32 ndarray}."""
    import torch
    import open_clip

    model, _, prep = open_clip.create_model_and_transforms(
        "ViT-B-32-quickgelu", pretrained="openai", device=device)
    model.eval()

    embs: dict = {}
    for i in tqdm(range(0, len(paths), batch_size), desc="Scene embeddings (CLIP)"):
        batch = paths[i:i + batch_size]
        tensors, bpaths = [], []
        for p in batch:
            try:
                tensors.append(prep(Image.open(p).convert("RGB")))
                bpaths.append(p)
            except Exception as e:
                print(f"  skip {p.name}: {e}")
        if not tensors:
            continue
        t = torch.stack(tensors).to(device)
        with torch.no_grad(), torch.amp.autocast(device):
            f = model.encode_image(t)
            f = f / f.norm(dim=-1, keepdim=True)
        for p, v in zip(bpaths, f.cpu().float().numpy()):
            embs[p] = v

    del model
    return embs


def _visually_similar(a: Path, b: Path, embeddings: dict | None, hashes: dict | None,
                      sim: float, phash_dist: int) -> bool:
    """Whether two images look like the same scene. Prefers CLIP cosine when
    embeddings are present, else falls back to perceptual-hash distance."""
    if embeddings is not None and a in embeddings and b in embeddings:
        return float(np.dot(embeddings[a], embeddings[b])) >= sim
    if hashes is not None and a in hashes and b in hashes:
        return (hashes[a] - hashes[b]) <= phash_dist
    return False


def group_scenes(paths: list[Path], times: dict,
                 embeddings: dict | None = None, hashes: dict | None = None,
                 big_gap: float = 3600.0, small_gap: float = 120.0,
                 sim: float = 0.85, phash_dist: int = 18) -> tuple[dict, int]:
    """Segment images into rough "scenes" in capture-time order. EXIF time is the
    primary signal; visual similarity (CLIP cosine, or phash fallback) refines it.

    Boundary between two time-adjacent images when:
      - the gap exceeds `big_gap` (always a new scene), or
      - the gap exceeds `small_gap` AND they are not visually similar.
    Tight bursts (gap <= small_gap) always stay together.

    Returns ({path: scene_id|None}, n_scenes). Like dup groups, only multi-member
    scenes get an id; lone images get None."""
    if not paths:
        return {}, 0
    ordered = sorted(paths, key=lambda p: (times.get(p, 0.0), str(p)))
    segments: list = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        dt = times.get(cur, 0.0) - times.get(prev, 0.0)
        similar = _visually_similar(prev, cur, embeddings, hashes, sim, phash_dist)
        if dt > big_gap or (dt > small_gap and not similar):
            segments.append([cur])
        else:
            segments[-1].append(cur)

    scene_of: dict = {}
    sid = 0
    for seg in segments:
        if len(seg) > 1:
            for p in seg:
                scene_of[p] = sid
            sid += 1
        else:
            scene_of[seg[0]] = None
    return scene_of, sid


# ── Face detection / clustering (facenet-pytorch + DBSCAN) ───────────────────

def run_faces(paths: list[Path], device: str,
              face_refs: dict | None = None,
              min_prob: float = 0.90,
              min_face_size: int = 80,
              min_face_rel: float = 0.04,
              eps: float = 0.50,
              score_expr: bool = False) -> tuple[dict, dict]:
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
    """
    import torch
    from facenet_pytorch import MTCNN, InceptionResnetV1
    from sklearn.cluster import DBSCAN

    print(f"\nInitialising face detector + embedder on {device}...")
    # post_process=True → crops already normalised to [-1, 1] for InceptionResnetV1
    mtcnn  = MTCNN(keep_all=True, device=device,
                   min_face_size=min_face_size, margin=14, post_process=True)
    resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)

    # ── Phase 1: detect + embed every face ────────────────────────────────────
    # all_faces entries: (path, face_slot_idx, embedding_np, bbox, prob,
    #                     face_sharp_raw, expr_crop|None)
    all_faces: list[tuple] = []
    img_sizes: dict[Path, tuple] = {}
    n_filtered_rel = 0

    for p in tqdm(paths, desc="Face detection"):
        try:
            img     = Image.open(p).convert("RGB")
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
        return {}, img_sizes

    print(f"  Detected {len(all_faces)} face instances in {len(img_sizes)} images "
          f"(filtered out {n_filtered_rel} faces below rel-size {min_face_rel:.2f})")

    # ── Phase 2: DBSCAN on L2-normalised embeddings (cosine metric) ───────────
    emb_matrix = np.array([f[2] for f in all_faces])           # (M, 512)
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
    cluster_names: dict[int, str] = {}
    if face_refs:
        print("  Matching reference photos to clusters...")
        for name, ref_path in face_refs.items():
            try:
                ref_img   = Image.open(ref_path).convert("RGB")
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
    norm_sharp = normalise_sharpness([f[5] for f in all_faces])
    expr_scores = (run_face_expression([f[6] for f in all_faces], device)
                   if score_expr else [None] * len(all_faces))

    # ── Phase 6: build per-image face records ──────────────────────────────────
    face_data: dict[Path, list] = {}
    for idx, (p, _vi, _emb, box, prob, _sr, _ec) in enumerate(all_faces):
        cid  = int(labels[idx])
        name = cluster_names.get(cid) if cid >= 0 else None
        rec = {
            "bbox":       [round(v, 1) for v in box],
            "prob":       round(prob, 3),
            "cluster_id": cid,
            "name":       name,
            "sharp":      round(norm_sharp[idx], 4),
        }
        if expr_scores[idx] is not None:
            rec["expr"] = expr_scores[idx]
        face_data.setdefault(p, []).append(rec)

    del resnet
    return face_data, img_sizes


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--recurse",        action="store_true")
    ap.add_argument("--out",            default=None)
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
                    help="Add BLIP captions + CLIP keyword tags (slower)")
    ap.add_argument("--top-tags",       type=int, default=8,
                    help="Number of keyword tags per image (default: 8)")
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
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"Error: {folder} does not exist"); sys.exit(1)

    glob = "**/*" if args.recurse else "*"
    paths = [p for p in folder.glob(glob)
             if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()]
    print(f"Found {len(paths)} images in {folder}")
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

    prev_by_path: dict[str, dict] = {}
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
            else:
                print(f"  (config changed {prev_cfg} -> {cur_cfg}; re-scoring all)")
        except Exception as e:
            print(f"  (cache unreadable, re-scoring all: {e})")

    sigs: dict[Path, tuple] = {}
    for p in paths:
        try:
            st = p.stat()
            sigs[p] = (st.st_mtime, st.st_size)
        except OSError:
            sigs[p] = (None, None)

    def reusable(p: Path):
        prev = prev_by_path.get(str(p))
        if not prev or "phash" not in prev or prev.get("mtime") is None:
            return None
        mt, sz = sigs[p]
        if sz is None or prev.get("fsize") != sz or abs(prev["mtime"] - mt) > 1e-6:
            return None
        if use_para     and "para_aesthetic" not in prev: return None
        if use_clip_iqa and "clip_iqa"       not in prev: return None
        if args.caption and "caption"        not in prev: return None
        if args.faces   and "faces"          not in prev: return None
        if use_scenes   and "scene_group"    not in prev: return None
        return prev

    cached: dict[Path, dict] = {}
    to_process: list[Path] = []
    for p in paths:
        prev = reusable(p)
        (cached.__setitem__(p, prev) if prev is not None else to_process.append(p))
    print(f"\nIncremental: {len(cached)} cached, {len(to_process)} to score "
          f"(of {len(paths)})")

    def device_for() -> str:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"

    # ── Sharpness (raw reused from cache; only new files read from disk) ──
    print("\nComputing sharpness (Laplacian variance)...")
    raw_sharp: dict[Path, float] = {p: cached[p].get("raw_laplacian", 0.0) for p in cached}
    for p in tqdm(to_process, desc="Sharpness"):
        raw_sharp[p] = laplacian_variance(p)
    norm_sharp = normalise_sharpness([raw_sharp[p] for p in paths])
    sharpness  = {p: s for p, s in zip(paths, norm_sharp)}

    # ── CLIP-IQA / PARA (new files only) ──
    clip_iqa_scores: dict[Path, float] = {}
    if use_clip_iqa and to_process:
        print()
        clip_iqa_scores = run_clip_iqa(to_process, device_for())

    para_raw: dict[Path, dict] = {}
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

    # ── Capture time (EXIF, mtime fallback; reused from cache when present) ──
    capture_time: dict[Path, float] = {}
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
    # re-derived here (never persisted) and drive both "same scene" and the
    # CLIP-aware near-dup test; phash is the fallback / exact-dup signal.
    embeddings: dict | None = None
    if use_scenes and not args.no_clip:
        print()
        embeddings = compute_clip_embeddings(paths, device_for())

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

    # ── BLIP captions + CLIP keyword tags (new files only) ──
    captions: dict[Path, dict] = {}
    if args.caption and to_process:
        captions = run_caption_and_tags(to_process, device_for(), top_k=args.top_tags)

    # ── Face detection + identity clustering ──
    # Clustering is global, so any change forces a whole-folder re-detection;
    # an unchanged folder reuses cached faces.
    face_data: dict[Path, list] = {}
    faces_global = bool(args.faces and to_process)
    if faces_global:
        refs: dict[str, Path] = {}
        for item in args.face_ref:
            if "=" in item:
                name, rpath = item.split("=", 1)
                refs[name.strip()] = Path(rpath.strip())
            else:
                print(f"  Warning: --face-ref '{item}' ignored (expected NAME=PATH)")
        face_data, _ = run_faces(
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
        rec["capture_time"] = capture_time.get(p)
        if p in hashes:
            rec["phash"] = str(hashes[p])
        return rec

    records = []
    for p in paths:
        if p in cached:
            # Reuse all per-image outputs; only recompute set-relative scalars.
            rec = dict(cached[p])
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
            "caption_model":    "blip-base+clip-b32" if args.caption else None,
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


if __name__ == "__main__":
    main()
