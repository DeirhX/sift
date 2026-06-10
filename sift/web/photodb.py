#!/usr/bin/env python3
"""
photodb.py — Single source of truth for the photo-audit SQLite layer.

build_db.py (ingest) and server.py (API) both depend on the *same* schema, the
*same* additive migrations, and the *same* face/portrait domain rules. They used
to keep private copies of all three, with comments begging future editors to keep
them in sync by hand. A mismatch is not cosmetic: if the bbox_key rounding or the
portrait formula drift apart, every manual face edit silently detaches and every
portrait score skews on the next rebuild.

So everything the two scripts must agree on lives here, and nowhere else:
  - the base schema (BASE_SCHEMA / FTS_SCHEMA)
  - the one migration authority (ensure_schema), called from both entry points
  - the face/portrait domain helpers (bbox_key, portrait_score)
  - the manual-cluster id range and allocator
"""

import sqlite3
from collections import Counter

# Manually-created people get ids in a high range so they never collide with
# the detector's cluster ids (0..N) when build_db re-ingests a fresh report.
MANUAL_CLUSTER_BASE = 100_000

# Base schema, created once by build_db on ingest. Every statement is
# CREATE IF NOT EXISTS so re-running it on an existing DB is a no-op.
# Note: late-arriving columns and the face_overrides table are deliberately NOT
# here — they live in ensure_schema() so an older photos.db can be upgraded in
# place without a full rebuild, and so there is exactly one definition of each.
BASE_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS images (
    id              INTEGER PRIMARY KEY,
    path            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    sharpness       REAL,
    combined        REAL,
    raw_laplacian   REAL,
    dup_group       INTEGER,
    para_aesthetic  REAL,
    para_quality    REAL,
    para_composition REAL,
    para_light      REAL,
    para_color      REAL,
    para_dof        REAL,
    para_content    REAL,
    clip_iqa        REAL,
    caption         TEXT,
    imgw            INTEGER,
    imgh            INTEGER,
    thumb           TEXT,
    content_hash    TEXT,
    mtime           REAL,
    fsize           INTEGER,
    n_faces         INTEGER DEFAULT 0,
    face_sharp      REAL,   -- largest face's normalised sharpness
    face_expr       REAL,   -- largest face's expression quality (if scored)
    portrait        REAL,   -- combined portrait quality of the largest face
    scene_group     INTEGER,-- rough scene id (near-dup dup_group nests inside)
    capture_time    REAL,   -- EXIF capture time (epoch), mtime fallback
    dup_central     REAL    -- mean CLIP cosine to dup-group peers (medoid hero)
);

CREATE TABLE IF NOT EXISTS faces (
    id          INTEGER PRIMARY KEY,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    prob        REAL,
    cluster_id  INTEGER,
    sharp       REAL,   -- face-region Laplacian variance, normalised 0-1
    expr        REAL    -- portrait expression quality 0-1 (NULL if not scored)
);

CREATE TABLE IF NOT EXISTS image_tags (
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL
);

