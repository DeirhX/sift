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
    portrait        REAL    -- combined portrait quality of the largest face
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

# Columns introduced after the initial release. CREATE IF NOT EXISTS can't add a
# column to an existing table, so ensure_schema() adds them with ALTER on demand.
_IMAGE_LATE_COLUMNS = (
    ("content_hash", "TEXT"), ("mtime", "REAL"), ("fsize", "INTEGER"),
    ("face_sharp", "REAL"), ("face_expr", "REAL"), ("portrait", "REAL"),
)
_FACE_LATE_COLUMNS = (("sharp", "REAL"), ("expr", "REAL"))


def _table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def create_base_schema(conn) -> None:
    """Create the base tables/indexes. Idempotent; run by build_db on ingest."""
    conn.executescript(BASE_SCHEMA)


def ensure_overrides(conn) -> None:
    """Create just the face_overrides table. Cheap enough to call defensively
    from write paths that record an override."""
    conn.executescript(FACE_OVERRIDES_DDL)


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
    # Created after the ALTERs so legacy tables don't index a missing column.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_hash ON images(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_images_portrait ON images(portrait)")


def get_photo_roots(conn) -> list[str]:
    """The configured photo-root directories, as stored (display) paths."""
    conn.executescript(PHOTO_ROOTS_DDL)
    return [r[0] for r in conn.execute("SELECT path FROM photo_roots ORDER BY path")]


def add_photo_root(conn, path: str) -> None:
    conn.executescript(PHOTO_ROOTS_DDL)
    conn.execute("INSERT OR IGNORE INTO photo_roots (path) VALUES (?)", (path,))


def remove_photo_root(conn, path: str) -> None:
    conn.execute("DELETE FROM photo_roots WHERE path = ?", (path,))


def bbox_key(x1, y1, x2, y2) -> str:
    """Canonical face-bbox key. Mirrors photo_audit's 1-decimal rounding of bbox
    coordinates so a recorded override re-binds to the same face after a rebuild.
    Both ingest and the API MUST produce identical keys — hence one definition."""
    return ",".join(f"{round(float(v), 1)}" for v in (x1, y1, x2, y2))


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
