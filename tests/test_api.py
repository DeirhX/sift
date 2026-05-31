"""
API + ingest tests for the photo audit web app.

These exercise build_db's portrait aggregation + face-override replay and the
server's face/cluster mutation endpoints against a synthetic SQLite DB — no ML
models or real image files required (thumbnails are skipped; unreadable paths
fall back to a stable path-hash).

Run:  pytest -q
"""
import json
import sys
from pathlib import Path

import pytest

# Make the webapp package importable regardless of pytest's cwd.
WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
sys.path.insert(0, str(WEBAPP))

import build_db          # noqa: E402
import server            # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _report():
    """Three images: two in a duplicate group, one standalone with two faces."""
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


def _build(tmp_path):
    report_path = tmp_path / "audit_report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    db_path = tmp_path / "photos.db"
    thumbs = tmp_path / ".thumbs"
    build_db.build(report_path, db_path, thumbs,
                   thumb_size=400, thumb_quality=80, workers=2,
                   skip_thumbs=True, force_thumbs=False, prune=False)
    return report_path, db_path, thumbs


@pytest.fixture()
def env(tmp_path):
    report_path, db_path, thumbs = _build(tmp_path)
    server.DB_PATH = db_path
    server.THUMB_DIR = thumbs
    client = TestClient(server.app)
    return type("Env", (), {"client": client, "report": report_path,
                            "db": db_path, "thumbs": thumbs})


def _rebuild(env):
    build_db.build(env.report, env.db, env.thumbs,
                   thumb_size=400, thumb_quality=80, workers=2,
                   skip_thumbs=True, force_thumbs=False, prune=False)


# ── build_db aggregation ─────────────────────────────────────────────────────

def test_portrait_aggregation(env):
    items = {it["filename"]: it for it in env.client.get("/api/images?limit=50").json()["items"]}
    # largest face per image drives the aggregate; sharp dominates (0.6/0.4)
    assert items["a.jpg"]["portrait"] == pytest.approx(0.6 * 0.9 + 0.4 * 0.8, abs=1e-3)
    assert items["b.jpg"]["portrait"] == pytest.approx(0.6 * 0.3 + 0.4 * 0.4, abs=1e-3)
    # c.jpg: largest face is the 75x75 one (sharp 0.6), not the 20x20 (sharp 0.2)
    assert items["c.jpg"]["portrait"] == pytest.approx(0.6 * 0.6 + 0.4 * 0.5, abs=1e-3)
    # per-face fields exposed
    fa = items["a.jpg"]["faces"][0]
    assert fa["sharp"] == pytest.approx(0.9) and fa["expr"] == pytest.approx(0.8)
    assert "id" in fa


def test_meta_has_portrait(env):
    meta = env.client.get("/api/meta").json()
    assert meta["has_portrait"] is True
    assert meta["counts"]["with_portrait"] == 3
    assert "portrait" in meta["histograms"]


def test_portrait_filter(env):
    # portrait_min 0.5 keeps a.jpg (0.86) and c.jpg (0.56), drops b.jpg (0.34)
    items = env.client.get("/api/images?limit=50&portrait_min=0.5").json()["items"]
    names = {it["filename"] for it in items}
    assert names == {"a.jpg", "c.jpg"}


def test_portrait_sort(env):
    items = env.client.get("/api/images?limit=50&sort=portrait&dir=desc").json()["items"]
    ports = [it["portrait"] for it in items if it["portrait"] is not None]
    assert ports == sorted(ports, reverse=True)


# ── group filtering + match flags ────────────────────────────────────────────

def test_group_match_flags(env):
    data = env.client.get("/api/groups?limit=50&portrait_min=0.5").json()
    assert data["total"] == 1                      # group 0 has a matching member
    g = data["groups"][0]
    assert g["count"] == 2 and g["match_count"] == 1
    by_name = {it["filename"]: it["matches"] for it in g["items"]}
    assert by_name == {"a.jpg": True, "b.jpg": False}


# ── face mutations + override persistence ────────────────────────────────────

def _face_id(env, filename, which=0):
    items = {it["filename"]: it for it in env.client.get("/api/images?limit=50").json()["items"]}
    return items[filename]["faces"][which]["id"]


def test_assign_new_person_persists(env):
    fid = _face_id(env, "b.jpg")
    r = env.client.post(f"/api/faces/{fid}/assign", json={"new_person": True, "name": "Alice"}).json()
    assert r["cluster_id"] >= server.MANUAL_CLUSTER_BASE
    new_cid = r["cluster_id"]
    # survives a fresh ingest
    _rebuild(env)
    items = {it["filename"]: it for it in env.client.get("/api/images?limit=50").json()["items"]}
    assert items["b.jpg"]["faces"][0]["cluster_id"] == new_cid
    meta = {c["cluster_id"]: c for c in env.client.get("/api/meta").json()["clusters"]}
    assert meta[new_cid]["name"] == "Alice"


def test_merge_persists(env):
    r = env.client.post("/api/clusters/merge", json={"from": 1, "into": 0}).json()
    assert r["moved"] == 2                          # b.jpg face + c.jpg small face
    _rebuild(env)
    cids = {c["cluster_id"] for c in env.client.get("/api/meta").json()["clusters"]}
    assert 1 not in cids and 0 in cids


def test_delete_face_persists(env):
    fid = _face_id(env, "c.jpg", which=1)           # the small 20x20 face
    env.client.delete(f"/api/faces/{fid}")
    items = {it["filename"]: it for it in env.client.get("/api/images?limit=50").json()["items"]}
    assert len(items["c.jpg"]["faces"]) == 1
    _rebuild(env)
    items = {it["filename"]: it for it in env.client.get("/api/images?limit=50").json()["items"]}
    assert len(items["c.jpg"]["faces"]) == 1        # stayed deleted across ingest


def test_rename_cluster(env):
    env.client.post("/api/clusters", json={"cluster_id": 0, "name": "Bob"})
    meta = {c["cluster_id"]: c for c in env.client.get("/api/meta").json()["clusters"]}
    assert meta[0]["name"] == "Bob"
