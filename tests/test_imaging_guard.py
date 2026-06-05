"""Enforce the single image-loading chokepoint.

All image decoding must go through `sift.audit.imaging` (load_rgb / load_gray_u8)
so adding a new input format stays a one-file change. This test fails if a bare
`Image.open(` or `cv2.imread(` appears anywhere under `sift/` except imaging.py.
Mark a deliberate exception with a trailing `# noqa: imaging` comment.
"""
from pathlib import Path

SIFT = Path(__file__).resolve().parents[1] / "sift"
FORBIDDEN = ("Image.open(", "cv2.imread(")
ALLOWED_FILE = "imaging.py"


def _violations() -> list[str]:
    bad = []
    for py in SIFT.rglob("*.py"):
        if py.name == ALLOWED_FILE:
            continue
        rel = py.relative_to(SIFT.parent)
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "# noqa: imaging" in line:
                continue
            if any(tok in line for tok in FORBIDDEN):
                bad.append(f"{rel}:{n}: {line.strip()}")
    return bad


def test_no_direct_image_loading_outside_imaging():
    bad = _violations()
    assert not bad, (
        "Open images via sift.audit.imaging.load_rgb / load_gray_u8 only.\n"
        "Found direct loads (add '# noqa: imaging' for a deliberate exception):\n"
        + "\n".join(bad))
