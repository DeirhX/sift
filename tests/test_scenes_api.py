"""Coverage for /api/scenes: scene listing, nested dup_group items, filter
matching, pagination, meta count, and the storage migration that adds the
scene_group / capture_time columns."""
import sqlite3

from sift.web import photodb
from conftest import default_report


def _scenes(client, query=""):
    return client.get(f"/api/scenes{query}").json()


# ── basic shape + nesting ─────────────────────────────────────────────────────

def test_scenes_listed_with_members(env):
    data = _scenes(env.client)
    assert data["total"] == 1
    assert len(data["scenes"]) == 1
    sc = data["scenes"][0]
    assert sc["scene_group"] == 0
    assert sc["count"] == 3                      # a, b, c
    # members best-first by combined: a (.80) > b (.60) > c (.50)
    assert [it["filename"] for it in sc["items"]] == ["a.jpg", "b.jpg", "c.jpg"]


def test_scene_carries_dup_group_for_nesting(env):
    sc = _scenes(env.client)["scenes"][0]
    by_name = {it["filename"]: it for it in sc["items"]}
    # a + b are the near-dup set (dup_group 0); c is loose (None).
    assert by_name["a.jpg"]["dup_group"] == 0
    assert by_name["b.jpg"]["dup_group"] == 0
    assert by_name["c.jpg"]["dup_group"] is None
    # the server reports one near-dup set in this scene
    assert sc["dup_sets"] == 1


def test_scene_time_range(env):
    sc = _scenes(env.client)["scenes"][0]
    assert sc["time_start"] == 1000.0
    assert sc["time_end"] == 1020.0


# ── filtering semantics (mirror /api/groups) ──────────────────────────────────

def test_scene_included_when_one_member_matches_but_all_returned(env):
    # score_min only a.jpg (.80) passes, but the whole scene stays reviewable.
    sc = _scenes(env.client, "?score_min=0.65")["scenes"][0]
    assert sc["count"] == 3
    assert sc["match_count"] == 1
    matched = {it["filename"]: it["matches"] for it in sc["items"]}
    assert matched == {"a.jpg": True, "b.jpg": False, "c.jpg": False}


def test_scene_dropped_when_no_member_matches(env):
    # Nothing scores above 0.99 -> no scene qualifies.
    data = _scenes(env.client, "?score_min=0.99")
    assert data["total"] == 0
    assert data["scenes"] == []


# ── pagination ────────────────────────────────────────────────────────────────

def test_scenes_pagination(make_env):
    rep = default_report()
    # Add a second, later scene (well past the big gap) with its own dup set.
    rep["images"] += [
        {"path": "/fake/d.jpg", "filename": "d.jpg", "combined": 0.7,
         "sharpness": 0.7, "para_aesthetic": 0.7, "dup_group": 1,
         "scene_group": 1, "capture_time": 99000.0, "imgw": 400, "imgh": 300, "faces": []},
        {"path": "/fake/e.jpg", "filename": "e.jpg", "combined": 0.6,
         "sharpness": 0.6, "para_aesthetic": 0.6, "dup_group": 1,
         "scene_group": 1, "capture_time": 99010.0, "imgw": 400, "imgh": 300, "faces": []},
    ]
    rep["scene_groups"] = 2
    env = make_env(rep)
    first = _scenes(env.client, "?limit=1&offset=0")
    second = _scenes(env.client, "?limit=1&offset=1")
    assert first["total"] == 2 and second["total"] == 2
    # default order is by time: scene 0 (t=1000) before scene 1 (t=99000)
    assert first["scenes"][0]["scene_group"] == 0
    assert second["scenes"][0]["scene_group"] == 1


# ── meta count ────────────────────────────────────────────────────────────────

def test_meta_reports_scene_groups(env):
    meta = env.client.get("/api/meta").json()
    assert meta["counts"]["scene_groups"] == 1


# ── storage migration ─────────────────────────────────────────────────────────

def test_migration_adds_scene_columns(tmp_path):
    """An old DB lacking scene_group/capture_time gains them via ensure_schema.

    Builds a full base schema, then drops the scene columns to emulate a DB
    created before the feature, so ensure_schema's other steps (faces ALTER,
    anchor backfill) still have their tables to work on."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    photodb.create_base_schema(conn)
    conn.execute("DROP INDEX IF EXISTS idx_images_scene")
    conn.execute("ALTER TABLE images DROP COLUMN scene_group")
    conn.execute("ALTER TABLE images DROP COLUMN capture_time")
    conn.commit()
    assert "scene_group" not in photodb._table_columns(conn, "images")
    assert "capture_time" not in photodb._table_columns(conn, "images")

    photodb.ensure_schema(conn)
    cols = photodb._table_columns(conn, "images")
    assert "scene_group" in cols
    assert "capture_time" in cols
    conn.close()
