"""Shared pytest fixtures + helpers for the photo-audit webapp tests.

Everything builds a synthetic audit_report.json, ingests it with build_db into a
throwaway SQLite DB, and drives the FastAPI app through a TestClient — no ML
models required. Real image files are only created for the tests that actually
need bytes on disk (thumbnails, apply/undo file moves, duplicate-location).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "webapp"
# webapp first so `import server/build_db/photodb` resolve as the scripts do.
sys.path.insert(0, str(WEBAPP))
sys.path.insert(0, str(ROOT))

import build_db          # noqa: E402
import photodb           # noqa: E402
import server            # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# ── Synthetic report ──────────────────────────────────────────────────────────

def default_report():
    """Three images: two in a duplicate group, one standalone with two faces.
    Matches the fixture the original test_api.py relied on."""
    return {
        "folder": "/fake",
        "backend": "para",
        "caption_model": None,
        "face_model": "mtcnn+vggface2",
        "face_expr_model": "clip-b32-expr",
        "duplicate_groups": 1,
        "images": [
            {"path": "/fake/a.jpg", "filename": "a.jpg", "combined": 0.80,
             "sharpness": 0.70, "para_aesthetic": 0.75, "dup_group": 0, "imgw": 400, "imgh": 300,
             "faces": [{"bbox": [10.0, 10.0, 50.0, 50.0], "prob": 0.99,
                        "cluster_id": 0, "name": None, "sharp": 0.90, "expr": 0.80}]},
            {"path": "/fake/b.jpg", "filename": "b.jpg", "combined": 0.60,
             "sharpness": 0.50, "para_aesthetic": 0.55, "dup_group": 0, "imgw": 400, "imgh": 300,
             "faces": [{"bbox": [20.0, 20.0, 60.0, 60.0], "prob": 0.95,
                        "cluster_id": 1, "name": None, "sharp": 0.30, "expr": 0.40}]},
            {"path": "/fake/c.jpg", "filename": "c.jpg", "combined": 0.50,
             "sharpness": 0.40, "para_aesthetic": 0.45, "dup_group": None, "imgw": 400, "imgh": 300,
             "faces": [
                 {"bbox": [5.0, 5.0, 80.0, 80.0], "prob": 0.97,
                  "cluster_id": 0, "name": None, "sharp": 0.60, "expr": 0.50},
                 {"bbox": [100.0, 100.0, 120.0, 120.0], "prob": 0.91,
                  "cluster_id": 1, "name": None, "sharp": 0.20, "expr": 0.30}]},
        ],
    }


def ingest(tmp_path, report=None, *, skip_thumbs=True, prune=False,
           thumb_size=400, force_thumbs=False):
    """Write `report` to disk and build the DB + (optionally) thumbnails.
    Returns (report_path, db_path, thumbs_dir)."""
    report = default_report() if report is None else report
    report_path = tmp_path / "audit_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    db_path = tmp_path / "photos.db"
    thumbs = tmp_path / ".thumbs"
    build_db.build(report_path, db_path, thumbs,
                   thumb_size=thumb_size, thumb_quality=80, workers=2,
                   skip_thumbs=skip_thumbs, force_thumbs=force_thumbs, prune=prune)
    return report_path, db_path, thumbs


class Env:
    def __init__(self, client, report, db, thumbs):
        self.client = client
        self.report = report
        self.db = db
        self.thumbs = thumbs


@pytest.fixture()
def make_env(tmp_path):
    """Factory fixture: build a DB from an optional custom report and wire a
    TestClient to it. Call with a report dict to exercise specific data."""
    def _make(report=None, **kw):
        report_path, db_path, thumbs = ingest(tmp_path, report, **kw)
        server.DB_PATH = db_path
        server.THUMB_DIR = thumbs
        return Env(TestClient(server.app), report_path, db_path, thumbs)
    return _make


@pytest.fixture()
def env(make_env):
    """The default 3-image library."""
    return make_env()


@pytest.fixture()
def rebuild():
    """Re-ingest an env's report (simulating a fresh build_db run)."""
    def _rebuild(env, **kw):
        kw.setdefault("skip_thumbs", True)
        kw.setdefault("prune", False)
        build_db.build(env.report, env.db, env.thumbs,
                       thumb_size=kw.pop("thumb_size", 400), thumb_quality=80,
                       workers=2, force_thumbs=kw.pop("force_thumbs", False), **kw)
    return _rebuild


# ── Helpers ─────────────────────────────────────────────────────────────────

def items_by_name(client, query="?limit=50"):
    return {it["filename"]: it for it in client.get(f"/api/images{query}").json()["items"]}


def face_id(client, filename, which=0):
    return items_by_name(client)[filename]["faces"][which]["id"]


def tiny_jpeg(path: Path, color=(120, 120, 120), size=(16, 16)):
    """Write a real, decodable JPEG so PIL/thumbnailing works."""
    from PIL import Image
    Image.new("RGB", size, color).save(path, "JPEG")


@pytest.fixture()
def real_library(tmp_path):
    """Build a library backed by real files on disk, so apply/undo, thumbnail
    generation, and duplicate-location tests have actual bytes to operate on.

    Returns a callable: real_library(specs, **build_kw) -> Env, where `specs`
    is a list of (filename, combined, dup_group, content_marker) tuples. Files
    sharing a content_marker get byte-identical contents (same content hash).
    """
    import hashlib
    from PIL import Image

    lib = tmp_path / "lib"
    lib.mkdir()

    def _color(marker):
        d = hashlib.md5(marker.encode("utf-8")).digest()
        return (d[0], d[1], d[2])

    def _make(specs, *, skip_thumbs=True, prune=False, thumb_size=400):
        images = []
        for fn, combined, dup, marker in specs:
            fpath = lib / fn
            # A real, decodable JPEG. Identical marker -> identical pixels ->
            # byte-identical file -> identical content hash (deterministic save).
            Image.new("RGB", (64, 64), _color(marker)).save(fpath, "JPEG", quality=90)
            images.append({
                "path": str(fpath), "filename": fn, "combined": combined,
                "sharpness": combined, "para_aesthetic": combined,
                "dup_group": dup, "imgw": 100, "imgh": 100, "faces": [],
            })
        report = {"folder": str(lib), "backend": "para", "caption_model": None,
                  "face_model": None, "duplicate_groups": 0, "images": images}
        report_path = tmp_path / "audit_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        db_path = tmp_path / "photos.db"
        thumbs = tmp_path / ".thumbs"
        build_db.build(report_path, db_path, thumbs, thumb_size=thumb_size,
                       thumb_quality=80, workers=2, skip_thumbs=skip_thumbs,
                       force_thumbs=False, prune=prune)
        server.DB_PATH = db_path
        server.THUMB_DIR = thumbs
        env = Env(TestClient(server.app), report_path, db_path, thumbs)
        env.lib = lib
        return env

    return _make
