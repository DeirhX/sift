"""Unit tests for the shared photodb module: the domain rules and the single
schema-migration authority that build_db and server both depend on."""
import sqlite3

import pytest

from sift.web import photodb


# ── bbox_key ─────────────────────────────────────────────────────────────────

def test_bbox_key_rounds_to_one_decimal():
    assert photodb.bbox_key(10.04, 20.05, 30.16, 40.0) == "10.0,20.1,30.2,40.0"


def test_bbox_key_accepts_strings_and_ints():
    # Coordinates may arrive as ints (report) or floats (DB) — same key either way.
    assert photodb.bbox_key(10, 20, 30, 40) == photodb.bbox_key(10.0, 20.0, 30.0, 40.0)


def test_bbox_key_is_the_same_object_build_db_uses():
    from sift.web import build_db
    assert build_db.bbox_key is photodb.bbox_key


# ── portrait_score ───────────────────────────────────────────────────────────

def test_portrait_score_weights_sharp_over_expr():
    assert photodb.portrait_score(0.9, 0.8) == pytest.approx(0.6 * 0.9 + 0.4 * 0.8)


def test_portrait_score_none_sharp_is_none():
    assert photodb.portrait_score(None, 0.8) is None


def test_portrait_score_missing_expr_falls_back_to_sharp():
    assert photodb.portrait_score(0.7, None) == pytest.approx(0.7)


def test_portrait_score_rounds_to_four_places():
    v = photodb.portrait_score(1 / 3, 1 / 7)
    assert v == round(v, 4)


# ── largest_face_aggregate ───────────────────────────────────────────────────

def test_aggregate_empty():
    assert photodb.largest_face_aggregate([]) == (0, None, None, None)


def test_aggregate_single_face():
    n, sharp, expr, portrait = photodb.largest_face_aggregate(
        [(0, 0, 10, 10, 0.5, 0.5)])
    assert n == 1 and sharp == 0.5 and expr == 0.5
    assert portrait == pytest.approx(0.5)


def test_aggregate_picks_largest_by_area():
    faces = [
        (0, 0, 10, 10, 0.2, 0.3),     # area 100, small
        (0, 0, 75, 75, 0.6, 0.5),     # area 5625, largest -> drives the aggregate
    ]
    n, sharp, expr, portrait = photodb.largest_face_aggregate(faces)
    assert n == 2 and sharp == 0.6 and expr == 0.5
    assert portrait == pytest.approx(0.6 * 0.6 + 0.4 * 0.5)


def test_aggregate_consumes_generator():
    gen = ((0, 0, i + 1, i + 1, 0.1 * i, 0.1 * i) for i in range(3))
    n, *_ = photodb.largest_face_aggregate(gen)
    assert n == 3


# ── next_manual_cluster_id ───────────────────────────────────────────────────

def _conn_with_clusters(ids):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE clusters (cluster_id INTEGER PRIMARY KEY, name TEXT)")
    for cid in ids:
        conn.execute("INSERT INTO clusters (cluster_id, name) VALUES (?, NULL)", (cid,))
    return conn


def test_next_manual_id_empty_starts_at_base():
    conn = _conn_with_clusters([])
    assert photodb.next_manual_cluster_id(conn) == photodb.MANUAL_CLUSTER_BASE


def test_next_manual_id_ignores_detector_clusters():
    # Low detector ids (0..N) must not push the manual range up.
    conn = _conn_with_clusters([0, 1, 2, 5])
    assert photodb.next_manual_cluster_id(conn) == photodb.MANUAL_CLUSTER_BASE


def test_next_manual_id_increments_above_existing_manual():
    base = photodb.MANUAL_CLUSTER_BASE
    conn = _conn_with_clusters([0, base, base + 3])
    assert photodb.next_manual_cluster_id(conn) == base + 4


# ── ensure_schema migration ──────────────────────────────────────────────────

def _legacy_db():
    """A pre-incremental DB: images/faces without the late score columns and no
    face_overrides table — the shape an old photos.db would have."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE images (
            id INTEGER PRIMARY KEY, path TEXT, filename TEXT,
            sharpness REAL, combined REAL);
        CREATE TABLE faces (
            id INTEGER PRIMARY KEY, image_id INTEGER,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL, prob REAL, cluster_id INTEGER);
    """)
    return conn


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_ensure_schema_adds_late_image_columns():
    conn = _legacy_db()
    photodb.ensure_schema(conn)
    assert {"content_hash", "mtime", "fsize",
            "face_sharp", "face_expr", "portrait"} <= _cols(conn, "images")


def test_ensure_schema_adds_late_face_columns():
    conn = _legacy_db()
    photodb.ensure_schema(conn)
    assert {"sharp", "expr"} <= _cols(conn, "faces")


def test_ensure_schema_creates_overrides_table_and_indexes():
    conn = _legacy_db()
    photodb.ensure_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "face_overrides" in tables
    assert {"idx_images_hash", "idx_images_portrait"} <= idx


def test_ensure_schema_is_idempotent():
    conn = _legacy_db()
    photodb.ensure_schema(conn)
    photodb.ensure_schema(conn)   # second run must not raise (duplicate column/table)
    assert {"content_hash", "portrait"} <= _cols(conn, "images")


def test_create_base_schema_then_ensure_is_clean():
    conn = sqlite3.connect(":memory:")
    photodb.create_base_schema(conn)
    photodb.ensure_schema(conn)
    cols = _cols(conn, "images")
    assert {"portrait", "content_hash"} <= cols