-- Persisted across rebuilds:
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id  INTEGER PRIMARY KEY,
    name        TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    hash        TEXT PRIMARY KEY,   -- content hash: survives renames AND moves
    decision    TEXT                -- 'keep' | 'del'
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_images_combined   ON images(combined);
CREATE INDEX IF NOT EXISTS idx_images_sharpness  ON images(sharpness);
CREATE INDEX IF NOT EXISTS idx_images_aesthetic  ON images(para_aesthetic);
CREATE INDEX IF NOT EXISTS idx_images_dup        ON images(dup_group);
CREATE INDEX IF NOT EXISTS idx_images_scene      ON images(scene_group);
CREATE INDEX IF NOT EXISTS idx_faces_image       ON faces(image_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster     ON faces(cluster_id);
CREATE INDEX IF NOT EXISTS idx_tags_image        ON image_tags(image_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag          ON image_tags(tag);
"""

# FTS5 caption search (separate so callers can fall back gracefully if the
# SQLite build lacks the extension).
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS images_fts
    USING fts5(caption, content='images', content_rowid='id');
"""

# Manual face edits that must survive a fresh build_db ingest. Keyed by the
# image's content hash + the face bbox, so they re-apply to the same face after
# the faces table is rebuilt from a regenerated report.
FACE_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS face_overrides (
    hash        TEXT NOT NULL,
    bbox        TEXT NOT NULL,   -- "x1,y1,x2,y2" rounded to 1 decimal
    action      TEXT NOT NULL,   -- 'assign' | 'delete'
    cluster_id  INTEGER,         -- target cluster for 'assign'
    PRIMARY KEY (hash, bbox)
);
"""

# Runtime-configurable photo roots: the directories the server's file-reveal
# guardrail is allowed to open into. Persisted here (not in meta) so they
# survive a build_db rebuild — build_db only clears images/faces/tags and never
# touches this table.
PHOTO_ROOTS_DDL = """
CREATE TABLE IF NOT EXISTS photo_roots (
    path TEXT PRIMARY KEY
);
"""

# The set of source folders that make up the catalog. Analyze scans the UNION of
# these in one pass (so dedup / scenes / face clusters stay global and coherent),
# and the resulting single report rebuilds the library. Persisted here — not in
# meta — so the onboarded set survives a build_db rebuild (which only clears
# images/faces/tags). Distinct from photo_roots: that table only gates the
# file-reveal guardrail, while this one defines what actually gets indexed.
LIBRARY_FOLDERS_DDL = """
CREATE TABLE IF NOT EXISTS library_folders (
    path TEXT PRIMARY KEY
);
"""

# App-managed recycle bin. Each row is file-level (image_id/path), not just
# content-hash-level, so exact byte-identical copies can be trashed/restored
# independently once the UI supports per-copy actions.
TRASH_DDL = """
CREATE TABLE IF NOT EXISTS trash_moves (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id     INTEGER,
    hash         TEXT,
    from_path    TEXT NOT NULL,
    trash_path   TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'trashed', -- trashed | restored | emptied | missing
    trashed_at   TEXT,
    restored_at  TEXT,
    emptied_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_trash_state ON trash_moves(state);
CREATE INDEX IF NOT EXISTS idx_trash_image ON trash_moves(image_id);
CREATE INDEX IF NOT EXISTS idx_trash_hash ON trash_moves(hash);
"""

# Long-running web operations (analyze, index, apply, etc.) share one persisted
# task ledger so progress survives browser reconnects and recent history is
# visible after a panel closes. The process itself is not resumable after a
# server restart; startup marks any leftover running task abandoned.
TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    state             TEXT NOT NULL,
    phase             TEXT,
    progress          REAL,
    message           TEXT,
    params_json       TEXT,
    result_json       TEXT,
    error             TEXT,
    cancel_requested  INTEGER DEFAULT 0,
    started           REAL,
    ended             REAL
);

CREATE TABLE IF NOT EXISTS task_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    ts           REAL NOT NULL,
    event_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(task_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_tasks_started ON tasks(started DESC);
CREATE INDEX IF NOT EXISTS idx_task_events_task_seq ON task_events(task_id, seq);
"""

# Cluster-name anchors. A person's name is keyed by cluster_id, but the face
# detector reassigns cluster ids on every re-clustering, so an id-keyed name
# re-binds to the wrong faces after a rebuild. Anchoring the name to the
# cluster's member faces — by image content hash plus the face's bbox geometry —
# lets the name follow the actual people across a rebuild. Matching is by bbox
# overlap (IoU), not exact coordinates, so the name still re-binds when the
# detector nudges a face's box slightly between runs. On ingest each new
# cluster's name is resolved by majority vote of its members' overlapping
# anchors. Persisted across rebuilds; build_db never clears this table.
CLUSTER_ANCHORS_DDL = """
CREATE TABLE IF NOT EXISTS cluster_name_anchors (
    hash    TEXT NOT NULL,
    x1      REAL NOT NULL,
    y1      REAL NOT NULL,
    x2      REAL NOT NULL,
    y2      REAL NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (hash, x1, y1, x2, y2)
);
"""

# Manual scene merges that must survive both a slider re-segmentation and a fresh
# build_db ingest. Keyed by image content hash (stable across rebuilds), grouped
# by group_id: every image whose hash shares a group_id is forced into the same
# scene, so two time-separated bursts the user declared "one scene" stay merged
# no matter how the automatic time-gap segmentation lands. Like face_overrides,
# build_db never clears this table.
SCENE_MERGES_DDL = """
CREATE TABLE IF NOT EXISTS scene_merges (
    group_id INTEGER NOT NULL,
    hash     TEXT NOT NULL,
    PRIMARY KEY (group_id, hash)
);
CREATE INDEX IF NOT EXISTS idx_scene_merges_hash ON scene_merges(hash);
"""

# Columns introduced after the initial release. CREATE IF NOT EXISTS can't add a
# column to an existing table, so ensure_schema() adds them with ALTER on demand.
_IMAGE_LATE_COLUMNS = (
    ("content_hash", "TEXT"), ("mtime", "REAL"), ("fsize", "INTEGER"),
    ("face_sharp", "REAL"), ("face_expr", "REAL"), ("portrait", "REAL"),
    ("scene_group", "INTEGER"), ("capture_time", "REAL"),
    ("dup_central", "REAL"),
)
_FACE_LATE_COLUMNS = (("sharp", "REAL"), ("expr", "REAL"))


def connect(path, *, timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open a *hardened* connection to the library DB — the single chokepoint
    every entry point (server, ingest, backup, CLI) uses so they all treat the
    one shared SQLite file identically:

      - WAL + synchronous=NORMAL: crash-consistent (a power loss can lose the
        last uncommitted txn but not corrupt the file) without the full-fsync tax.
      - busy_timeout: wait out a concurrent writer instead of instantly raising
        'database is locked' — the server and a CLI `sift index` can legitimately
        touch the same DB at once.
      - foreign_keys: enforce the faces->images ON DELETE CASCADE so a delete
        can't leave orphaned faces behind.

    busy_timeout / synchronous / foreign_keys are per-connection and set on every
    open. journal_mode is NOT — WAL is a persistent property of the file, so we
    only switch it when the file isn't already in WAL. Re-issuing
    `PRAGMA journal_mode=WAL` on every connect takes a momentary EXCLUSIVE lock;
    under high-frequency connection churn (e.g. the server's per-poll SSE
    connections) that exclusive grab starves behind the constant readers and can
    block for seconds — wedging the event loop and starving a concurrent
    `sift index`. Reading the mode first is a cheap, lock-free pragma."""
    conn = sqlite3.connect(path, timeout=timeout_ms / 1000)
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")
    row = conn.execute("PRAGMA journal_mode").fetchone()
    if not row or str(row[0]).lower() != "wal":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def create_base_schema(conn) -> None:
    """Create the base tables/indexes. Idempotent; run by build_db on ingest."""
    conn.executescript(BASE_SCHEMA)


def ensure_overrides(conn) -> None:
    """Create just the face_overrides table. Cheap enough to call defensively
    from write paths that record an override."""
    conn.executescript(FACE_OVERRIDES_DDL)


def ensure_anchors(conn) -> None:
    """Create just the cluster_name_anchors table. A pre-existing table in the
    legacy (hash, bbox) text-key format is dropped so the IoU-keyed schema can
    be created; names re-seed from clusters.name via _backfill_anchors_if_empty,
    so no user labor is lost."""
    if conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='cluster_name_anchors'").fetchone():
        if "x1" not in _table_columns(conn, "cluster_name_anchors"):
            conn.execute("DROP TABLE cluster_name_anchors")
    conn.executescript(CLUSTER_ANCHORS_DDL)


def ensure_scene_merges(conn) -> None:
    """Create just the scene_merges table. Cheap enough to call defensively from
    write paths that record or read a manual scene merge."""
    conn.executescript(SCENE_MERGES_DDL)


def ensure_schema(conn) -> None:
    """The one migration authority, shared by ingest and the server.

    Additively upgrades an existing DB so a photos.db built by an older version
    works without a full rebuild: add any late columns, create the overrides
    table, and create the indexes that depend on those late columns. Every step
    is idempotent, so this is safe to call on every startup and every ingest.
    New score columns stay NULL until the next build_db ingest populates them.
    """
    icols = _table_columns(conn, "images")
    for col, decl in _IMAGE_LATE_COLUMNS:
        if col not in icols:
            conn.execute(f"ALTER TABLE images ADD COLUMN {col} {decl}")
    fcols = _table_columns(conn, "faces")
    for col, decl in _FACE_LATE_COLUMNS:
        if col not in fcols:
            conn.execute(f"ALTER TABLE faces ADD COLUMN {col} {decl}")
    ensure_overrides(conn)
    conn.executescript(PHOTO_ROOTS_DDL)
    conn.executescript(LIBRARY_FOLDERS_DDL)
    conn.executescript(TRASH_DDL)
    conn.executescript(TASKS_DDL)
    ensure_anchors(conn)
    ensure_scene_merges(conn)
    # Created after the ALTERs so legacy tables don't index a missing column.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_hash ON images(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_portrait ON images(portrait)")
    # One-time: convert names that were only id-keyed into face anchors, so
    # existing renames also survive the next re-clustering. Runs while the old
    # faces/memberships are still present (ensure_schema is called before
    # build_db wipes them, and on every server start).
    _backfill_anchors_if_empty(conn)


def get_photo_roots(conn) -> list[str]:
    """The configured photo-root directories, as stored (display) paths."""
    conn.executescript(PHOTO_ROOTS_DDL)
    return [r[0] for r in conn.execute("SELECT path FROM photo_roots ORDER BY path")]


def add_photo_root(conn, path: str) -> None:
    conn.executescript(PHOTO_ROOTS_DDL)
    conn.execute("INSERT OR IGNORE INTO photo_roots (path) VALUES (?)", (path,))


def remove_photo_root(conn, path: str) -> None:
    conn.execute("DELETE FROM photo_roots WHERE path = ?", (path,))


def get_library_folders(conn) -> list[str]:
    """The catalog's source folders, as stored (display) paths."""
    conn.executescript(LIBRARY_FOLDERS_DDL)
    return [r[0] for r in conn.execute("SELECT path FROM library_folders ORDER BY path")]


def add_library_folder(conn, path: str) -> None:
    conn.executescript(LIBRARY_FOLDERS_DDL)
    conn.execute("INSERT OR IGNORE INTO library_folders (path) VALUES (?)", (path,))


def remove_library_folder(conn, path: str) -> None:
    conn.execute("DELETE FROM library_folders WHERE path = ?", (path,))


def bbox_key(x1, y1, x2, y2) -> str:
    """Canonical face-bbox key. Mirrors photo_audit's 1-decimal rounding of bbox
    coordinates so a recorded override re-binds to the same face after a rebuild.
    Both ingest and the API MUST produce identical keys — hence one definition."""
    return ",".join(f"{round(float(v), 1)}" for v in (x1, y1, x2, y2))


# Faces re-detected on a later ingest rarely land on byte-identical coordinates,
# so name anchors re-bind by bounding-box overlap rather than exact match. 0.5
# IoU is the usual "same object" bar: forgiving of small detector jitter without
# letting a different face in the same photo steal a name.
ANCHOR_IOU_MIN = 0.5


def _iou(a, b) -> float:
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def portrait_score(face_sharp, face_expr):
    """Combine a face's sharpness and expression into one portrait quality.
    Sharpness dominates (a blurry face is unusable regardless of expression)."""
    if face_sharp is None:
        return None
    if face_expr is None:
        return round(face_sharp, 4)
    return round(0.6 * face_sharp + 0.4 * face_expr, 4)


def largest_face_aggregate(faces):
    """Per-image face aggregate driven by the largest face by bbox area:
    returns (n_faces, face_sharp, face_expr, portrait). `faces` is an iterable
    of (x1, y1, x2, y2, sharp, expr) tuples; an empty set yields
    (0, None, None, None). This is the single definition of how a photo's
    portrait fields are derived from its faces, shared by the ingest insert and
    the override-replay recompute so the two can't disagree."""
    faces = list(faces)
    if not faces:
        return 0, None, None, None
    big = max(faces, key=lambda f: max(0.0, f[2] - f[0]) * max(0.0, f[3] - f[1]))
    sharp, expr = big[4], big[5]
    return len(faces), sharp, expr, portrait_score(sharp, expr)


def next_manual_cluster_id(conn) -> int:
    """Allocate the next manual person id, above MANUAL_CLUSTER_BASE so it never
    collides with a detector cluster id on the next ingest."""
    m = conn.execute("SELECT MAX(cluster_id) FROM clusters WHERE cluster_id >= ?",
                     (MANUAL_CLUSTER_BASE,)).fetchone()[0]
    return (m + 1) if m is not None else MANUAL_CLUSTER_BASE


def _cluster_face_boxes(conn, cluster_id):
    """(hash, x1, y1, x2, y2) for every face currently in a cluster. Faces whose
    image has no content hash can't be anchored and are skipped."""
    return [
        (r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in conn.execute(
            """SELECT i.content_hash, f.x1, f.y1, f.x2, f.y2
               FROM faces f JOIN images i ON i.id = f.image_id
               WHERE f.cluster_id = ? AND i.content_hash IS NOT NULL""",
            (cluster_id,))
    ]


def set_cluster_name_anchors(conn, cluster_id, name) -> None:
    """Re-anchor a cluster's name onto its current member faces so the name
    re-binds to the same people after a rebuild reassigns cluster ids. Any
    existing anchor overlapping a member face (same photo, IoU ≥ threshold) is
    cleared first so a stale name can't linger on that face after re-anchoring;
    a falsy `name` just clears them (the person was un-named)."""
    ensure_anchors(conn)
    boxes = _cluster_face_boxes(conn, cluster_id)
    member_boxes_by_hash: dict[str, list] = {}
    for h, x1, y1, x2, y2 in boxes:
        member_boxes_by_hash.setdefault(h, []).append((x1, y1, x2, y2))
    for h, member_boxes in member_boxes_by_hash.items():
        existing = conn.execute(
            "SELECT x1, y1, x2, y2 FROM cluster_name_anchors WHERE hash=?",
            (h,)).fetchall()
        for ax1, ay1, ax2, ay2 in existing:
            if any(_iou((ax1, ay1, ax2, ay2), mb) >= ANCHOR_IOU_MIN
                   for mb in member_boxes):
                conn.execute(
                    "DELETE FROM cluster_name_anchors "
                    "WHERE hash=? AND x1=? AND y1=? AND x2=? AND y2=?",
                    (h, ax1, ay1, ax2, ay2))
    if name:
        for h, x1, y1, x2, y2 in boxes:
            conn.execute(
                "INSERT OR REPLACE INTO cluster_name_anchors "
                "(hash, x1, y1, x2, y2, name) VALUES (?,?,?,?,?,?)",
                (h, x1, y1, x2, y2, name))


def resolve_anchor_names(conn, members_by_cluster) -> dict:
    """Map {cluster_id: [(hash, x1, y1, x2, y2), ...]} → {cluster_id: name} from
    anchored names of member faces. A member face adopts the name of the
    best-overlapping anchor in the same photo (IoU ≥ threshold). Each name is
    awarded to the single cluster with the most votes for it, so a stale anchor
    set (after a person is split across re-clustered groups) can't paint one
    name onto several clusters."""
    ensure_anchors(conn)
    anchors_by_hash: dict[str, list] = {}
    for r in conn.execute(
            "SELECT hash, x1, y1, x2, y2, name FROM cluster_name_anchors"):
        anchors_by_hash.setdefault(r[0], []).append(
            (float(r[1]), float(r[2]), float(r[3]), float(r[4]), r[5]))
    if not anchors_by_hash:
        return {}

    votes: dict = {}
    for cid, boxes in members_by_cluster.items():
        c = Counter()
        for h, x1, y1, x2, y2 in boxes:
            cand = anchors_by_hash.get(h)
            if not cand:
                continue
            best_name, best_iou = None, ANCHOR_IOU_MIN
            for ax1, ay1, ax2, ay2, nm in cand:
                iou = _iou((x1, y1, x2, y2), (ax1, ay1, ax2, ay2))
                if iou >= best_iou:
                    best_iou, best_name = iou, nm
            if best_name:
                c[best_name] += 1
        if c:
            votes[cid] = c

    # name → (winning cluster id, vote count for that name in that cluster)
    best_for_name: dict = {}
    for cid, c in votes.items():
        for nm, n in c.items():
            cur = best_for_name.get(nm)
            if cur is None or n > cur[1]:
                best_for_name[nm] = (cid, n)

    # cid → chosen name; if a cluster wins several names, keep the strongest.
    out: dict = {}
    for nm, (cid, n) in best_for_name.items():
        if cid not in out or n > votes[cid][out[cid]]:
            out[cid] = nm
    return out


def _backfill_anchors_if_empty(conn) -> None:
    """First-run migration: if no anchors exist yet, seed them from the current
    id-keyed cluster names so existing renames also become rebuild-durable.
    No-op once any anchor exists, and on DBs with no faces yet."""
    ensure_anchors(conn)
    if conn.execute("SELECT 1 FROM cluster_name_anchors LIMIT 1").fetchone():
        return
    try:
        named = conn.execute(
            "SELECT cluster_id, name FROM clusters "
            "WHERE name IS NOT NULL AND TRIM(name) <> ''").fetchall()
    except Exception:
        return
    for cid, name in named:
        set_cluster_name_anchors(conn, cid, name)
