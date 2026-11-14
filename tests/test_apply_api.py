"""Coverage for the reversible Trash flow: moving 'del' files into _trash/,
status accounting, restore, and emptying. Uses real files on disk."""
import sqlite3
from pathlib import Path

from conftest import items_by_name, tiny_jpeg


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


def test_trash_ledger_survives_reindex_renumbering(real_library, rebuild):
    """`sift index` renumbers image ids. A trashed file drops out of the next
    report (analyze skips _trash/), so its old id is handed to another photo —
    which must not inherit the trash state, nor be repointed by a later restore."""
    import json
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    assert env.client.post("/api/apply").json()["moved"] == 1

    report = json.loads(env.report.read_text(encoding="utf-8"))
    report["images"] = [im for im in report["images"] if im["filename"] != "y.jpg"]
    env.report.write_text(json.dumps(report), encoding="utf-8")
    rebuild(env)

    active = items_by_name(env.client)
    assert set(active) == {"x.jpg", "z.jpg"}            # z took y's old id
    assert all(it["trash_state"] is None for it in active.values())
    assert items_by_name(env.client, "?limit=50&trash=trashed") == {}

    assert env.client.post("/api/apply/undo").json()["restored"] == 1
    assert (env.lib / "y.jpg").exists()
    paths = {fn: Path(it["path"]) for fn, it in items_by_name(env.client).items()}
    assert paths == {"x.jpg": env.lib / "x.jpg", "z.jpg": env.lib / "z.jpg"}


def test_reindex_same_report_keeps_trashed_photo_in_trash(real_library, rebuild):
    env = real_library(SPECS)
    client = env.client
    _mark(env, "y.jpg", "del")
    assert client.post("/api/apply").json()["moved"] == 1
    rebuild(env)

    assert set(items_by_name(client)) == {"x.jpg", "z.jpg"}
    trashed = items_by_name(client, "?limit=50&trash=trashed")["y.jpg"]
    assert Path(trashed["path"]).is_file()
    assert client.post("/api/apply/undo").json()["restored"] == 1
    assert Path(items_by_name(client)["y.jpg"]["path"]) == env.lib / "y.jpg"


def test_reindex_does_not_hide_replacement_at_original_path(real_library, rebuild):
    env = real_library(SPECS)
    client = env.client
    _mark(env, "y.jpg", "del")
    assert client.post("/api/apply").json()["moved"] == 1
    tiny_jpeg(env.lib / "y.jpg", color=(253, 100, 47))
    rebuild(env)

    replacement = items_by_name(client)["y.jpg"]
    assert Path(replacement["path"]) == env.lib / "y.jpg"
    assert replacement["decision"] is None


def test_reindex_same_report_handles_reused_trash_filename(tmp_path, make_env, rebuild):
    from sift.web.library_ops import empty_trash

    originals = [tmp_path / folder / "shared.jpg" for folder in ("first", "second")]
    for i, path in enumerate(originals):
        path.parent.mkdir()
        tiny_jpeg(path, color=(30 + 100 * i, 100, 47))
    env = make_env({"folder": str(tmp_path), "images": [
        {"path": str(path), "filename": path.name, "combined": 0.5, "faces": []}
        for path in originals
    ]})
    client = env.client
    photos = {item["path"]: item for item in client.get("/api/images").json()["items"]}

    for i, path in enumerate(originals):
        client.post("/api/decisions", json={"hash": photos[str(path)]["hash"], "decision": "del"})
        assert client.post("/api/apply").json()["moved"] == 1
        if i == 0:
            with sqlite3.connect(env.db) as conn:
                conn.row_factory = sqlite3.Row
                assert empty_trash(conn)["deleted"] == 1

    rebuild(env)
    trashed = client.get("/api/images", params={"trash": "trashed"}).json()["items"]
    assert [item["hash"] for item in trashed] == [photos[str(originals[1])]["hash"]]
    assert client.post("/api/apply/undo").json()["restored"] == 1
    assert originals[1].exists() and not originals[0].exists()


def test_trash_folder_check_matches_whole_path_components(tmp_path):
    from sift.web.library_ops import _inside

    trash = tmp_path / "_trash"
    assert _inside(trash / "a.jpg", trash)
    assert _inside(trash, trash)
    # Siblings that merely share the prefix are ordinary library folders.
    assert not _inside(tmp_path / "_trash_old" / "a.jpg", trash)
    assert not _inside(tmp_path / "_trashed.jpg", trash)
    assert not _inside(tmp_path / "a.jpg", trash)


def test_photos_in_a_trash_lookalike_folder_can_still_be_trashed(real_library):
    env = real_library(SPECS)
    lookalike = env.lib / "_trash_old"
    lookalike.mkdir()
    (env.lib / "z.jpg").rename(lookalike / "z.jpg")
    with sqlite3.connect(env.db) as conn:
        conn.execute("UPDATE images SET path=? WHERE filename='z.jpg'", (str(lookalike / "z.jpg"),))
    _mark(env, "z.jpg", "del")

    assert env.client.get("/api/apply/status").json()["pending"] == 1
    r = env.client.post("/api/apply").json()
    assert r["moved"] == 1 and r["skipped"] == 0
    assert (env.lib / "_trash" / "z.jpg").exists()


def test_empty_trash_never_deletes_outside_a_trash_folder(real_library):
    """A ledger row pointing at a real library file must not get it deleted."""
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    env.client.post("/api/apply")
    victim = env.lib / "x.jpg"
    with sqlite3.connect(env.db) as conn:
        conn.execute("UPDATE trash_moves SET trash_path=?", (str(victim),))

    start = env.client.post("/api/tasks", json={"type": "empty_trash", "params": {}}).json()
    import time
    for _ in range(100):
        done = env.client.get(f"/api/tasks/{start['id']}").json()
        if done["state"] != "running":
            break
        time.sleep(0.05)

    assert done["state"] == "done"
    assert done["result"] == {"deleted": 0, "skipped": 1}
    assert victim.exists()
