"""Per-image quality scoring: classical Laplacian sharpness and the two
aesthetic backends (CLIP-IQA bipolar prompts, PARA regression heads)."""
import cv2
import numpy as np

from .clip_common import iter_image_batches, encode_prompt_pairs, bipolar_score
from .imaging import load_gray_u8

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
    img = load_gray_u8(path)
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


def run_clip_iqa(paths, device, batch_size=32, progress=None):
    import torch
    from sklearn.preprocessing import normalize

    model, preprocess, tokenizer = load_clip_iqa(device)
    pos_feats, neg_feats = encode_prompt_pairs(model, tokenizer, QUALITY_PAIRS, device)

    embeddings, valid = [], []
    done, n = 0, len(paths)
    for t, bpaths in iter_image_batches(paths, preprocess, device,
                                        batch_size, "CLIP-IQA embed"):
        with torch.no_grad(), torch.amp.autocast(device):
            f = model.encode_image(t).cpu().float().numpy()
        embeddings.extend(f)
        valid.extend(bpaths)
        done += len(bpaths)
        if progress is not None:
            progress(done, n)

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
    from transformers.utils import logging as hf_logging
    from huggingface_hub import hf_hub_download
    from sift.audit.aesthetic_scorer import AestheticScorer

    # We deliberately load a full CLIP checkpoint into a vision-only model, so the
    # text_model.* keys are "unexpected" by design. Silence transformers' multi-
    # line LOAD REPORT — it's pure noise here and floods the live task log.
    hf_logging.set_verbosity_error()

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


def run_para(paths, device, batch_size=32, progress=None):
    import torch

    scorer, processor = load_para_scorer(device)

    def prep(img):
        return processor(images=img, return_tensors="pt")["pixel_values"].squeeze(0)

    results = {}
    done, n = 0, len(paths)
    for batch_t, bpaths in iter_image_batches(paths, prep, device,
                                              batch_size, "PARA scoring"):
        with torch.no_grad():
            out = scorer(batch_t)
        head_lists = [o.squeeze(1).tolist() for o in out]
        for p, *vals in zip(bpaths, *head_lists):
            results[p] = {k: v for k, v in zip(PARA_KEYS, vals)}
        done += len(bpaths)
        if progress is not None:
            progress(done, n)

    fallback = {k: 2.5 for k in PARA_KEYS}
    for p in paths:
        results.setdefault(p, fallback)

    del scorer
    return results
