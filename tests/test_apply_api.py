"""Coverage for the reversible apply flow: moving 'del' files into _rejected/,
status accounting, and undo. Uses real files on disk."""
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
    assert status["rejected_dir"].endswith("_rejected")


def test_apply_moves_del_files(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    r = env.client.post("/api/apply").json()
    assert r["moved"] == 1 and r["skipped"] == 0

    rejected = env.lib / "_rejected"
    assert (rejected / "y.jpg").exists()
    assert not (env.lib / "y.jpg").exists()
    # The DB path follows the file; the decision is unchanged (hash-keyed).
    moved = items_by_name(env.client)["y.jpg"]
    assert Path(moved["path"]) == rejected / "y.jpg"
    assert moved["decision"] == "del"
    # Status reflects the applied move.
    status = env.client.get("/api/apply/status").json()
    assert status["pending"] == 0 and status["applied"] == 1


def test_apply_undo_restores(real_library):
    env = real_library(SPECS)
    _mark(env, "y.jpg", "del")
    env.client.post("/api/apply")
    r = env.client.post("/api/apply/undo").json()
    assert r["restored"] == 1
    assert (env.lib / "y.jpg").exists()
    assert not (env.lib / "_rejected" / "y.jpg").exists()
    assert items_by_name(env.client)["y.jpg"]["path"] == str(env.lib / "y.jpg")
    # Log cleared, so nothing is left to undo.
    assert env.client.get("/api/apply/status").json()["applied"] == 0


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
