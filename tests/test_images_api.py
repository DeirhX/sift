"""Coverage for /api/images faceted querying: range filters, people/tags,
decision filter, dup_mode, sort/direction, pagination, caption search, and the
shape of the returned items."""
import pytest

from conftest import default_report, items_by_name


def names(client, query):
    return set(items_by_name(client, query))


# ── range filters ────────────────────────────────────────────────────────────

def test_score_min(env):
    # combined: a=.80, b=.60, c=.50
    assert names(env.client, "?limit=50&score_min=0.65") == {"a.jpg"}


def test_score_max(env):
    assert names(env.client, "?limit=50&score_max=0.55") == {"c.jpg"}


def test_sharp_min(env):
    # sharpness: a=.70, b=.50, c=.40
    assert names(env.client, "?limit=50&sharp_min=0.65") == {"a.jpg"}


def test_aesthetic_max_excludes_high(env):
    # para_aesthetic: a=.75, b=.55, c=.45
    assert names(env.client, "?limit=50&aes_max=0.5") == {"c.jpg"}


def test_aesthetic_filter_keeps_null_rows(make_env):
    # Rows without an aesthetic score must not be filtered out by an aes range.
    rep = default_report()
    rep["images"][0]["para_aesthetic"] = None       # a.jpg has no aesthetic
    env = make_env(rep)
    # aes_max=0.1 would drop b/c (have scores) but keep a (NULL passes).
    assert "a.jpg" in names(env.client, "?limit=50&aes_max=0.1")


# ── people + tags ────────────────────────────────────────────────────────────

def test_people_filter_cluster0(env):
    # cluster 0 faces are on a.jpg and c.jpg
    assert names(env.client, "?limit=50&people=0") == {"a.jpg", "c.jpg"}


def test_people_filter_cluster1(env):
    assert names(env.client, "?limit=50&people=1") == {"b.jpg", "c.jpg"}


def test_tags_filter(make_env):
    rep = default_report()
    rep["images"][0]["tags"] = ["sunset", "beach"]
    rep["images"][1]["tags"] = ["beach"]
    env = make_env(rep)
    assert names(env.client, "?limit=50&tags=sunset") == {"a.jpg"}
    assert names(env.client, "?limit=50&tags=beach") == {"a.jpg", "b.jpg"}


# ── decision filter ──────────────────────────────────────────────────────────

def test_decision_filter(env):
    by = items_by_name(env.client)
    env.client.post("/api/decisions", json={"hash": by["a.jpg"]["hash"], "decision": "keep"})
    env.client.post("/api/decisions", json={"hash": by["b.jpg"]["hash"], "decision": "del"})
    assert names(env.client, "?limit=50&decision=keep") == {"a.jpg"}
    assert names(env.client, "?limit=50&decision=del") == {"b.jpg"}
    assert names(env.client, "?limit=50&decision=unmarked") == {"c.jpg"}
    # "Hide deletions" keeps everything except the del-marked photo.
    assert names(env.client, "?limit=50&decision=notdel") == {"a.jpg", "c.jpg"}


# ── dup_mode ─────────────────────────────────────────────────────────────────

def test_dup_mode_groups_only(env):
    assert names(env.client, "?limit=50&dup_mode=groups-only") == {"a.jpg", "b.jpg"}


def test_dup_mode_no_groups(env):
    assert names(env.client, "?limit=50&dup_mode=no-groups") == {"c.jpg"}


def test_dup_mode_hide_dups_keeps_best_representative(env):
    # group 0's best is a.jpg (combined .80 > b's .60); c.jpg has no group.
    assert names(env.client, "?limit=50&dup_mode=hide-dups") == {"a.jpg", "c.jpg"}


# ── sort + direction ─────────────────────────────────────────────────────────

def _order(client, query):
    return [it["filename"] for it in client.get(f"/api/images{query}").json()["items"]]


def test_sort_combined_desc(env):
    assert _order(env.client, "?limit=50&sort=combined&dir=desc") == ["a.jpg", "b.jpg", "c.jpg"]


def test_sort_combined_asc(env):
    assert _order(env.client, "?limit=50&sort=combined&dir=asc") == ["c.jpg", "b.jpg", "a.jpg"]


def test_sort_filename(env):
    assert _order(env.client, "?limit=50&sort=filename&dir=asc") == ["a.jpg", "b.jpg", "c.jpg"]


# ── pagination ───────────────────────────────────────────────────────────────

def test_pagination(env):
    page1 = env.client.get("/api/images?sort=combined&dir=desc&limit=1&offset=0").json()
    page2 = env.client.get("/api/images?sort=combined&dir=desc&limit=1&offset=1").json()
    assert page1["total"] == 3 and len(page1["items"]) == 1
    assert page1["items"][0]["filename"] == "a.jpg"
    assert page2["items"][0]["filename"] == "b.jpg"


def test_limit_capped(env):
    # limit is declared le=300; over-limit should be rejected by validation.
    r = env.client.get("/api/images?limit=9999")
    assert r.status_code == 422


# ── caption search ───────────────────────────────────────────────────────────

def test_caption_search(make_env):
    rep = default_report()
    rep["images"][0]["caption"] = "a dog running on the beach"
    rep["images"][1]["caption"] = "a cat on a sofa"
    env = make_env(rep)
    # Matches whether FTS5 is present (MATCH) or not (LIKE fallback).
    assert names(env.client, "?limit=50&q=dog") == {"a.jpg"}


# ── item shape ───────────────────────────────────────────────────────────────

def test_item_shape(env):
    a = items_by_name(env.client)["a.jpg"]
    for key in ("id", "filename", "path", "hash", "combined", "sharpness",
                "para_aesthetic", "portrait", "decision", "faces", "tags"):
        assert key in a
    face = a["faces"][0]
    assert set(face) >= {"id", "bbox", "prob", "cluster_id", "sharp", "expr"}
    assert len(face["bbox"]) == 4
