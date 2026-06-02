"""End-to-end pipeline test (model-free).

This is the only test that runs the real `photo_audit.py` CLI as a subprocess
and then feeds its report through `build_db` into the live API — exercising all
three stages and the CLI→report→DB→server seams the way the app actually runs.
It uses `--no-clip`, so only the classical paths run (Laplacian sharpness +
perceptual-hash duplicates); no ML weights are loaded, so it's CI-safe.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import build_db
import server
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "photo_audit.py"


def _write_library(lib: Path):
    """Four real JPEGs: a byte-identical duplicate pair, one unique noise image
    (distinct phash, high sharpness), and one flat gray image (~0 sharpness)."""
    np = pytest.importorskip("numpy")
    from PIL import Image
    lib.mkdir(parents=True, exist_ok=True)

    noise = np.random.default_rng(0).integers(0, 256, (128, 128, 3), dtype="uint8")
    other = np.random.default_rng(99).integers(0, 256, (128, 128, 3), dtype="uint8")
    Image.fromarray(noise).save(lib / "dupe_a.jpg", "JPEG", quality=95)
    Image.fromarray(noise).save(lib / "dupe_b.jpg", "JPEG", quality=95)   # identical bytes
    Image.fromarray(other).save(lib / "unique.jpg", "JPEG", quality=95)
    Image.new("RGB", (128, 128), (127, 127, 127)).save(lib / "flat.jpg", "JPEG")


def _run_audit(lib: Path, out: Path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    return subprocess.run(
        [sys.executable, str(AUDIT), str(lib), "--recurse", "--no-clip",
         "--out", str(out)],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


def test_pipeline_no_clip_end_to_end(tmp_path):
    pytest.importorskip("cv2")
    pytest.importorskip("imagehash")
    lib = tmp_path / "lib"
    _write_library(lib)
    report = tmp_path / "audit_report.json"

    # ── Stage 1: the real CLI ──
    proc = _run_audit(lib, report)
    assert proc.returncode == 0, f"photo_audit failed:\n{proc.stdout}\n{proc.stderr}"
    assert report.exists()

    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["backend"] == "none"            # --no-clip: no aesthetic model
    assert data["total_images"] == 4
    assert data["duplicate_groups"] >= 1
    by_name = {r["filename"]: r for r in data["images"]}

    # The byte-identical pair is grouped; the flat image is the least sharp.
    assert by_name["dupe_a.jpg"]["dup_group"] is not None
    assert by_name["dupe_a.jpg"]["dup_group"] == by_name["dupe_b.jpg"]["dup_group"]
    assert by_name["unique.jpg"]["dup_group"] != by_name["dupe_a.jpg"]["dup_group"]
    assert by_name["flat.jpg"]["sharpness"] == min(
        v["sharpness"] for v in by_name.values())

    # ── Stage 2: ingest ──
    db_path = tmp_path / "photos.db"
    thumbs = tmp_path / ".thumbs"
    build_db.build(report, db_path, thumbs, thumb_size=128, thumb_quality=80,
                   workers=2, skip_thumbs=False, force_thumbs=False, prune=False)
    # The duplicate pair is byte-identical → one shared content-hashed thumbnail.
    assert len(list(thumbs.glob("*.webp"))) == 3

    # ── Stage 3: serve ──
    server.DB_PATH = db_path
    server.THUMB_DIR = thumbs
    client = TestClient(server.app)

    assert client.get("/api/meta").json()["counts"]["total"] == 4

    groups = client.get("/api/groups?limit=50").json()
    assert groups["total"] >= 1
    grouped_names = {it["filename"] for g in groups["groups"] for it in g["items"]}
    assert {"dupe_a.jpg", "dupe_b.jpg"} <= grouped_names

    # Sharpness sort (ascending) puts the flat image first.
    items = client.get("/api/images?limit=50&sort=sharpness&dir=asc").json()["items"]
    assert items[0]["filename"] == "flat.jpg"
