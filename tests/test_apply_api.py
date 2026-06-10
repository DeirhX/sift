"""Coverage for the reversible Trash flow: moving 'del' files into _trash/,
status accounting, restore, and emptying. Uses real files on disk."""
import sqlite3
from pathlib import Path

from conftest import items_by_name


SPECS = [
    ("x.jpg", 0.80, 0, "mx"),
    ("y.jpg", 0.60, 0, "my"),
    ("z.jpg", 0.50, None, "mz"),
]


def _mark(env, filename, decision):
    h = items_by_name(env.client)[filename]["hash"]
    env.client.post("/api/decisions", json={"hash": h, "decision": decision})


def test_apply_status_counts_pending(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    status = env.client.get("/api/apply/status").json()
    assert status["pending"] == 1 and status["applied"] == 0
    assert status["trash_dir"].endswith("_trash")


def test_apply_moves_del_files(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    r = env.client.post("/api/apply").json()
    assert r["moved"] == 1 and r["skipped"] == 0

    trash = env.lib / "_trash"
    assert (trash / "y.jpg").exists()
    assert not (env.lib / "y.jpg").exists()
    # The DB path follows the file; the decision is unchanged (hash-keyed).
    trash_items = items_by_name(env.client, "?limit=50&trash=trashed")
    moved = trash_items["y.jpg"]
    assert Path(moved["path"]) == trash / "y.jpg"
    assert moved["decision"] == "del"
    assert moved["trash_state"] == "trashed"
    # Decision + lifecycle are orthogonal: the trashed file is del-marked, so it
    # shows under trashed+del but not trashed+keep.
    assert "y.jpg" in items_by_name(env.client, "?limit=50&trash=trashed&decision=del")
    assert "y.jpg" not in items_by_name(env.client, "?limit=50&trash=trashed&decision=keep")
    # Show is multi-select: active,trashed spans both the live library and Trash,
    # while active alone (the default) hides the trashed file.
    both = items_by_name(env.client, "?limit=50&trash=active,trashed")
    assert {"x.jpg", "z.jpg"} <= set(both) and "y.jpg" in both
    assert "y.jpg" not in items_by_name(env.client, "?limit=50&trash=active")
    # Status reflects the applied move.
    status = env.client.get("/api/apply/status").json()
    assert status["pending"] == 0 and status["applied"] == 1
    assert "y.jpg" not in items_by_name(env.client)


def test_apply_undo_restores(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    env.client.post("/api/apply")
    r = env.client.post("/api/apply/undo").json()
    assert r["restored"] == 1
    assert (env.lib / "y.jpg").exists()
    assert not (env.lib / "_trash" / "y.jpg").exists()
    assert items_by_name(env.client)["y.jpg"]["path"] == str(env.lib / "y.jpg")
    # Log cleared, so nothing is left to undo.
    assert env.client.get("/api/apply/status").json()["applied"] == 0


def test_apply_undo_marks_missing_trash_file_missing(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    env.client.post("/api/apply")
    (env.lib / "_trash" / "y.jpg").unlink()

    r = env.client.post("/api/apply/undo").json()

    assert r == {"restored": 0, "skipped": 1}
    with sqlite3.connect(env.db) as conn:
        state = conn.execute("SELECT state FROM trash_moves").fetchone()[0]
    assert state == "missing"
    assert "y.jpg" not in items_by_name(env.client)


def test_apply_empty_trash_deletes(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    env.client.post("/api/apply")
    start = env.client.post("/api/tasks", json={"type": "empty_trash", "params": {}}).json()
    import time
    for _ in range(100):
        done = env.client.get(f"/api/tasks/{start['id']}").json()
        if done["state"] != "running":
            break
        time.sleep(0.05)
    assert done["state"] == "done"
    assert done["result"]["deleted"] == 1
    assert not (env.lib / "_trash" / "y.jpg").exists()
    assert env.client.get("/api/trash/status").json()["trashed"] == 0
    assert "y.jpg" not in items_by_name(env.client)


def test_apply_skips_missing_source(real_library):
    env = real_library(SPECS)
    _mark(env, "z.jpg", "del")
    (env.lib / "z.jpg").unlink()           # vanished before apply
    r = env.client.post("/api/apply").json()
    assert r["moved"] == 0 and r["skipped"] == 1


def test_apply_noop_when_nothing_marked(real_library):
    env = real_library(SPECS)
    r = env.client.post("/api/apply").json()
    assert r["moved"] == 0 and r["skipped"] == 0
