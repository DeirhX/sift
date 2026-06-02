"""
Classify _Unclassified images against the Inspiration folder taxonomy using CLIP.
Outputs sorted results so each image gets a best-fit destination folder.
"""
import sys, json
from pathlib import Path
import torch, open_clip
from PIL import Image
from tqdm import tqdm

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.gif'}

CATEGORIES = {
    "Digital Concept Art/Dark Fantasy": [
        "dark fantasy digital painting with demons or monsters",
        "gothic horror fantasy illustration with dark atmosphere",
        "vampire or undead character digital art",
        "dark medieval fantasy scene painting",
    ],
    "Digital Concept Art/Historical-Medieval": [
        "historical medieval battle scene painting",
        "medieval knight or soldier illustration",
        "historical war painting or reenactment",
        "ancient or medieval landscape with people",
    ],
    "Digital Concept Art/Sci-Fi": [
        "science fiction concept art with spaceships or technology",
        "futuristic city or environment digital painting",
        "sci-fi character with armor or cyberpunk aesthetic",
    ],
    "Digital Concept Art/Environment-Landscape": [
        "fantasy environment landscape concept art painting",
        "misty foggy forest or nature environment art",
        "fantasy village or town scene painting",
        "dramatic sky or atmospheric landscape illustration",
    ],
    "Digital Concept Art/Character Portrait": [
        "fantasy character portrait digital painting close-up",
        "detailed character face or bust painting",
    ],
    "Cinematic Reference/Film Stills": [
        "movie film still or screenshot from a film",
        "cinematic scene from a movie or TV show",
        "dramatic film noir or cinematic photography",
    ],
    "Photography/Nature-Forest": [
        "photograph of forest trees or nature",
        "outdoor nature photography with plants or landscape",
        "rock formation or natural geological feature photo",
    ],
    "Photography/Vintage-Historical": [
        "vintage or old black and white photograph",
        "historical photograph of people or places",
        "sepia tone or aged photographic portrait",
    ],
    "Photography/Portrait-Lighting": [
        "portrait photography with dramatic studio lighting",
        "professional portrait photo of a person",
    ],
    "Production Reference/Game Dev (KCD-Warhorse)": [
        "game development screenshot or concept",
        "medieval game environment or asset reference",
        "video game concept art or promotional image",
    ],
    "Production Reference/Color Grading": [
        "color grading reference image or LUT chart",
        "film color palette or cinematography reference",
    ],
    "Production Reference/Making-of Process": [
        "digital art making-of process or tutorial screenshot",
        "3D modeling or rendering software screenshot",
        "behind the scenes production reference",
    ],
    "Classical Painting": [
        "classical oil painting from the 17th or 18th century",
        "renaissance or baroque era painting reproduction",
        "classical portrait painting in traditional style",
    ],
    "Junk/Delete": [
        "blank white or empty image",
        "low quality blurry image",
        "screenshot of a website or social media post",
        "meme or internet joke image",
        "watermarked stock photo thumbnail",
    ],
}

# Flatten to (label_text, category) pairs
ALL_LABELS = []
LABEL_TO_CAT = {}
for cat, labels in CATEGORIES.items():
    for l in labels:
        ALL_LABELS.append(l)
        LABEL_TO_CAT[l] = cat


def classify(folder: Path):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading CLIP on {device}...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-L-14", pretrained="openai", device=device
    )
    tokenizer = open_clip.get_tokenizer("ViT-L-14")
    model.eval()

    texts = [f"a photo of {l}" for l in ALL_LABELS]
    tokens = tokenizer(texts).to(device)
    with torch.no_grad(), torch.amp.autocast(device):
        text_feats = model.encode_text(tokens)
        text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)

    paths = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file())
    print(f"Classifying {len(paths)} images...\n")

    results = []
    for p in tqdm(paths):
        try:
            img = Image.open(p).convert("RGB")
            t = preprocess(img).unsqueeze(0).to(device)
            with torch.no_grad(), torch.amp.autocast(device):
                f = model.encode_image(t)
                f = f / f.norm(dim=-1, keepdim=True)
            sims = (f @ text_feats.T).squeeze(0).cpu().float().tolist()

            # Best label and its category
            best_idx = sims.index(max(sims))
            best_label = ALL_LABELS[best_idx]
            best_cat = LABEL_TO_CAT[best_label]
            best_score = sims[best_idx]

            # Best score per category
            cat_scores = {}
            for label, score in zip(ALL_LABELS, sims):
                cat = LABEL_TO_CAT[label]
                cat_scores[cat] = max(cat_scores.get(cat, 0), score)

            top3_cats = sorted(cat_scores.items(), key=lambda x: -x[1])[:3]

            results.append({
                "file": p.name,
                "best_category": best_cat,
                "best_score": round(best_score, 4),
                "best_label": best_label,
                "top3": [(c, round(s, 4)) for c, s in top3_cats],
            })
        except Exception as e:
            print(f"  Error on {p.name}: {e}")

    # Sort by category then score
    results.sort(key=lambda r: (r["best_category"], -r["best_score"]))

    out = folder.parent / "unclassified_classification.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {out}\n")
    print("=== CLASSIFICATION RESULTS ===")
    current_cat = None
    for r in results:
        if r["best_category"] != current_cat:
            current_cat = r["best_category"]
            print(f"\n--- {current_cat} ---")
        print(f"  [{r['best_score']:.3f}] {r['file']}")
        print(f"         ^ {r['best_label']}")

    return results


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"G:\My Drive\Inspiration\_Unclassified")
    classify(folder)
