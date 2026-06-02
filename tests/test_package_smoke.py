"""Import-smoke test for the `audit` package split.

`photo_audit.py` is a thin re-export shim over the `audit` subpackage. This test
imports every submodule (catching import errors / NameErrors at module load that
the model-mocked unit tests can't, since their heavy paths are stubbed) and
asserts the shim re-exports the *same* objects each submodule defines — so the
public surface can't silently drift out of sync with the package.
"""
import importlib

import pytest

import photo_audit

# (submodule, [public names it owns]) — the contract the shim must re-export.
SUBMODULES = {
    "audit.clip_common": ["iter_image_batches", "load_openclip_b32",
                          "encode_prompt_pairs", "bipolar_score"],
    "audit.scoring":     ["laplacian_variance", "normalise_sharpness",
                          "load_clip_iqa", "run_clip_iqa",
                          "load_para_scorer", "run_para",
                          "QUALITY_PAIRS", "PARA_KEYS"],
    "audit.faces":       ["face_laplacian_variance", "expand_box",
                          "run_face_expression", "run_faces",
                          "EXPRESSION_PAIRS", "FACE_SHARP_PX"],
    "audit.tagging":     ["_clean_tags", "run_qwen_tags", "run_caption_and_tags",
                          "QWEN_TAG_MODEL", "QWEN_TAG_PROMPT"],
    "audit.grouping":    ["compute_phashes", "group_duplicates",
                          "assign_dup_groups", "dup_centrality",
                          "coarsen_scenes_for_dups", "read_capture_time",
                          "compute_clip_embeddings", "group_scenes"],
    "audit.cli":         ["build_parser", "main", "IMAGE_EXTENSIONS"],
}


@pytest.mark.parametrize("module_name", SUBMODULES)
def test_submodule_imports(module_name):
    mod = importlib.import_module(module_name)
    for name in SUBMODULES[module_name]:
        assert hasattr(mod, name), f"{module_name} is missing {name}"


@pytest.mark.parametrize(
    "name",
    [n for names in SUBMODULES.values() for n in names if not n[0].isupper()],
)
def test_shim_reexports_same_object(name):
    """Every public name is reachable on photo_audit and is the *same* object as
    in its owning submodule (no stale copy)."""
    owner = next(importlib.import_module(m) for m, ns in SUBMODULES.items()
                 if name in ns)
    assert hasattr(photo_audit, name), f"photo_audit no longer re-exports {name}"
    assert getattr(photo_audit, name) is getattr(owner, name)


def test_parser_builds_and_parses():
    """The argparse contract survives the move to audit.cli."""
    args = photo_audit.build_parser().parse_args(["/some/folder", "--no-clip"])
    assert args.folder == "/some/folder"
    assert args.no_clip is True
    assert args.backend == "para"
