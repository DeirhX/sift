"""Shared CLIP / image-batching primitives.

The lowest layer of the analysis package: the open/convert/skip/stack batching
loop and the OpenCLIP ViT-B/32 helpers that the scoring, grouping and face
modules all build on. No intra-package dependencies.
"""
import numpy as np
from PIL import Image, ImageOps
from tqdm import tqdm


def iter_image_batches(paths, preprocess, device, batch_size, desc):
    """Yield (stacked_tensor, batch_paths) over `paths`, opening each as RGB and
    mapping it through `preprocess` (PIL.Image -> tensor). Images that fail to
    open/preprocess are skipped with a warning; empty batches are dropped. This
    is the one place the open/convert/skip/stack/tqdm boilerplate lives, shared
    by every CLIP-style batch encoder below.

    EXIF orientation is applied (``exif_transpose``) so the models see each image
    upright, exactly as the viewer displays it. This matters a lot: a portrait
    frame (EXIF orient 6/8) left as raw sensor pixels is effectively rotated 90°,
    which collapses its CLIP cosine to a differently-oriented frame of the same
    scene (measured 0.74 raw vs 0.97 upright) and skews the aesthetic/quality
    scorers that were trained on upright photos."""
    import torch
    for i in tqdm(range(0, len(paths), batch_size), desc=desc):
        tensors, bpaths = [], []
        for p in paths[i:i + batch_size]:
            try:
                tensors.append(preprocess(ImageOps.exif_transpose(Image.open(p)).convert("RGB")))
                bpaths.append(p)
            except Exception as e:
                print(f"  skip {getattr(p, 'name', p)}: {e}")
        if tensors:
            yield torch.stack(tensors).to(device), bpaths


def load_openclip_b32(device: str):
    """Load the OpenAI CLIP ViT-B/32 (quickgelu) backbone used for scene
    embeddings and expression scoring. Returns (model.eval(), preprocess,
    tokenizer)."""
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32-quickgelu", pretrained="openai", device=device)
    return model.eval(), preprocess, open_clip.get_tokenizer("ViT-B-32-quickgelu")


def encode_prompt_pairs(model, tokenizer, pairs, device):
    """Encode (positive, negative) prompt pairs with a CLIP text encoder,
    L2-normalised. Returns (pos_feats, neg_feats), each (len(pairs), D) on CPU."""
    import torch
    pos = tokenizer([f"a photo of {p}" for p, _ in pairs]).to(device)
    neg = tokenizer([f"a photo of {n}" for _, n in pairs]).to(device)
    with torch.no_grad(), torch.amp.autocast(device):
        pf = model.encode_text(pos); pf = pf / pf.norm(dim=-1, keepdim=True)
        nf = model.encode_text(neg); nf = nf / nf.norm(dim=-1, keepdim=True)
    return pf.cpu().float(), nf.cpu().float()


def bipolar_score(img_feat, pos_feats, neg_feats) -> float:
    """Mean positive-vs-negative softmax probability across prompt pairs — the
    shared CLIP bipolar-prompt scorer behind both CLIP-IQA and portrait
    expression quality. `img_feat` and each per-pair feat are 1-D L2-normalised
    vectors; returns a single 0-1 score."""
    import torch, torch.nn.functional as F
    vals = []
    for pf, nf in zip(pos_feats, neg_feats):
        logits = torch.tensor([float(img_feat @ pf), float(img_feat @ nf)])
        vals.append(F.softmax(logits, dim=0)[0].item())
    return float(np.mean(vals))
