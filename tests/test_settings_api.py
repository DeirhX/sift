"""Coverage for the previously-untested endpoints: the /api/reveal security
guardrail, the runtime photo-root settings, filesystem autocomplete, and the
idle analyze SSE stream. The OS file-manager call is stubbed so reveal tests
exercise only the path-validation logic (no Explorer/Finder windows pop up)."""
import os

import pytest

import server


@pytest.fixture()
def clean_roots():
    """Isolate the module-level reveal-root globals (set once at startup in prod,
    mutated by these tests) so they don't leak across the session."""
    saved = (list(server.PHOTO_ROOTS), list(server.PHOTO_ROOT_DIRS))
    server.PHOTO_ROOTS, server.PHOTO_ROOT_DIRS = [], []
    yield
    server.PHOTO_ROOTS, server.PHOTO_ROOT_DIRS = list(saved[0]), list(saved[1])


# ── /api/settings/roots ──────────────────────────────────────────────────────

def test_add_list_delete_root(env, clean_roots, tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    resolved = str(d.resolve())

    out = env.client.post("/api/settings/roots", json={"path": str(d)}).json()
    assert resolved in out["photo_roots"]
    assert env.client.get("/api/settings/roots").json()["photo_roots"] == [resolved]

    # idempotent guard: re-adding the same root conflicts
    assert env.client.post("/api/settings/roots", json={"path": str(d)}).status_code == 409

    env.client.request("DELETE", "/api/settings/roots", json={"path": resolved})
    assert env.client.get("/api/settings/roots").json()["photo_roots"] == []


def test_add_root_rejects_nonexistent(env, clean_roots, tmp_path):
    r = env.client.post("/api/settings/roots", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_add_root_rejects_overlap(env, clean_roots, tmp_path):
    root = tmp_path / "lib"
    (root / "sub").mkdir(parents=True)
    env.client.post("/api/settings/roots", json={"path": str(root)})
    # a directory nested under an existing root is redundant → rejected
    r = env.client.post("/api/settings/roots", json={"path": str(root / "sub")})
    assert r.status_code == 409


def test_add_root_requires_path(env, clean_roots):
    assert env.client.post("/api/settings/roots", json={}).status_code == 400


# ── /api/reveal (guardrail only; OS call stubbed) ────────────────────────────

def test_reveal_without_root_forbidden(env, clean_roots, tmp_path):
    assert env.client.post("/api/reveal", json={"path": str(tmp_path)}).status_code == 403


def test_reveal_requires_path(env, clean_roots, monkeypatch):
    monkeypatch.setattr(server, "_reveal_in_os", lambda target: None)
    server.PHOTO_ROOTS = ["x"]
    assert env.client.post("/api/reveal", json={"path": ""}).status_code == 400


def test_reveal_within_root_ok(env, clean_roots, monkeypatch, tmp_path):
    opened = {}
    monkeypatch.setattr(server, "_reveal_in_os", lambda target: opened.setdefault("t", target))
    root = tmp_path / "lib"
    root.mkdir()
    f = root / "photo.jpg"
    f.write_bytes(b"x")
    env.client.post("/api/settings/roots", json={"path": str(root)})

    assert env.client.post("/api/reveal", json={"path": str(f)}).status_code == 200
    assert str(opened["t"]) == str(f)


def test_reveal_outside_root_forbidden(env, clean_roots, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_reveal_in_os", lambda target: None)
    root = tmp_path / "lib"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    env.client.post("/api/settings/roots", json={"path": str(root)})

    assert env.client.post("/api/reveal", json={"path": str(outside)}).status_code == 403


def test_reveal_missing_path_404(env, clean_roots, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_reveal_in_os", lambda target: None)
    root = tmp_path / "lib"
    root.mkdir()
    env.client.post("/api/settings/roots", json={"path": str(root)})

    r = env.client.post("/api/reveal", json={"path": str(root / "ghost.jpg")})
    assert r.status_code == 404


# ── /api/fs/complete ─────────────────────────────────────────────────────────

def test_fs_complete_lists_child_dirs(env, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "note.txt").write_text("x")          # files are never listed
    out = env.client.get("/api/fs/complete", params={"q": str(tmp_path) + os.sep}).json()
    names = {os.path.basename(p.rstrip("\\/")) for p in out["entries"]}
    assert {"alpha", "beta"} <= names
    assert "note.txt" not in names


def test_fs_complete_prefix_filters(env, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    out = env.client.get("/api/fs/complete", params={"q": str(tmp_path / "al")}).json()
    names = {os.path.basename(p.rstrip("\\/")) for p in out["entries"]}
    assert names == {"alpha"}


def test_fs_complete_empty_query_lists_roots(env):
    out = env.client.get("/api/fs/complete", params={"q": ""}).json()
    assert out["entries"]                              # drives (win) or "/" (posix)


# ── /api/analyze/stream (idle) ───────────────────────────────────────────────

def test_analyze_stream_idle_terminates(env):
    server.CURRENT_JOB = None
    r = env.client.get("/api/analyze/stream")
    assert r.status_code == 200
    assert "idle" in r.text                            # emits a single 'end' event
