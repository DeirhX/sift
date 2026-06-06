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
from PIL import Image

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
