"""Coverage for the re-analysis endpoints WITHOUT spawning subprocesses: status
when idle, cancel/conflict guards, and argv building + validation/clamping."""
import sqlite3

import pytest
from fastapi import HTTPException

from sift.web import server, analysis


def _build_steps(env, payload):
    """Call the (now decoupled) argv builder with the env's DB/thumb paths."""
    return analysis.build_analyze_steps(
        payload, db_path=env.db, thumb_dir=env.thumbs, db_factory=server.db)


@pytest.fixture(autouse=True)
def reset_job():
    """No analysis job before/after each test, so we never touch a real run."""
    server.CURRENT_ANALYZE_TASK_ID = None
    server.tasks.MANAGER._active.clear()
    yield
    server.CURRENT_ANALYZE_TASK_ID = None
    server.tasks.MANAGER._active.clear()


def test_status_idle(env):
    assert env.client.get("/api/analyze/status").json() == {"state": "idle", "commands": []}


def test_cancel_with_no_job_conflicts(env):
    assert env.client.post("/api/analyze/cancel").status_code == 409


def test_start_conflicts_when_already_running(env):
    with sqlite3.connect(env.db) as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, state, started) VALUES ('busy', 'analyze_library', 'running', 1)"
        )
    r = env.client.post("/api/analyze", json={"folder": str(env.db.parent)})
    assert r.status_code == 409


def test_start_rejects_missing_folder(env):
    r = env.client.post("/api/analyze", json={"folder": "/no/such/folder/xyz"})
    assert r.status_code == 400


# ── argv builder (pure, no process launch) ───────────────────────────────────

def test_build_steps_bad_folder_raises(env):
    with pytest.raises(HTTPException) as ei:
        _build_steps(env, {"folder": "/no/such/folder/xyz"})
    assert ei.value.status_code == 400


def test_build_steps_bad_backend_raises(env, tmp_path):
    with pytest.raises(HTTPException) as ei:
        _build_steps(env, {"folder": str(tmp_path), "backend": "bogus"})
    assert ei.value.status_code == 400


def test_build_steps_emits_two_steps_with_flags(env, tmp_path):
    steps = _build_steps(env, {
        "folder": str(tmp_path), "backend": "para",
        "recurse": True, "faces": True, "face_expr": True,
    })
    assert [name for name, _ in steps] == ["analyze", "index"]
    audit = steps[0][1]
    assert "--backend" in audit and "para" in audit
    assert "--recurse" in audit and "--faces" in audit and "--face-expr" in audit


def test_build_steps_clamps_numeric_knobs(env, tmp_path):
    audit = dict(_build_steps(env, {
        "folder": str(tmp_path), "backend": "para",
        "dup_threshold": 999, "face_min_rel": 2.0,
    }))["analyze"]
    # dup_threshold clamps to [0,64]; face_min_rel to [0,1].
    assert audit[audit.index("--dup-threshold") + 1] == "64"
    assert audit[audit.index("--face-min-rel") + 1] == "1.0"


def test_build_steps_rejects_bad_numeric(env, tmp_path):
    with pytest.raises(HTTPException) as ei:
        _build_steps(env, {
            "folder": str(tmp_path), "backend": "para", "dup_threshold": "abc"})
    assert ei.value.status_code == 400


def test_build_steps_passes_multiple_folders(env, tmp_path):
    """All onboarded folders land on the analyze argv before --out, so the CLI
    scans them as one union."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    audit = dict(_build_steps(env, {
        "folders": [str(a), str(b)], "backend": "para"}))["analyze"]
    i = audit.index("analyze")
    out = audit.index("--out")
    assert audit[i + 1:out] == [str(a), str(b)]


def test_build_steps_dedups_folders(env, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    audit = dict(_build_steps(env, {
        "folders": [str(a), str(a)], "backend": "para"}))["analyze"]
    assert audit.count(str(a)) == 1


def test_build_steps_missing_folder_in_set_raises(env, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    with pytest.raises(HTTPException) as ei:
        _build_steps(env, {"folders": [str(a), str(tmp_path / "ghost")]})
    assert ei.value.status_code == 400
