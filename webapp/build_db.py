#!/usr/bin/env python3
"""
build_db.py — Ingest an audit_report.json into a SQLite database and generate
              WebP thumbnails for fast grid rendering.

This is the data-prep step for the web app. Run it after photo_audit.py.

Usage:
  python build_db.py <audit_report.json> [options]

Options:
  --db <path>          SQLite output path (default: <report_dir>/photos.db)
  --thumbs <dir>       Thumbnail cache dir (default: <report_dir>/.thumbs)
  --thumb-size <px>    Max thumbnail dimension (default: 400)
  --thumb-quality <n>  WebP quality 1-100 (default: 80)
  --workers <n>        Thumbnail worker threads (default: 8)
  --skip-thumbs        Only (re)build the DB, don't regenerate thumbnails
  --force-thumbs       Regenerate thumbnails even if they already exist

The DB preserves any existing decisions/cluster names when rebuilt, so you can
re-ingest a fresh audit without losing your keep/delete marks or face names.
"""

import sys
import json
import argparse
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageOps
from tqdm import tqdm


SCHEMA = """
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
    n_faces         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS faces (
    id          INTEGER PRIMARY KEY,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    prob        REAL,
    cluster_id  INTEGER
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
    path        TEXT PRIMARY KEY,   -- keyed by path so it survives id changes
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

# FTS5 caption search (separate so we can fall back gracefully if unavailable)
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS images_fts
    USING fts5(caption, content='images', content_rowid='id');
"""


def make_thumb(args: tuple) -> tuple:
    """Worker: generate one WebP thumbnail.

    Returns (image_id, status, err) where status is "made" | "skip" | "fail".
    A thumbnail is reused only when it already exists AND is at least as new as
    its source file — so edited/replaced photos get fresh thumbnails while
    unchanged ones are skipped, making re-ingest fast."""
    image_id, src_path, dst_path, size, quality, force = args
    try:
        if dst_path.exists() and not force:
            try:
                if dst_path.stat().st_mtime >= src_path.stat().st_mtime:
                    return image_id, "skip", None
            except OSError:
                # Source missing/unreadable — keep the existing thumb rather
                # than failing the whole build.
                return image_id, "skip", None
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)        # honour camera rotation
            im = im.convert("RGB")
            im.thumbnail((size, size), Image.LANCZOS)
            im.save(dst_path, "WEBP", quality=quality, method=4)
        return image_id, "made", None
    except Exception as e:
        return image_id, "fail", str(e)


