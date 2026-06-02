"""photo_audit.py — CLI entry point for the photo audit pipeline.

The implementation now lives in the ``audit`` package (audit.clip_common /
.scoring / .tagging / .grouping / .faces / .cli). This module is a thin shim
that re-exports the public surface so existing callers keep working unchanged:

  * ``python photo_audit.py <folder> [options]``  — the documented CLI
  * ``import photo_audit`` then ``photo_audit.<fn>``  — tests + the webapp
  * the webapp launches this file as a subprocess (see webapp/analysis.py)

Run ``python photo_audit.py --help`` for the full option reference (it lives in
audit/cli.py's module docstring).
"""
import sys
from pathlib import Path

# aesthetic_scorer.py sits next to this file; keep it importable from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit.clip_common import (iter_image_batches, load_openclip_b32,  # noqa: E402,F401
                               encode_prompt_pairs, bipolar_score)
from audit.scoring import (laplacian_variance, normalise_sharpness,  # noqa: E402,F401
                           load_clip_iqa, run_clip_iqa, load_para_scorer, run_para,
                           QUALITY_PAIRS, PARA_KEYS)
from audit.faces import (face_laplacian_variance, expand_box,  # noqa: E402,F401
                         run_face_expression, run_faces,
                         EXPRESSION_PAIRS, FACE_SHARP_PX)
from audit.tagging import (_clean_tags, run_qwen_tags, run_caption_and_tags,  # noqa: E402,F401
                           QWEN_TAG_MODEL, QWEN_TAG_PROMPT)
from audit.grouping import (compute_phashes, group_duplicates, assign_dup_groups,  # noqa: E402,F401
                            dup_centrality, coarsen_scenes_for_dups, read_capture_time,
                            compute_clip_embeddings, group_scenes)
from audit.cli import build_parser, main, IMAGE_EXTENSIONS  # noqa: E402,F401


if __name__ == "__main__":
    main()
