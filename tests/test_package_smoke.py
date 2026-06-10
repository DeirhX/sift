"""Import-smoke test for the `sift` package layout.

The `sift.audit` package re-exports the public surface of its submodules so
callers can reach the whole analysis API as ``sift.audit.<name>``. This test
imports every submodule (catching import errors / NameErrors at module load
that the model-mocked unit tests can't, since their heavy paths are stubbed)
and asserts the package re-exports the *same* objects each submodule defines —
so the public surface can't silently drift out of sync. It also pins the
`sift` CLI dispatcher's routing contract.
"""
import importlib

import pytest

import sift
from sift import audit
from sift import cli

# (submodule, [public names it owns]) — the contract the package must re-export.
SUBMODULES = {
    "sift.audit.clip_common": ["iter_image_batches", "load_openclip_b32",
                               "encode_prompt_pairs", "bipolar_score"],
    "sift.audit.scoring":     ["laplacian_variance", "normalise_sharpness",
                               "load_clip_iqa", "run_clip_iqa",
                               "load_para_scorer", "run_para",
                               "QUALITY_PAIRS", "PARA_KEYS"],
    "sift.audit.faces":       ["face_laplacian_variance", "expand_box",
                               "run_face_expression", "run_faces",
                               "EXPRESSION_PAIRS", "FACE_SHARP_PX"],
    "sift.audit.tagging":     ["_clean_tags", "run_qwen_tags", "run_caption_and_tags",
                               "QWEN_TAG_MODEL", "QWEN_TAG_PROMPT"],
    "sift.audit.grouping":    ["compute_phashes", "group_duplicates",
                               "assign_dup_groups", "dup_centrality",
                               "coarsen_scenes_for_dups", "read_capture_time",
                               "compute_clip_embeddings", "group_scenes"],
    "sift.audit.cli":         ["build_parser", "main", "IMAGE_EXTENSIONS"],
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
def test_package_reexports_same_object(name):
    """Every public name is reachable on sift.audit and is the *same* object as
    in its owning submodule (no stale copy)."""
    owner = next(importlib.import_module(m) for m, ns in SUBMODULES.items()
                 if name in ns)
    assert hasattr(audit, name), f"sift.audit no longer re-exports {name}"
    assert getattr(audit, name) is getattr(owner, name)


def test_parser_builds_and_parses():
    """The argparse contract survives the move to sift.audit.cli."""
    args = audit.build_parser().parse_args(["/some/folder", "--no-clip"])
    # `folder` is nargs='+' now (multi-folder onboarding), so it's a list.
    assert args.folder == ["/some/folder"]
    assert args.no_clip is True
    assert args.backend == "para"


def test_parser_accepts_multiple_folders():
    """Multiple source folders collapse into the one positional list."""
    args = audit.build_parser().parse_args(["/a", "/b", "/c", "--recurse"])
    assert args.folder == ["/a", "/b", "/c"]
    assert args.recurse is True


def test_package_has_version():
    assert isinstance(sift.__version__, str) and sift.__version__


# ── CLI dispatcher routing ───────────────────────────────────────────────────

def test_cli_help_returns_zero(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "analyze" in out and "index" in out and "serve" in out


def test_cli_no_args_shows_usage(capsys):
    assert cli.main([]) == 0
    assert "usage: sift" in capsys.readouterr().out


def test_cli_unknown_command_is_error(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err
