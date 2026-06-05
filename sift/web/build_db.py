#!/usr/bin/env python3
"""
build_db.py — Ingest an audit_report.json into a SQLite database and generate
              WebP thumbnails for fast grid rendering.

This is the data-prep step for the web app. Run it after `sift analyze`.

Usage:
  sift index <audit_report.json> [options]

Options:
  --db <path>          SQLite output path (default: <report_dir>/photos.db)
  --thumbs <dir>       Thumbnail cache dir (default: <report_dir>/.thumbs)
  --thumb-size <px>    Max thumbnail dimension (default: 400)
  --thumb-quality <n>  WebP quality 1-100 (default: 80)
  --workers <n>        Thumbnail worker threads (default: 8)
  --skip-thumbs        Only (re)build the DB, don't regenerate thumbnails
  --force-thumbs       Regenerate thumbnails even if they already exist
  --no-prune           Keep thumbnails no longer referenced by any image
                       (by default, orphaned thumbnails are deleted)

The DB preserves any existing decisions/cluster names when rebuilt, so you can
re-ingest a fresh audit without losing your keep/delete marks or face names.

Incremental ingest:
  Each image is content-hashed (blake2b of its bytes). The hash is reused for
  files whose (mtime, size) are unchanged since the last build, so re-ingesting
  a regenerated report only re-hashes and re-thumbnails files that actually
  changed. Thumbnails are named by content hash, so re-sorting the report no
  longer invalidates the cache.

Decisions are keyed by content hash rather than path, so keep/delete marks
survive moving or reorganizing the underlying files.
"""

import sys
import json
import hashlib
import argparse
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageOps
from tqdm import tqdm

from sift.web import photodb
from sift.web.photodb import bbox_key, largest_face_aggregate
from sift.audit.imaging import load_rgb

HASH_CHUNK = 1 << 20   # 1 MiB read blocks when hashing file contents


def hash_file(path: Path) -> str:
    """blake2b-128 of the file's bytes (32 hex chars). Falls back to hashing
    the path string when the file can't be read, so an unreadable image still
    gets a stable key rather than crashing the build."""
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return hashlib.blake2b(str(path).encode("utf-8"), digest_size=16).hexdigest()


