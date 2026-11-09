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
    # entries carry the stable hash key plus the human-readable fields
    assert set(out["kept"][0]) >= {"hash", "path", "filename", "combined"}
    assert out["kept"][0]["hash"] == by["a.jpg"]["hash"]


def test_export_all_unmarked_initially(env):
    out = env.client.get("/api/export").json()
    assert out["kept"] == [] and out["deleted"] == []
    assert {e["filename"] for e in out["unmarked"]} == {"a.jpg", "b.jpg", "c.jpg"}


# ── import (re-apply exported verdicts) ──────────────────────────────────────

def test_import_roundtrip_by_hash(env):
    by = items_by_name(env.client)
    env.client.post("/api/decisions", json={"hash": by["a.jpg"]["hash"], "decision": "keep"})
    env.client.post("/api/decisions", json={"hash": by["b.jpg"]["hash"], "decision": "del"})
    exported = env.client.get("/api/export").json()
    # wipe both marks, then restore them from the exported file
    env.client.post("/api/decisions", json={"hash": by["a.jpg"]["hash"], "decision": None})
    env.client.post("/api/decisions", json={"hash": by["b.jpg"]["hash"], "decision": None})
    assert items_by_name(env.client)["a.jpg"]["decision"] is None

    r = env.client.post("/api/import", json=exported).json()
    assert r == {"ok": True, "applied": 2, "unmatched": 0}
    after = items_by_name(env.client)
    assert after["a.jpg"]["decision"] == "keep"
    assert after["b.jpg"]["decision"] == "del"
    assert after["c.jpg"]["decision"] is None       # unmarked bucket is ignored


def test_import_matches_by_path_without_hash(env):
    # An entry lacking a hash still resolves through its path.
    payload = {"kept": [{"path": "/fake/a.jpg"}], "deleted": []}
    r = env.client.post("/api/import", json=payload).json()
    assert r["applied"] == 1 and r["unmatched"] == 0
    assert items_by_name(env.client)["a.jpg"]["decision"] == "keep"


def test_import_is_additive(env):
    # Marks not present in the file are left untouched.
    by = items_by_name(env.client)
    env.client.post("/api/decisions", json={"hash": by["c.jpg"]["hash"], "decision": "keep"})
    env.client.post("/api/import",
                    json={"kept": [{"hash": by["a.jpg"]["hash"]}], "deleted": []})
    after = items_by_name(env.client)
    assert after["a.jpg"]["decision"] == "keep"
    assert after["c.jpg"]["decision"] == "keep"     # untouched


def test_import_counts_unmatched(env):
    payload = {"kept": [{"path": "/nope/ghost.jpg"}, {}], "deleted": []}
    r = env.client.post("/api/import", json=payload).json()
    assert r["applied"] == 0 and r["unmatched"] == 2


def test_import_records_unknown_hash(env):
    # A hash that isn't in the catalog is still recorded; it binds if that photo
    # is indexed later (same contract as decisions preserved across a rebuild).
    r = env.client.post("/api/import",
                        json={"kept": [{"hash": "deadbeefcafe"}], "deleted": []}).json()
    assert r["applied"] == 1 and r["unmatched"] == 0


def test_import_rejects_bad_format(env):
    assert env.client.post("/api/import", json={"foo": 1}).status_code == 400


def test_copy_marks_survive_trash_restore_and_export(real_library):
    env = real_library([("a.jpg", 0.8, 0, "same"), ("b.jpg", 0.7, 0, "same")])
    client = env.client
    photo = items_by_name(client)["b.jpg"]
    identity = {key: photo[key] for key in ("id", "hash", "path")}
    client.post("/api/decisions", json={"hash": photo["hash"], "decision": "keep"})
    assert client.post("/api/decisions", json={**identity, "decision": "del"}).is_success
    assert client.post("/api/apply").json()["moved"] == 1
    assert (env.lib / "a.jpg").exists()
    assert client.post("/api/apply/undo").json()["restored"] == 1
    assert items_by_name(client)["b.jpg"]["decision"] == "del"

    client.post("/api/decisions", json={**identity, "decision": None})
    saved = client.get("/api/export").json()
    assert saved["unmarked"][0]["copy_specific"]
    client.post("/api/decisions", json={**identity, "decision": "keep"})
    assert client.post("/api/import", json=saved).is_success
    photos = items_by_name(client)
    assert photos["a.jpg"]["decision"] == "keep"
    assert photos["b.jpg"]["decision"] is None


