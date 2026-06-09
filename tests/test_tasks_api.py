"""Coverage for the generic web task runner."""
import sqlite3
import time
from pathlib import Path

from conftest import items_by_name


def _wait_done(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}").json()
        if last["state"] != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {last}")


def _mark(env, filename, decision):
    h = items_by_name(env.client)[filename]["hash"]
    env.client.post("/api/decisions", json={"hash": h, "decision": decision})


def test_task_list_empty(env):
    r = env.client.get("/api/tasks").json()
    assert r == {"tasks": [], "current": None}


def test_apply_task_moves_files(real_library):
    env = real_library([("x.jpg", 0.80, None, "mx"), ("y.jpg", 0.50, None, "my")])
    _mark(env, "y.jpg", "del")

    start = env.client.post("/api/tasks", json={"type": "apply_decisions", "params": {}}).json()
    assert start["type"] == "apply_decisions"
    done = _wait_done(env.client, start["id"])

    assert done["state"] == "done"
    assert done["result"]["moved"] == 1
    assert (env.lib / "_trash" / "y.jpg").exists()
    assert not (env.lib / "y.jpg").exists()
    trash_items = items_by_name(env.client, "?limit=50&trash=trashed")
    assert Path(trash_items["y.jpg"]["path"]) == env.lib / "_trash" / "y.jpg"


def test_task_stream_replays_completed_task(real_library):
    env = real_library([("x.jpg", 0.80, None, "mx"), ("y.jpg", 0.50, None, "my")])
    _mark(env, "y.jpg", "del")
    start = env.client.post("/api/tasks", json={"type": "apply_decisions", "params": {}}).json()
    _wait_done(env.client, start["id"])

    body = env.client.get(f"/api/tasks/{start['id']}/stream").text
    assert "event: snapshot" in body
    assert "event: progress" in body
    assert "event: end" in body


def test_trash_task_can_move_one_exact_duplicate_copy(real_library):
    env = real_library([("copy-a.jpg", 0.80, None, "same"),
                        ("copy-b.jpg", 0.70, None, "same")])
    items = items_by_name(env.client)
    assert items["copy-a.jpg"]["hash"] == items["copy-b.jpg"]["hash"]

    start = env.client.post("/api/tasks", json={
        "type": "trash_decisions",
        "params": {"image_ids": [items["copy-b.jpg"]["id"]]},
    }).json()
    done = _wait_done(env.client, start["id"])

    assert done["state"] == "done"
    assert done["result"]["moved"] == 1
    assert (env.lib / "copy-a.jpg").exists()
    assert not (env.lib / "copy-b.jpg").exists()
    trash_items = items_by_name(env.client, "?limit=50&trash=trashed")
    assert list(trash_items) == ["copy-b.jpg"]


def test_restore_task_marks_missing_trash_file_missing(real_library):
    env = real_library([("x.jpg", 0.80, None, "mx"), ("y.jpg", 0.50, None, "my")])
    _mark(env, "y.jpg", "del")
    start = env.client.post("/api/tasks", json={"type": "trash_decisions", "params": {}}).json()
    _wait_done(env.client, start["id"])
    (env.lib / "_trash" / "y.jpg").unlink()

    restore = env.client.post("/api/tasks", json={"type": "restore_trash", "params": {}}).json()
    done = _wait_done(env.client, restore["id"])

    assert done["state"] == "done"
    assert done["result"] == {"restored": 0, "skipped": 1}
    with sqlite3.connect(env.db) as conn:
        state = conn.execute("SELECT state FROM trash_moves").fetchone()[0]
    assert state == "missing"
    assert "y.jpg" not in items_by_name(env.client)


def test_running_task_conflicts(env):
    import sqlite3

    with sqlite3.connect(env.db) as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, state, started) VALUES ('busy', 'apply_decisions', 'running', 1)"
        )
    r = env.client.post("/api/tasks", json={"type": "autocull_duplicates", "params": {}})
    assert r.status_code == 409


def test_unknown_task_type_rejected(env):
    r = env.client.post("/api/tasks", json={"type": "summon_goblin", "params": {}})
    assert r.status_code == 400

