"""Per-image quality scoring: classical Laplacian sharpness and the two
aesthetic backends (CLIP-IQA bipolar prompts, PARA regression heads)."""
import cv2
import numpy as np

from .clip_common import iter_image_batches, encode_prompt_pairs, bipolar_score

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


# ── Sharpness ────────────────────────────────────────────────────────────────

def laplacian_variance(path) -> float:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    if max(h, w) > 1920:
        scale = 1920 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def normalise_sharpness(values: list[float]) -> list[float]:
    log_vals = [np.log1p(v) for v in values]
    lo, hi = min(log_vals), max(log_vals)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in log_vals]


def sharpness_basis(values: list[float]) -> tuple[float, float]:
    """The (lo, hi) log1p reference range for normalising raw Laplacian variance
    to 0-1. Computed once over the whole library and persisted, so per-image
    sharpness/combined scores stay put across rescans (and are comparable across
    folders) instead of drifting with each set's own min/max."""
    logs = [float(np.log1p(v)) for v in values]
    if not logs:
        return 0.0, 1.0
    return min(logs), max(logs)


def normalise_with_basis(values: list[float], lo: float, hi: float) -> list[float]:
    """Normalise raw Laplacian variances against a fixed `(lo, hi)` basis,
    clamped to [0, 1] so a value beyond the basis saturates rather than
    re-scaling everyone else. Mirrors normalise_sharpness's log1p transform."""
    if hi <= lo:
        return [0.5] * len(values)
    span = hi - lo
    return [min(1.0, max(0.0, (float(np.log1p(v)) - lo) / span)) for v in values]


# ── CLIP-IQA (ViT-L/14 bipolar prompts) ──────────────────────────────────────

def load_clip_iqa(device: str):
    import open_clip
    print(f"Loading CLIP ViT-L/14 (IQA backend) on {device}...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-L-14", pretrained="openai", device=device
    )
    tokenizer = open_clip.get_tokenizer("ViT-L-14")
    model.eval()
    return model, preprocess, tokenizer


def run_clip_iqa(paths, device, batch_size=32):
    import torch
    from sklearn.preprocessing import normalize

    model, preprocess, tokenizer = load_clip_iqa(device)
    pos_feats, neg_feats = encode_prompt_pairs(model, tokenizer, QUALITY_PAIRS, device)

    embeddings, valid = [], []
    for t, bpaths in iter_image_batches(paths, preprocess, device,
                                        batch_size, "CLIP-IQA embed"):
        with torch.no_grad(), torch.amp.autocast(device):
            f = model.encode_image(t).cpu().float().numpy()
        embeddings.extend(f)
        valid.extend(bpaths)

    embs = normalize(np.array(embeddings))
    scores = {}
    for p, e in zip(valid, embs):
        scores[p] = bipolar_score(torch.tensor(e), pos_feats, neg_feats)
    for p in paths:
        scores.setdefault(p, 0.5)

    del model
    return scores


# ── PARA / rsinema aesthetic scorer (ViT-B/32) ───────────────────────────────

def load_para_scorer(device: str):
    import torch
    from transformers import CLIPVisionModel, CLIPProcessor
    from huggingface_hub import hf_hub_download
    from sift.audit.aesthetic_scorer import AestheticScorer

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

    def prep(img):
        return processor(images=img, return_tensors="pt")["pixel_values"].squeeze(0)

    results = {}
    for batch_t, bpaths in iter_image_batches(paths, prep, device,
                                              batch_size, "PARA scoring"):
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
