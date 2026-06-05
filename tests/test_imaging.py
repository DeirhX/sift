"""Unit tests for the central image loader (sift.audit.imaging).

The RAW path is exercised with a faked `rawpy` module (we have no real NEF/RAF
fixture and don't want to ship multi-MB binaries), asserting the dispatch,
embedded-preview decode, EXIF passthrough, and fallbacks. The non-RAW path and
the extension wiring use real files.
"""
import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

from sift.audit import imaging
from sift.audit.imaging import load_rgb, load_gray_u8, is_raw, RAW_EXTENSIONS
from sift.audit.cli import IMAGE_EXTENSIONS


def _jpeg_with_exif(color=(10, 20, 30), size=(64, 48)) -> bytes:
    img = Image.new("RGB", size, color)
    exif = img.getexif()
    exif[0x0112] = 6                       # Orientation: rotate 90 CW
    sub = exif.get_ifd(0x8769)             # ExifIFD
    sub[0x9003] = "2021:07:15 12:34:56"    # DateTimeOriginal
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _fake_rawpy(*, jpeg=None, bitmap=None, raise_no_thumb=False):
    """A stand-in `rawpy` module: imread(...).extract_thumb() yields the JPEG or
    bitmap preview we hand it, or raises so the postprocess fallback kicks in."""
    m = types.SimpleNamespace()

    class ThumbFormat:
        JPEG = "JPEG"
        BITMAP = "BITMAP"

    class LibRawNoThumbnailError(Exception):
        pass

    class LibRawUnsupportedThumbnailError(Exception):
        pass

    m.ThumbFormat = ThumbFormat
    m.LibRawNoThumbnailError = LibRawNoThumbnailError
    m.LibRawUnsupportedThumbnailError = LibRawUnsupportedThumbnailError

    class FakeRaw:
        def __enter__(self_):
            return self_

        def __exit__(self_, *exc):
            return False

        def extract_thumb(self_):
            if raise_no_thumb:
                raise LibRawNoThumbnailError("no preview")
            if bitmap is not None:
                return types.SimpleNamespace(data=bitmap, format=ThumbFormat.BITMAP)
            return types.SimpleNamespace(data=jpeg, format=ThumbFormat.JPEG)

        def postprocess(self_, **kw):
            return np.full((12, 16, 3), 128, dtype=np.uint8)

    m.imread = lambda path: FakeRaw()
    return m


# ── extension wiring ──────────────────────────────────────────────────────────

def test_raw_extensions_are_discoverable():
    assert {".nef", ".raf"} <= RAW_EXTENSIONS
    assert {".nef", ".raf"} <= IMAGE_EXTENSIONS          # scanner picks them up
    assert ".jpg" in IMAGE_EXTENSIONS                    # ordinary formats kept


def test_is_raw_is_case_insensitive():
    assert is_raw("DSC_0001.NEF")
    assert is_raw("x.raf")
    assert not is_raw("x.jpg")


# ── RAW path (faked rawpy) ────────────────────────────────────────────────────

def test_load_rgb_decodes_embedded_jpeg_preview(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "rawpy", _fake_rawpy(jpeg=_jpeg_with_exif()))
    im = load_rgb(tmp_path / "shot.nef")                 # file need not exist (imread faked)
    assert im.size == (64, 48)
    # EXIF survives so downstream exif_transpose / capture-time still work.
    assert im.getexif().get(0x0112) == 6
    assert im.getexif().get_ifd(0x8769).get(0x9003) == "2021:07:15 12:34:56"


def test_capture_time_reads_through_raw_preview(monkeypatch, tmp_path):
    from sift.audit.grouping import read_capture_time
    monkeypatch.setitem(sys.modules, "rawpy", _fake_rawpy(jpeg=_jpeg_with_exif()))
    from datetime import datetime
    expected = datetime.strptime("2021:07:15 12:34:56", "%Y:%m:%d %H:%M:%S").timestamp()
    assert read_capture_time(tmp_path / "shot.nef") == pytest.approx(expected)


def test_load_rgb_bitmap_preview_fallback(monkeypatch, tmp_path):
    bmp = np.zeros((8, 10, 3), dtype=np.uint8)
    monkeypatch.setitem(sys.modules, "rawpy", _fake_rawpy(bitmap=bmp))
    im = load_rgb(tmp_path / "shot.raf")
    assert im.size == (10, 8)                            # PIL is (w, h)


def test_load_rgb_postprocess_fallback_when_no_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "rawpy", _fake_rawpy(raise_no_thumb=True))
    im = load_rgb(tmp_path / "shot.cr2")
    assert im.size == (16, 12)                           # from fake postprocess


# ── non-RAW path + grayscale helper ───────────────────────────────────────────

def test_load_rgb_non_raw_delegates_to_pillow(tmp_path):
    p = tmp_path / "real.jpg"
    Image.new("RGB", (20, 10), (1, 2, 3)).save(p)
    im = load_rgb(p)
    assert im.size == (20, 10)


def test_load_gray_u8_shape_and_dtype(tmp_path):
    p = tmp_path / "real.png"
    Image.new("RGB", (20, 10), (200, 200, 200)).save(p)
    arr = load_gray_u8(p)
    assert arr is not None
    assert arr.ndim == 2 and arr.shape == (10, 20)       # numpy is (h, w)
    assert arr.dtype == np.uint8


def test_load_gray_u8_returns_none_on_failure(tmp_path):
    assert load_gray_u8(tmp_path / "does_not_exist.jpg") is None
