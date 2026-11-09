"""The single image-loading chokepoint for the whole pipeline.

Every part of the analysis stack (perceptual hashing, CLIP/aesthetic scoring,
face detection, captioning, thumbnailing, sharpness) opens images through
`load_rgb` / `load_gray_u8` here, and nowhere else. A guard test
(`tests/test_imaging_guard.py`) fails the build if a bare `Image.open(` or
`cv2.imread(` shows up elsewhere under `sift/`, so adding a new input format
stays a one-file change in this module.

RAW formats (Nikon NEF, Fuji RAF, etc.) are decoded via their *embedded camera
JPEG preview* — the full-look render the camera already made. For a culling tool
that's the right input: it matches what the photographer sees, it's fast, and it
sidesteps libraw's flat default demosaic and Fuji X-Trans artifacts. We never
write JPGs to disk; decode is in-memory only.

Crucially, the returned image preserves EXIF (orientation + capture datetime),
so downstream `exif_transpose` and `read_capture_time` behave the same for RAW
previews as for ordinary JPEGs — and every consumer sees the *same* preview
pixel space, which is what keeps phash sizes, face bboxes and thumbnail
dimensions on one consistent coordinate system.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# Decoded via rawpy's embedded preview. Add a format here and it flows through
# the entire pipeline (this is the one place to edit).
RAW_EXTENSIONS = {".nef", ".raf", ".cr2", ".cr3", ".arw", ".dng", ".orf", ".rw2"}


def is_raw(path) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def _load_raw_preview(p: Path) -> Image.Image:
    """Return a RAW file's embedded camera JPEG preview as a PIL image.

    Falls back to a fast half-size libraw demosaic only when a file carries no
    usable embedded preview (rare on modern bodies), so an exotic RAW still
    loads instead of crashing the run."""
    import io
    import rawpy

    with rawpy.imread(str(p)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return Image.open(io.BytesIO(thumb.data))   # carries EXIF
            return Image.fromarray(thumb.data)              # bitmap preview
        except (rawpy.LibRawNoThumbnailError,
                rawpy.LibRawUnsupportedThumbnailError):
            rgb = raw.postprocess(half_size=True, use_camera_wb=True)
            return Image.fromarray(rgb)


def load_rgb(path) -> Image.Image:
    """Open any supported image as a PIL image, EXIF preserved. RAW -> embedded
    camera preview. This is the canonical loader; do not call Image.open directly
    elsewhere in the pipeline (see module docstring)."""
    p = Path(path)
    if p.suffix.lower() in RAW_EXTENSIONS:
        return _load_raw_preview(p)
    return Image.open(p)


def load_gray_u8(path) -> np.ndarray | None:
    """Grayscale uint8 array for the OpenCV sharpness path (cv2.imread can't read
    RAW). Returns None when the image can't be opened, matching cv2.imread's
    None-on-failure contract so callers keep their existing guards."""
    try:
        return np.asarray(load_rgb(path).convert("L"))
    except Exception as e:
        print(f"  load error {getattr(path, 'name', path)}: {e}")
        return None


# ── Upright (EXIF-corrected) coordinate space ────────────────────────────────
# Everything the UI positions against an image — stored imgw/imgh, face boxes —
# lives in the *upright* frame: the pixels as displayed after EXIF orientation is
# applied, which is also what thumbnails and /img serve. (Until the "upright"
# report format these were raw sensor coordinates, so a portrait-orientation
# camera frame got sideways face detection and misplaced overlays.)
COORDS = "upright"
ORIENTATION_TAG = 0x0112


def load_upright(path) -> Image.Image:
    """Open an image as RGB with EXIF orientation applied."""
    with load_rgb(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def exif_orientation(im) -> int:
    try:
        return int(im.getexif().get(ORIENTATION_TAG, 1) or 1)
    except Exception:
        return 1


def upright_size(size, orientation: int) -> tuple:
    """(w, h) of an image after EXIF transposition."""
    w, h = size
    return (h, w) if orientation in (5, 6, 7, 8) else (w, h)


def read_upright_size(path) -> tuple:
    """Upright (w, h) from the header alone — no full decode for ordinary files."""
    with load_rgb(path) as im:
        return upright_size(im.size, exif_orientation(im))


def upright_box(box, size, orientation: int) -> list:
    """Map an (x1, y1, x2, y2) box from raw sensor coordinates of a `size` =
    (w, h) image into the upright frame — the same geometry ``exif_transpose``
    applies to the pixels. Used once, to migrate face edits recorded before
    boxes were stored upright."""
    w, h = size
    x1, y1, x2, y2 = box

    def pt(x, y):
        return {
            2: (w - x, y), 3: (w - x, h - y), 4: (x, h - y), 5: (y, x),
            6: (h - y, x), 7: (h - y, w - x), 8: (y, w - x),
        }.get(orientation, (x, y))

    (ax, ay), (bx, by) = pt(x1, y1), pt(x2, y2)
    return [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