def process_image(args: tuple) -> tuple:
    """Worker: ensure an image's content hash and WebP thumbnail exist.

    Computes the content hash only when not already known (i.e. the file is new
    or changed), and generates the thumbnail only when it's missing or --force.
    Thumbnails are named by content hash so they're stable across re-sorts.

    Returns (idx, content_hash, raw_size|None, status, err) where status is
    "made" | "skip" | "fail". raw_size is (w, h) before EXIF transpose — the
    same coordinate space the face detector uses — and is None when the source
    wasn't opened (thumbnail skipped)."""
    idx, src_path, known_hash, thumb_dir, size, quality, force, skip_thumbs = args
    try:
        content_hash = known_hash or hash_file(src_path)
        raw_size = None
        status = "skip"
        if not skip_thumbs:
            dst = thumb_dir / f"{content_hash}.webp"
            if force or not dst.exists():
                with load_rgb(src_path) as im:
                    raw_size = im.size                      # pre-transpose (w, h)
                    out = ImageOps.exif_transpose(im)       # honour camera rotation
                    out = out.convert("RGB")
                    out.thumbnail((size, size), Image.LANCZOS)
                    out.save(dst, "WEBP", quality=quality, method=4)
                status = "made"
        return idx, content_hash, raw_size, status, None
    except Exception as e:
        return idx, known_hash, None, "fail", str(e)


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def build(report_path: Path, db_path: Path, thumb_dir: Path,
          thumb_size: int, thumb_quality: int, workers: int,
          skip_thumbs: bool, force_thumbs: bool, prune: bool = True,
          progress_json: bool = False) -> None:

    def emit_progress(phase: str, pct: float, message: str,
                      current: int | None = None, total: int | None = None):
        if not progress_json:
            return
        print("SIFT_PROGRESS " + json.dumps({
            "phase": phase, "pct": pct, "message": message,
            "current": current, "total": total,
        }), flush=True)

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    images = report["images"]
    print(f"Loaded {len(images)} image records from {report_path.name}")
    emit_progress("load", 0.05, f"Loaded {len(images)} image records", 0, len(images))

    thumb_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    photodb.create_base_schema(conn)
    has_fts = True
    try:
        conn.executescript(photodb.FTS_SCHEMA)
    except sqlite3.OperationalError:
        has_fts = False
        print("  (FTS5 unavailable — caption search will use LIKE fallback)")

    # Single migration authority (shared with the server): add late columns,
    # the overrides table, and the indexes that depend on those columns, so an
    # older photos.db is upgraded in place rather than needing a full rebuild.
    photodb.ensure_schema(conn)

    # ── Snapshot prior state before wiping ───────────────────────────────────
    prev_names = dict(conn.execute("SELECT cluster_id, name FROM clusters").fetchall())

    # Prior per-path signature so we can reuse content hashes for unchanged
    # files instead of re-reading their bytes. Guarded for pre-incremental DBs.
    prev_sig: dict[str, tuple] = {}
    if {"content_hash", "mtime", "fsize"} <= set(_columns(conn, "images")):
        for r in conn.execute(
            "SELECT path, content_hash, mtime, fsize FROM images "
            "WHERE content_hash IS NOT NULL"):
            prev_sig[r[0]] = (r[1], r[2], r[3])

    # Prior decisions, keyed by whatever the existing schema used. Older DBs
    # keyed decisions by 'path'; we translate those to content hashes below.
    dec_cols = _columns(conn, "decisions")
    dec_key = "hash" if "hash" in dec_cols else "path"
    prev_decisions = dict(conn.execute(
        f"SELECT {dec_key}, decision FROM decisions").fetchall())
    print(f"  Preserving {len(prev_decisions)} decisions ({dec_key}-keyed), "
          f"{len(prev_names)} cluster names")

    # ── Wipe rebuildable tables ──────────────────────────────────────────────
    conn.execute("DELETE FROM images")
    conn.execute("DELETE FROM faces")
    conn.execute("DELETE FROM image_tags")
    if has_fts:
        conn.execute("DELETE FROM images_fts")

    # ── Pass 1: resolve content hashes + (re)generate thumbnails ─────────────
    # Reuse the prior hash when a file's (mtime, size) is unchanged; otherwise
    # the worker hashes it. Workers also (re)build any missing thumbnail.
    jobs: list[tuple] = []
    reused = 0
    for idx, im in enumerate(images):
        src = Path(im["path"])
        try:
            st = src.stat()
            sig = (st.st_mtime, st.st_size)
        except OSError:
            sig = (None, None)
        prev = prev_sig.get(im["path"])
        known_hash = None
        if prev and prev[1] == sig[0] and prev[2] == sig[1]:
            known_hash = prev[0]
            reused += 1
        jobs.append([idx, src, known_hash, thumb_dir, thumb_size,
                     thumb_quality, force_thumbs, skip_thumbs,
                     sig[0], sig[1]])

    print(f"  Hash reuse: {reused}/{len(images)} unchanged files")
    emit_progress("hash", 0.15, f"Reusing {reused}/{len(images)} hashes", reused, len(images))

    hashes: dict[int, str] = {}
    raw_sizes: dict[int, tuple] = {}
    made = skipped = failed = 0
    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(process_image, tuple(j[:8])) for j in jobs]
            for fut in tqdm(as_completed(futures), total=len(futures),
                            desc="Hash + thumbnails"):
                idx, h, rsize, status, err = fut.result()
                hashes[idx] = h
                if rsize:
                    raw_sizes[idx] = rsize
                if status == "made":
                    made += 1
                elif status == "skip":
                    skipped += 1
                else:
                    failed += 1
                    if failed <= 10:
                        print(f"  fail (id={idx}): {err}")
                done = made + skipped + failed
                if done == len(jobs) or done % 25 == 0:
                    emit_progress("thumbnails", 0.15 + 0.55 * (done / max(1, len(jobs))),
                                  f"Processed {done}/{len(jobs)} images",
                                  done, len(jobs))

    # ── Insert image records ─────────────────────────────────────────────────
    cluster_ids_seen: set[int] = set()

    for idx, im in enumerate(images):
        chash = hashes.get(idx)
        thumb_name = f"{chash}.webp" if chash else None
        rw, rh = raw_sizes.get(idx, (None, None))
        imgw = im.get("imgw") if im.get("imgw") is not None else rw
        imgh = im.get("imgh") if im.get("imgh") is not None else rh

        # Aggregate portrait quality from the largest detected face.
        faces_list = im.get("faces", [])
        _, face_sharp, face_expr, portrait = largest_face_aggregate(
            (*f["bbox"], f.get("sharp"), f.get("expr")) for f in faces_list)

        conn.execute(
            """INSERT INTO images
               (id, path, filename, sharpness, combined, raw_laplacian, dup_group,
                para_aesthetic, para_quality, para_composition, para_light,
                para_color, para_dof, para_content, clip_iqa, caption,
                imgw, imgh, thumb, content_hash, mtime, fsize, n_faces,
                face_sharp, face_expr, portrait, scene_group, capture_time,
                dup_central)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (idx, im["path"], im["filename"],
             im.get("sharpness"), im.get("combined"), im.get("raw_laplacian"),
             im.get("dup_group"),
             im.get("para_aesthetic"), im.get("para_quality"),
             im.get("para_composition"), im.get("para_light"),
             im.get("para_color"), im.get("para_dof"), im.get("para_content"),
             im.get("clip_iqa"), im.get("caption"),
             imgw, imgh, thumb_name, chash, jobs[idx][8], jobs[idx][9],
             len(faces_list), face_sharp, face_expr, portrait,
             im.get("scene_group"), im.get("capture_time"),
             im.get("dup_central")),
        )

        for f in faces_list:
            x1, y1, x2, y2 = f["bbox"]
            cid = f.get("cluster_id", -1)
            if cid >= 0:
                cluster_ids_seen.add(cid)
            conn.execute(
                """INSERT INTO faces (image_id, x1, y1, x2, y2, prob, cluster_id, sharp, expr)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (idx, x1, y1, x2, y2, f.get("prob"), cid, f.get("sharp"), f.get("expr")),
            )

        for tag in im.get("tags", []):
            conn.execute("INSERT INTO image_tags (image_id, tag) VALUES (?,?)",
                         (idx, tag))

        if has_fts and im.get("caption"):
            conn.execute("INSERT INTO images_fts (rowid, caption) VALUES (?,?)",
                         (idx, im["caption"]))
        if (idx + 1) == len(images) or (idx + 1) % 100 == 0:
            emit_progress("database", 0.70 + 0.20 * ((idx + 1) / max(1, len(images))),
                          f"Inserted {idx + 1}/{len(images)} rows",
                          idx + 1, len(images))

    # ── Re-apply persisted manual face edits (reassign / delete) ─────────────
    # Faces were just rebuilt from the report, wiping any prior manual edits.
    # Replay overrides, matched by (image content hash + bbox), so corrections
    # survive a fresh ingest. Detection is deterministic, so the same image
    # yields the same bbox and the override re-binds to the same face.
    overrides = list(conn.execute(
        "SELECT hash, bbox, action, cluster_id FROM face_overrides"))
    if overrides:
        hash_to_id = {h: i for i, h in hashes.items()}
        affected: set[int] = set()
        applied = dropped = 0
        for h, bbox, action, cid in overrides:
            img_id = hash_to_id.get(h)
            if img_id is None:
                continue
            match = None
            for fr in conn.execute(
                "SELECT id, x1, y1, x2, y2 FROM faces WHERE image_id=?", (img_id,)):
                if bbox_key(fr[1], fr[2], fr[3], fr[4]) == bbox:
                    match = fr[0]
                    break
            if match is None:
                continue
            if action == "delete":
                conn.execute("DELETE FROM faces WHERE id=?", (match,))
                dropped += 1
            else:  # assign
                conn.execute("UPDATE faces SET cluster_id=? WHERE id=?", (cid, match))
                if cid is not None and cid >= 0:
                    cluster_ids_seen.add(cid)
                applied += 1
            affected.add(img_id)

        # Recompute per-image face aggregates + counts for touched images.
        for img_id in affected:
            rows = conn.execute(
                "SELECT x1, y1, x2, y2, sharp, expr FROM faces WHERE image_id=?",
                (img_id,)).fetchall()
            n, fs, fe, portrait = largest_face_aggregate(rows)
            conn.execute(
                "UPDATE images SET n_faces=?, face_sharp=?, face_expr=?, portrait=? WHERE id=?",
                (n, fs, fe, portrait, img_id))
        print(f"  Re-applied {applied} reassign + {dropped} delete face overrides")

    # ── Re-seed clusters: re-bind names to people, add any new cluster ids ───
    # Names are anchored to member faces (by content hash + bbox), so they
    # follow the actual people even when the detector hands a cluster a new id.
    # Resolve each cluster's name from its members' anchors first; fall back to
    # the old id-keyed name (still correct for stable manual-cluster ids), then
    # to any name embedded in the report (from --face-ref matching).
    members_by_cluster: dict[int, list] = {}
    for fr in conn.execute(
            "SELECT image_id, x1, y1, x2, y2, cluster_id FROM faces WHERE cluster_id >= 0"):
        h = hashes.get(fr[0])
        if h:
            members_by_cluster.setdefault(fr[5], []).append(
                (h, bbox_key(fr[1], fr[2], fr[3], fr[4])))
    anchor_names = photodb.resolve_anchor_names(conn, members_by_cluster)

    report_names: dict[int, str] = {}
    for im in images:
        for f in im.get("faces", []):
            cid = f.get("cluster_id", -1)
            if cid >= 0 and f.get("name"):
                report_names[cid] = f["name"]

    rebound = 0
    for cid in sorted(cluster_ids_seen):
        name = anchor_names.get(cid) or prev_names.get(cid) or report_names.get(cid)
        if cid in anchor_names and anchor_names[cid] != prev_names.get(cid):
            rebound += 1
        conn.execute(
            "INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
            (cid, name))
    if anchor_names:
        print(f"  Re-bound {len(anchor_names)} person names via face anchors"
              f" ({rebound} to a different cluster id than before)")

    # ── Restore decisions, keyed by content hash ─────────────────────────────
    if dec_key != "hash":
        # Pre-incremental DB keyed decisions by path; rebuild the table on the
        # new schema and translate each path to its content hash. Decisions for
        # files that have since vanished from the library are dropped (we have
        # no old hash to recover them from — this is the one-time migration cost).
        conn.execute("DROP TABLE IF EXISTS decisions")
        conn.execute("CREATE TABLE decisions (hash TEXT PRIMARY KEY, decision TEXT)")
        path_to_hash = {im["path"]: hashes.get(i) for i, im in enumerate(images)}
        migrated = 0
        for path, decision in prev_decisions.items():
            h = path_to_hash.get(path)
            if h:
                conn.execute(
                    "INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                    (h, decision))
                migrated += 1
        print(f"  Migrated {migrated}/{len(prev_decisions)} decisions to content-hash keys")
    else:
        for h, decision in prev_decisions.items():
            conn.execute("INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                         (h, decision))

    # ── Meta ─────────────────────────────────────────────────────────────────
    for k in ("folder", "backend", "caption_model", "face_model", "scene_model"):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                     (k, str(report.get(k))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("total_images", str(len(images))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("duplicate_groups", str(report.get("duplicate_groups", 0))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("scene_groups", str(report.get("scene_groups", 0))))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("thumb_size", str(thumb_size)))

    conn.commit()
    print(f"  DB written: {db_path}  ({len(cluster_ids_seen)} clusters)")
    emit_progress("database", 0.95, "Database committed", len(images), len(images))
    if skip_thumbs:
        print("  Skipped thumbnail generation (--skip-thumbs)")
    else:
        print(f"  Thumbnails: {made} generated, {skipped} reused, {failed} failed")

    # ── Prune orphaned thumbnails (old naming scheme + stale hashes) ─────────
    if prune:
        referenced = {r[0] for r in
                      conn.execute("SELECT thumb FROM images WHERE thumb IS NOT NULL")}
        removed = 0
        for f in thumb_dir.glob("*.webp"):
            if f.name not in referenced:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            print(f"  Pruned {removed} orphaned thumbnails")
    emit_progress("done", 1.0, "Index complete", len(images), len(images))

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
    ap.add_argument("--no-prune",      action="store_true",
                    help="Keep thumbnails no longer referenced by any image")
    ap.add_argument("--progress-json", action="store_true",
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: {report_path} not found"); sys.exit(1)

    db_path   = Path(args.db)     if args.db     else report_path.parent / "photos.db"
    thumb_dir = Path(args.thumbs) if args.thumbs else report_path.parent / ".thumbs"

    build(report_path, db_path, thumb_dir,
          args.thumb_size, args.thumb_quality, args.workers,
          args.skip_thumbs, args.force_thumbs, prune=not args.no_prune,
          progress_json=args.progress_json)


if __name__ == "__main__":
    main()
