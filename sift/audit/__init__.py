"""The photo-audit analysis pipeline, split into focused modules:

    clip_common      shared CLIP / batching primitives (lowest layer)
    scoring          Laplacian sharpness + CLIP-IQA + PARA aesthetic backends
    aesthetic_scorer the rsinema/aesthetic-scorer model head (loaded by scoring)
    tagging          BLIP captions + Qwen3-VL keyword tags
    grouping         perceptual-hash / CLIP duplicates, scene segmentation
    faces            MTCNN detection + VGGFace2 embeddings + DBSCAN clustering
    cli              argument parsing + the orchestration in main()

Reached via the unified CLI as `sift analyze` (see ``sift.cli``), or directly
as ``from sift.audit.cli import main``.

This package re-exports the public surface of every submodule, so callers can
reach the whole API as ``sift.audit.<name>`` without knowing the internal split
(``test_package_smoke`` pins that the re-exports never drift). Only lightweight
deps (numpy/opencv/PIL/tqdm) are imported here; torch/transformers/imagehash
stay lazy inside the functions that need them.
"""
from .clip_common import (iter_image_batches, load_openclip_b32,
                          encode_prompt_pairs, bipolar_score)
from .scoring import (laplacian_variance, normalise_sharpness,
                      load_clip_iqa, run_clip_iqa, load_para_scorer, run_para,
                      QUALITY_PAIRS, PARA_KEYS)
from .faces import (face_laplacian_variance, expand_box,
                    run_face_expression, run_faces,
                    EXPRESSION_PAIRS, FACE_SHARP_PX)
from .tagging import (_clean_tags, run_qwen_tags, run_caption_and_tags,
                      QWEN_TAG_MODEL, QWEN_TAG_PROMPT)
from .grouping import (compute_phashes, group_duplicates, assign_dup_groups,
                       dup_centrality, coarsen_scenes_for_dups, read_capture_time,
                       compute_clip_embeddings, group_scenes)
from .cli import build_parser, main, IMAGE_EXTENSIONS

__all__ = [
    "iter_image_batches", "load_openclip_b32", "encode_prompt_pairs", "bipolar_score",
    "laplacian_variance", "normalise_sharpness", "load_clip_iqa", "run_clip_iqa",
    "load_para_scorer", "run_para", "QUALITY_PAIRS", "PARA_KEYS",
    "face_laplacian_variance", "expand_box", "run_face_expression", "run_faces",
    "EXPRESSION_PAIRS", "FACE_SHARP_PX",
    "_clean_tags", "run_qwen_tags", "run_caption_and_tags",
    "QWEN_TAG_MODEL", "QWEN_TAG_PROMPT",
    "compute_phashes", "group_duplicates", "assign_dup_groups", "dup_centrality",
    "coarsen_scenes_for_dups", "read_capture_time", "compute_clip_embeddings",
    "group_scenes",
    "build_parser", "main", "IMAGE_EXTENSIONS",
]