def test_stale_batch_rolls_back_earlier_updates(env):
    client = env.client
    photos = items_by_name(client)
    updates = [
        {**{key: photos[name][key] for key in ("id", "hash", "path")},
         "expected_decision": previous, "decision": "del"}
        for name, previous in [("a.jpg", None), ("b.jpg", "keep")]
    ]
    assert client.post("/api/decisions", json={"updates": updates}).status_code == 409
    assert items_by_name(client)["a.jpg"]["decision"] is None


# ── autocull ─────────────────────────────────────────────────────────────────

def test_autocull_keeps_best_deletes_rest(env):
    r = env.client.post("/api/groups/autocull").json()
    # group 0 = {a(.80), b(.60)} -> keep a, del b. c has no group, untouched.
    assert r == {"groups": 1, "kept": 1, "deleted": 1, "review": 0}
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


def test_autocull_exact_copies_keep_one_active_file(real_library):
    env = real_library([("a.jpg", 0.8, 0, "same"), ("b.jpg", 0.7, 0, "same"),
                        ("c.jpg", 0.6, 0, "similar")])
    client = env.client
    assert client.post("/api/groups/autocull").json() == {
        "groups": 1, "kept": 1, "deleted": 1, "review": 1,
    }
    by_name = items_by_name(client)
    assert [by_name[f"{c}.jpg"]["decision"] for c in "abc"] == ["keep", "del", None]
    assert client.post("/api/apply").json()["moved"] == 1
    assert (env.lib / "a.jpg").exists()
    assert (env.lib / "c.jpg").exists()
    assert client.post("/api/groups/autocull").json()["deleted"] == 0


def test_autocull_leaves_tight_near_ties_for_review(env):
    import sqlite3

    with sqlite3.connect(env.db) as conn:
        conn.execute("UPDATE images SET combined=0.8, portrait=NULL WHERE dup_group=0")
    result = env.client.post("/api/groups/autocull").json()
    assert result["review"] == 1 and result["deleted"] == 0
    assert all(it["decision"] is None for it in items_by_name(env.client).values())


def test_autocull_ranks_only_active_members(real_library):
    import sqlite3

    env = real_library([("a.jpg", 0.9, 0, "a"), ("b.jpg", 0.8, 0, "b"),
                        ("c.jpg", 0.7, 0, "c")])
    client = env.client
    first = items_by_name(client)["a.jpg"]
    client.post("/api/decisions", json={"hash": first["hash"], "decision": "del"})
    assert client.post("/api/apply").json()["moved"] == 1
    with sqlite3.connect(env.db) as conn:
        conn.execute("UPDATE images SET dup_cull_safe=1")

    assert client.post("/api/groups/autocull").json()["kept"] == 1
    by_name = items_by_name(client)
    assert by_name["b.jpg"]["decision"] == "keep"
    assert by_name["c.jpg"]["decision"] == "del"
    assert client.post("/api/apply").json()["moved"] == 1
    assert (env.lib / "b.jpg").exists()


def test_autocull_preview_and_stale_apply_are_read_only(env):
    client = env.client
    preview = client.get("/api/groups/autocull/preview").json()
    assert {change["filename"]: change["decision"] for change in preview["changes"]} == {
        "a.jpg": "keep", "b.jpg": "del",
    }
    assert all(it["decision"] is None for it in items_by_name(client).values())

    changes = [{**change, "expected_decision": change["previous"]} for change in preview["changes"]]
    photo = items_by_name(client)["b.jpg"]
    client.post("/api/decisions", json={"id": photo["id"], "hash": photo["hash"], "decision": "keep"})
    assert client.post("/api/decisions", json={"updates": changes}).status_code == 409
    current = items_by_name(client)
    assert current["a.jpg"]["decision"] is None
    assert current["b.jpg"]["decision"] == "keep"
