#!/usr/bin/env python3
"""Launch the real FastAPI server against a freshly-seeded fixture library, for
Playwright e2e runs. Builds a small set of real JPEGs + an audit report, ingests
them with build_db, then serves the production frontend build from dist.

Run by playwright.config.js as the webServer. Port via $E2E_PORT (default 8765).
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # frontend/e2e
FRONTEND = HERE.parent                                  # frontend
REPO_ROOT = FRONTEND.parent
sys.path.insert(0, str(REPO_ROOT))

from sift.web import build_db, server                  # noqa: E402
from PIL import Image                                   # noqa: E402

PORT = int(os.environ.get("E2E_PORT", "8765"))
FIXROOT = HERE / ".fixtures"
LIB = FIXROOT / "lib"
DB = FIXROOT / "photos.db"
THUMBS = FIXROOT / ".thumbs"


def _img(name, color, size=(80, 60)):
    LIB.mkdir(parents=True, exist_ok=True)
    p = LIB / name
    Image.new("RGB", size, color).save(p, "JPEG", quality=85)
    return str(p)


def seed():
    # Distinct combined scores -> stable sort order in the grid.
    # Scenes (rough hierarchy): scene 0 = beach burst + a loose city shot
    # (one near-dup set nested + one loose member); scene 1 = portrait + cat
    # (two loose members, no near-dups); blurry is a lone singleton (scene None).
    images = [
        {"path": _img("beach.jpg", (40, 120, 200)), "filename": "beach.jpg",
         "combined": 0.95, "sharpness": 0.9, "para_aesthetic": 0.92, "dup_group": 0,
         "scene_group": 0, "capture_time": 1000.0,
         "imgw": 80, "imgh": 60, "caption": "a sunny beach", "tags": ["beach", "summer"],
         "faces": []},
        {"path": _img("portrait.jpg", (200, 160, 140)), "filename": "portrait.jpg",
         "combined": 0.80, "sharpness": 0.8, "para_aesthetic": 0.78, "dup_group": None,
         "scene_group": 1, "capture_time": 5000.0,
         "imgw": 80, "imgh": 60, "caption": "a portrait of a person", "tags": ["people"],
         "faces": [{"bbox": [10.0, 8.0, 40.0, 45.0], "prob": 0.99, "cluster_id": 0,
                    "name": "Alice", "sharp": 0.9, "expr": 0.8}]},
        {"path": _img("city.jpg", (90, 90, 90)), "filename": "city.jpg",
         "combined": 0.70, "sharpness": 0.7, "para_aesthetic": 0.66, "dup_group": None,
         "scene_group": 0, "capture_time": 1010.0,
         "imgw": 80, "imgh": 60, "caption": "a city street at night", "tags": ["city"],
         "faces": []},
        {"path": _img("beach2.jpg", (45, 125, 205)), "filename": "beach2.jpg",
         "combined": 0.60, "sharpness": 0.55, "para_aesthetic": 0.58, "dup_group": 0,
         "scene_group": 0, "capture_time": 1005.0,
         "imgw": 80, "imgh": 60, "caption": "a sunny beach again", "tags": ["beach"],
         "faces": []},
        {"path": _img("cat.jpg", (180, 150, 60)), "filename": "cat.jpg",
         "combined": 0.50, "sharpness": 0.5, "para_aesthetic": 0.48, "dup_group": None,
         "scene_group": 1, "capture_time": 5030.0,
         "imgw": 80, "imgh": 60, "caption": "a cat on a sofa", "tags": ["animals"],
         "faces": []},
        {"path": _img("blurry.jpg", (30, 30, 30)), "filename": "blurry.jpg",
         "combined": 0.20, "sharpness": 0.15, "para_aesthetic": 0.22, "dup_group": None,
         "scene_group": None, "capture_time": 99999.0,
         "imgw": 80, "imgh": 60, "caption": "a blurry shot", "tags": [], "faces": []},
    ]
    # Optional scale seed (E2E_SCALE=N): append N single-photo scenes so the
    # pile overview has hundreds of piles. This is what the windowing e2e scrolls
    # through to prove the DOM stays bounded in a real browser. Distinct colours
    # keep content hashes unique (no dedup collapse); low scores keep them below
    # the base fixtures so the existing sort-order tests are unaffected.
    scale = int(os.environ.get("E2E_SCALE", "0"))
    for i in range(scale):
        color = (i % 256, (i * 7) % 256, (i * 13) % 256)
        images.append(
            {"path": _img(f"filler_{i:04d}.jpg", color, (16, 16)),
             "filename": f"filler_{i:04d}.jpg",
             "combined": 0.05, "sharpness": 0.05, "para_aesthetic": 0.05,
             "dup_group": None, "scene_group": 100 + i, "capture_time": 20000.0 + i * 60.0,
             "imgw": 16, "imgh": 16, "caption": f"filler scene {i}",
             "tags": [f"f{i % 8}"], "faces": []})

    report = {"folder": str(LIB), "backend": "para", "caption_model": "blip",
              "face_model": "mtcnn+vggface2", "scene_model": "exif+clip-b32",
              "duplicate_groups": 1, "scene_groups": 2 + scale, "images": images}
    FIXROOT.mkdir(parents=True, exist_ok=True)
    # Start from a clean DB every run: build_db deliberately *preserves*
    # decisions across rebuilds, so a stale photos.db would leak verdicts from
    # a previous run and make tests non-deterministic.
    for leftover in (DB, DB.with_suffix(".db-wal"), DB.with_suffix(".db-shm")):
        leftover.unlink(missing_ok=True)
    report_path = FIXROOT / "audit_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    # Skip thumbnail generation in scale mode: the windowing test counts DOM
    # nodes, not pixels, so hundreds of thumbnails would only slow startup.
    build_db.build(report_path, DB, THUMBS, thumb_size=400, thumb_quality=80,
                   workers=4, skip_thumbs=scale > 0, force_thumbs=scale == 0, prune=True)


def main():
    seed()
    server._init_runtime(DB, THUMBS, FRONTEND / "dist", None)
    import uvicorn
    print(f"e2e fixture server on http://127.0.0.1:{PORT}  (db={DB})")
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
