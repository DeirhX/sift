"""Coverage for build_db ingest internals: decision persistence + the one-time
path->hash decision migration, and thumbnail generation/pruning."""
import json
import sqlite3

from PIL import Image

import build_db
from conftest import default_report, ingest, items_by_name


def _decisions_columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    finally:
        conn.close()


def test_decision_persists_across_rebuild(env, rebuild):
    h = items_by_name(env.client)["a.jpg"]["hash"]
    env.client.post("/api/decisions", json={"hash": h, "decision": "keep"})
    rebuild(env)
    assert items_by_name(env.client)["a.jpg"]["decision"] == "keep"


def test_path_keyed_decisions_migrated_to_hash(tmp_path):
    report_path, db_path, thumbs = ingest(tmp_path)
    # Downgrade to a legacy, path-keyed decisions table, then re-ingest.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE decisions")
    conn.execute("CREATE TABLE decisions (path TEXT PRIMARY KEY, decision TEXT)")
    conn.execute("INSERT INTO decisions (path, decision) VALUES ('/fake/a.jpg', 'keep')")
    conn.commit()
    conn.close()

    build_db.build(report_path, db_path, thumbs, thumb_size=400, thumb_quality=80,
                   workers=2, skip_thumbs=True, force_thumbs=False, prune=False)

    # Table is now hash-keyed and the mark followed the file's content hash.
    assert "hash" in _decisions_columns(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT d.decision FROM images i JOIN decisions d ON d.hash = i.content_hash "
        "WHERE i.filename='a.jpg'").fetchone()
    conn.close()
    assert row["decision"] == "keep"


def test_cluster_names_survive_rebuild(env, rebuild):
    env.client.post("/api/clusters", json={"cluster_id": 0, "name": "Bob"})
    rebuild(env)
    meta = {c["cluster_id"]: c for c in env.client.get("/api/meta").json()["clusters"]}
    assert meta[0]["name"] == "Bob"


# ── thumbnails ───────────────────────────────────────────────────────────────

def _write_lib(tmp_path, names_colors):
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    images = []
    for fn, color in names_colors:
        Image.new("RGB", (32, 32), color).save(lib / fn, "JPEG")
        images.append({"path": str(lib / fn), "filename": fn, "combined": 0.5,
                       "sharpness": 0.5, "dup_group": None, "faces": []})
    report = {"folder": str(lib), "backend": "para", "duplicate_groups": 0,
              "images": images}
    return lib, report


def test_thumbnails_generated_per_image(tmp_path):
    lib, report = _write_lib(tmp_path, [("a.jpg", (10, 20, 30)), ("b.jpg", (200, 100, 50))])
    report_path = tmp_path / "audit_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    thumbs = tmp_path / ".thumbs"
    build_db.build(report_path, tmp_path / "photos.db", thumbs, thumb_size=64,
                   thumb_quality=80, workers=2, skip_thumbs=False, force_thumbs=False)
    assert len(list(thumbs.glob("*.webp"))) == 2


def test_orphan_thumbnails_pruned(tmp_path):
    lib, report = _write_lib(tmp_path, [("a.jpg", (10, 20, 30)), ("b.jpg", (200, 100, 50))])
    report_path = tmp_path / "audit_report.json"
    db_path = tmp_path / "photos.db"
    thumbs = tmp_path / ".thumbs"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    build_db.build(report_path, db_path, thumbs, thumb_size=64, thumb_quality=80,
                   workers=2, skip_thumbs=False, force_thumbs=False, prune=True)
    assert len(list(thumbs.glob("*.webp"))) == 2

    # Re-ingest a report that no longer references b.jpg -> its thumb is orphaned.
    report["images"] = report["images"][:1]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    build_db.build(report_path, db_path, thumbs, thumb_size=64, thumb_quality=80,
                   workers=2, skip_thumbs=False, force_thumbs=False, prune=True)
    assert len(list(thumbs.glob("*.webp"))) == 1
