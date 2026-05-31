"""Coverage for keep/del decisions, the JSON export, and bulk auto-cull."""
from conftest import items_by_name


def test_set_keep(env):
    h = items_by_name(env.client)["a.jpg"]["hash"]
    assert env.client.post("/api/decisions", json={"hash": h, "decision": "keep"}).json() == {"ok": True}
    assert items_by_name(env.client)["a.jpg"]["decision"] == "keep"


def test_set_del(env):
    h = items_by_name(env.client)["b.jpg"]["hash"]
    env.client.post("/api/decisions", json={"hash": h, "decision": "del"})
    assert items_by_name(env.client)["b.jpg"]["decision"] == "del"


def test_clear_decision(env):
    h = items_by_name(env.client)["a.jpg"]["hash"]
    env.client.post("/api/decisions", json={"hash": h, "decision": "keep"})
    env.client.post("/api/decisions", json={"hash": h, "decision": None})
    assert items_by_name(env.client)["a.jpg"]["decision"] is None


def test_decision_requires_hash(env):
    assert env.client.post("/api/decisions", json={"decision": "keep"}).status_code == 400


def test_decision_path_fallback(env):
    # No hash supplied: server resolves the content hash from the path.
    r = env.client.post("/api/decisions", json={"path": "/fake/a.jpg", "decision": "keep"})
    assert r.json() == {"ok": True}
    assert items_by_name(env.client)["a.jpg"]["decision"] == "keep"


# ── export ───────────────────────────────────────────────────────────────────

def test_export_buckets(env):
    by = items_by_name(env.client)
    env.client.post("/api/decisions", json={"hash": by["a.jpg"]["hash"], "decision": "keep"})
    env.client.post("/api/decisions", json={"hash": by["b.jpg"]["hash"], "decision": "del"})
    out = env.client.get("/api/export").json()
    assert {e["filename"] for e in out["kept"]} == {"a.jpg"}
    assert {e["filename"] for e in out["deleted"]} == {"b.jpg"}
    assert {e["filename"] for e in out["unmarked"]} == {"c.jpg"}
    # entries carry path/filename/combined
    assert set(out["kept"][0]) >= {"path", "filename", "combined"}


def test_export_all_unmarked_initially(env):
    out = env.client.get("/api/export").json()
    assert out["kept"] == [] and out["deleted"] == []
    assert {e["filename"] for e in out["unmarked"]} == {"a.jpg", "b.jpg", "c.jpg"}


# ── autocull ─────────────────────────────────────────────────────────────────

def test_autocull_keeps_best_deletes_rest(env):
    r = env.client.post("/api/groups/autocull").json()
    # group 0 = {a(.80), b(.60)} -> keep a, del b. c has no group, untouched.
    assert r == {"groups": 1, "kept": 1, "deleted": 1}
    by = items_by_name(env.client)
    assert by["a.jpg"]["decision"] == "keep"
    assert by["b.jpg"]["decision"] == "del"
    assert by["c.jpg"]["decision"] is None


def test_autocull_overwrites_existing_marks(env):
    by = items_by_name(env.client)
    # Pre-mark the best photo for deletion; autocull should flip it back to keep.
    env.client.post("/api/decisions", json={"hash": by["a.jpg"]["hash"], "decision": "del"})
    env.client.post("/api/groups/autocull")
    assert items_by_name(env.client)["a.jpg"]["decision"] == "keep"
