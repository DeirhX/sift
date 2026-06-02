"""The photo-audit analysis pipeline, split into focused modules:

    clip_common  shared CLIP / batching primitives (lowest layer)
    scoring      Laplacian sharpness + CLIP-IQA + PARA aesthetic backends
    tagging      BLIP captions + Qwen3-VL keyword tags
    grouping     perceptual-hash duplicates, scene segmentation, capture time
    faces        MTCNN detection + VGGFace2 embeddings + DBSCAN clustering
    cli          argument parsing + the orchestration in main()

`photo_audit.py` (repo root) is a thin re-export shim over this package so the
documented `python photo_audit.py` entry point and `import photo_audit` both
keep working. The webapp invokes the shim as a subprocess.
"""
import sys
from pathlib import Path

# aesthetic_scorer.py lives one level up (repo root). Keep it importable no
# matter how this package is reached (subprocess, tests, direct import), since
# scoring.load_para_scorer does `from aesthetic_scorer import AestheticScorer`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