def build(report_path: Path, db_path: Path, thumb_dir: Path,
          thumb_size: int, thumb_quality: int, workers: int,
          skip_thumbs: bool, force_thumbs: bool) -> None:

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    images = report["images"]
    print(f"Loaded {len(images)} image records from {report_path.name}")

    thumb_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    has_fts = True
    try:
        conn.executescript(FTS_SCHEMA)
    except sqlite3.OperationalError:
        has_fts = False
        print("  (FTS5 unavailable — caption search will use LIKE fallback)")

    # ── Preserve decisions + cluster names across rebuilds ───────────────────
    prev_decisions = dict(conn.execute("SELECT path, decision FROM decisions").fetchall())
    prev_names     = dict(conn.execute("SELECT cluster_id, name FROM clusters").fetchall())
    print(f"  Preserving {len(prev_decisions)} decisions, {len(prev_names)} cluster names")

    # ── Wipe rebuildable tables ──────────────────────────────────────────────
    conn.execute("DELETE FROM images")
    conn.execute("DELETE FROM faces")
    conn.execute("DELETE FROM image_tags")
    if has_fts:
        conn.execute("DELETE FROM images_fts")

    # ── Insert image records ─────────────────────────────────────────────────
    cluster_ids_seen: set[int] = set()
    thumb_jobs: list[tuple] = []

    for idx, im in enumerate(images):
        thumb_name = f"{idx}.webp"
        conn.execute(
            """INSERT INTO images
               (id, path, filename, sharpness, combined, raw_laplacian, dup_group,
                para_aesthetic, para_quality, para_composition, para_light,
                para_color, para_dof, para_content, clip_iqa, caption,
                imgw, imgh, thumb, n_faces)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (idx, im["path"], im["filename"],
             im.get("sharpness"), im.get("combined"), im.get("raw_laplacian"),
             im.get("dup_group"),
             im.get("para_aesthetic"), im.get("para_quality"),
             im.get("para_composition"), im.get("para_light"),
             im.get("para_color"), im.get("para_dof"), im.get("para_content"),
             im.get("clip_iqa"), im.get("caption"),
             im.get("imgw"), im.get("imgh"), thumb_name,
             len(im.get("faces", []))),
        )

        for f in im.get("faces", []):
            x1, y1, x2, y2 = f["bbox"]
            cid = f.get("cluster_id", -1)
            if cid >= 0:
                cluster_ids_seen.add(cid)
            conn.execute(
                """INSERT INTO faces (image_id, x1, y1, x2, y2, prob, cluster_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (idx, x1, y1, x2, y2, f.get("prob"), cid),
            )

        for tag in im.get("tags", []):
            conn.execute("INSERT INTO image_tags (image_id, tag) VALUES (?,?)",
                         (idx, tag))

        if has_fts and im.get("caption"):
            conn.execute("INSERT INTO images_fts (rowid, caption) VALUES (?,?)",
                         (idx, im["caption"]))

        thumb_jobs.append((idx, Path(im["path"]), thumb_dir / thumb_name,
                           thumb_size, thumb_quality, force_thumbs))

    # ── Re-seed clusters: keep prior names, add any new cluster ids ──────────
    # Also pick up names embedded in the report (from --face-ref matching).
    report_names: dict[int, str] = {}
    for im in images:
        for f in im.get("faces", []):
            cid = f.get("cluster_id", -1)
            if cid >= 0 and f.get("name"):
                report_names[cid] = f["name"]

    for cid in sorted(cluster_ids_seen):
        name = prev_names.get(cid) or report_names.get(cid)
        conn.execute(
            "INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
            (cid, name))

    # ── Restore decisions for paths that still exist ─────────────────────────
    for path, decision in prev_decisions.items():
        conn.execute("INSERT OR REPLACE INTO decisions (path, decision) VALUES (?,?)",
                     (path, decision))

    # ── Meta ─────────────────────────────────────────────────────────────────
    for k in ("folder", "backend", "caption_model", "face_model"):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                     (k, str(report.get(k))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("total_images", str(len(images))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("duplicate_groups", str(report.get("duplicate_groups", 0))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("thumb_size", str(thumb_size)))

    conn.commit()
    print(f"  DB written: {db_path}  ({len(cluster_ids_seen)} clusters)")

    # ── Generate thumbnails in parallel ──────────────────────────────────────
    if skip_thumbs:
        print("  Skipping thumbnail generation (--skip-thumbs)")
    else:
        made, skipped, failed = 0, 0, 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(make_thumb, job) for job in thumb_jobs]
            for fut in tqdm(as_completed(futures), total=len(futures),
                            desc="Thumbnails"):
                _id, status, err = fut.result()
                if status == "made":
                    made += 1
                elif status == "skip":
                    skipped += 1
                else:
                    failed += 1
                    if failed <= 10:
                        print(f"  thumb fail (id={_id}): {err}")
        print(f"  Thumbnails: {made} generated, {skipped} reused, {failed} failed")

    conn.close()
    print("\nDone. Next:  python server.py --db", db_path)


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("report", help="Path to audit_report.json")
    ap.add_argument("--db",            default=None)
    ap.add_argument("--thumbs",        default=None)
    ap.add_argument("--thumb-size",    type=int, default=400)
    ap.add_argument("--thumb-quality", type=int, default=80)
    ap.add_argument("--workers",       type=int, default=8)
    ap.add_argument("--skip-thumbs",   action="store_true")
    ap.add_argument("--force-thumbs",  action="store_true")
    args = ap.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: {report_path} not found"); sys.exit(1)

    db_path   = Path(args.db)     if args.db     else report_path.parent / "photos.db"
    thumb_dir = Path(args.thumbs) if args.thumbs else report_path.parent / ".thumbs"

    build(report_path, db_path, thumb_dir,
          args.thumb_size, args.thumb_quality, args.workers,
          args.skip_thumbs, args.force_thumbs)


if __name__ == "__main__":
    main()
