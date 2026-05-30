#!/usr/bin/env python3
"""
server.py — FastAPI backend for the photo audit web app.

Serves:
  GET  /api/meta                  metadata + facets (clusters, tags, ranges)
  GET  /api/images                faceted, sorted, paginated image query
  GET  /thumb/{id}                cached WebP thumbnail
  GET  /img/{id}                  full-resolution original (served on click)
  GET  /api/decisions             { hash: 'keep'|'del' }
  POST /api/decisions             { hash, decision|null }   set one decision
                                  (decisions are keyed by content hash, so they
                                   survive the underlying files being moved)
  GET  /api/clusters              [ {cluster_id, name} ]
  POST /api/clusters              { cluster_id, name|null }  rename a cluster
  GET  /api/export                full decisions export (kept/deleted/unmarked)

In production it also serves the built React app from ./frontend/dist.

Usage:
  python server.py --db "E:\\F\\!To Pictures\\photos.db"
  # open http://localhost:8000   (prod build)  or run Vite dev on :5173
"""

import sys
import json
import shutil
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Globals set in init() ─────────────────────────────────────────────────────
DB_PATH:    Path = Path()
THUMB_DIR:  Path = Path()
FRONTEND_DIST: Path | None = None

app = FastAPI(title="Photo Audit")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # local single-user tool
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _has_fts(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images_fts'"
    ).fetchone()
    return row is not None


# ── Metadata + facets ─────────────────────────────────────────────────────────

@app.get("/api/meta")
def get_meta():
    with db() as conn:
        meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}

        clusters = [
            {"cluster_id": r["cluster_id"], "name": r["name"],
             "count": conn.execute(
                 "SELECT COUNT(*) FROM faces WHERE cluster_id=?",
                 (r["cluster_id"],)).fetchone()[0]}
            for r in conn.execute("SELECT cluster_id, name FROM clusters ORDER BY cluster_id")
        ]

        tags = [
            {"tag": r["tag"], "count": r["c"]}
            for r in conn.execute(
                "SELECT tag, COUNT(*) c FROM image_tags GROUP BY tag ORDER BY c DESC")
        ]

        rng = conn.execute(
            """SELECT MIN(combined) cmin, MAX(combined) cmax,
                      MIN(sharpness) smin, MAX(sharpness) smax,
                      MIN(para_aesthetic) amin, MAX(para_aesthetic) amax
               FROM images""").fetchone()

        counts = {
            "total":    conn.execute("SELECT COUNT(*) FROM images").fetchone()[0],
            "with_faces": conn.execute("SELECT COUNT(*) FROM images WHERE n_faces>0").fetchone()[0],
            "dup_groups": int(meta.get("duplicate_groups", 0)),
        }

        # Fixed-domain [0,1] histograms so the slider track shows the value
        # distribution behind each range control.
        histograms = {
            "combined":  _histogram(conn, "combined"),
            "sharpness": _histogram(conn, "sharpness"),
            "aesthetic": _histogram(conn, "COALESCE(para_aesthetic, clip_iqa)"),
        }

    return {
        "meta": meta,
        "clusters": clusters,
        "tags": tags,
        "ranges": dict(rng),
        "counts": counts,
        "histograms": histograms,
        "has_para": rng["amin"] is not None,
    }


def _histogram(conn, expr: str, bins: int = 24) -> list[int]:
    """Count rows of `expr` into `bins` equal buckets across the [0,1] domain.
    Values are clamped into [0,1]; NULLs are ignored."""
    counts = [0] * bins
    rows = conn.execute(
        f"SELECT {expr} AS v FROM images WHERE {expr} IS NOT NULL")
    for r in rows:
        v = r["v"]
        if v < 0:
            v = 0.0
        elif v > 1:
            v = 1.0
        b = int(v * bins)
        if b >= bins:
            b = bins - 1
        counts[b] += 1
    return counts


# ── Faceted image query ───────────────────────────────────────────────────────

SORT_COLUMNS = {
    "combined":  "i.combined",
    "sharpness": "i.sharpness",
    "aesthetic": "COALESCE(i.para_aesthetic, i.clip_iqa)",
    "filename":  "i.filename",
}


@app.get("/api/images")
def get_images(
    offset: int = 0,
    limit:  int = Query(60, le=300),
    sort:   str = "combined",
    dir:    str = "asc",
    score_min: float = 0.0, score_max: float = 1.0,
    sharp_min: float = 0.0, sharp_max: float = 1.0,
    aes_min:   float = 0.0, aes_max:   float = 1.0,
    tags:    str | None = None,     # comma-separated, OR match
    people:  str | None = None,     # comma-separated cluster ids, OR match
    dup_mode: str = "all",          # all | groups-only | hide-dups | no-groups
    decision: str = "all",          # all | keep | del | unmarked
    q:       str | None = None,     # caption text search
):
    where = ["i.combined BETWEEN ? AND ?",
             "i.sharpness BETWEEN ? AND ?"]
    params: list = [score_min, score_max, sharp_min, sharp_max]

    # Aesthetic range only constrains rows that have a score
    where.append("(i.para_aesthetic IS NULL OR i.para_aesthetic BETWEEN ? AND ?)")
    params += [aes_min, aes_max]

    if dup_mode == "groups-only":
        where.append("i.dup_group IS NOT NULL")
    elif dup_mode == "no-groups":
        where.append("i.dup_group IS NULL")
    elif dup_mode == "hide-dups":
        # keep only the best-scoring representative of each duplicate group
        where.append(
            "(i.dup_group IS NULL OR i.id = ("
            " SELECT id FROM images i2 WHERE i2.dup_group = i.dup_group"
            " ORDER BY i2.combined DESC, i2.id ASC LIMIT 1))")

    if tags:
        tag_list = [t for t in tags.split(",") if t]
        if tag_list:
            ph = ",".join("?" * len(tag_list))
            where.append(f"i.id IN (SELECT image_id FROM image_tags WHERE tag IN ({ph}))")
            params += tag_list

    if people:
        cids = [int(c) for c in people.split(",") if c.lstrip("-").isdigit()]
        if cids:
            ph = ",".join("?" * len(cids))
            where.append(f"i.id IN (SELECT image_id FROM faces WHERE cluster_id IN ({ph}))")
            params += cids

    if decision == "keep":
        where.append("d.decision = 'keep'")
    elif decision == "del":
        where.append("d.decision = 'del'")
    elif decision == "unmarked":
        where.append("d.decision IS NULL")

    with db() as conn:
        if q:
            if _has_fts(conn):
                where.append("i.id IN (SELECT rowid FROM images_fts WHERE images_fts MATCH ?)")
                params.append(q)
            else:
                where.append("i.caption LIKE ?")
                params.append(f"%{q}%")

        sort_col = SORT_COLUMNS.get(sort, "i.combined")
        sort_dir = "DESC" if dir.lower() == "desc" else "ASC"
        where_sql = " AND ".join(where)

        base = f"""
            FROM images i
            LEFT JOIN decisions d ON d.hash = i.content_hash
            WHERE {where_sql}
        """

        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

        rows = conn.execute(
            f"""SELECT i.*, d.decision {base}
                ORDER BY {sort_col} {sort_dir}, i.id ASC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = _rows_to_items(conn, rows)

    return {"total": total, "offset": offset, "limit": limit, "items": items}


def _rows_to_items(conn, rows) -> list[dict]:
    """Attach face boxes + tags to a batch of image rows and shape them
    into the JSON payload used by /api/images and /api/groups."""
    ids = [r["id"] for r in rows]
    faces_by_img: dict[int, list] = {i: [] for i in ids}
    tags_by_img:  dict[int, list] = {i: [] for i in ids}
    if ids:
        ph = ",".join("?" * len(ids))
        for fr in conn.execute(
            f"""SELECT image_id, x1, y1, x2, y2, prob, cluster_id
                FROM faces WHERE image_id IN ({ph})""", ids):
            faces_by_img[fr["image_id"]].append({
                "bbox": [fr["x1"], fr["y1"], fr["x2"], fr["y2"]],
                "prob": fr["prob"], "cluster_id": fr["cluster_id"],
            })
        for tr in conn.execute(
            f"SELECT image_id, tag FROM image_tags WHERE image_id IN ({ph})", ids):
            tags_by_img[tr["image_id"]].append(tr["tag"])

    items = []
    for r in rows:
        d = dict(r)
        items.append({
            "id": d["id"], "filename": d["filename"], "path": d["path"],
            "hash": d["content_hash"],
            "combined": d["combined"], "sharpness": d["sharpness"],
            "para_aesthetic": d["para_aesthetic"],
            "para_composition": d["para_composition"],
            "para_light": d["para_light"],
            "clip_iqa": d["clip_iqa"],
            "dup_group": d["dup_group"],
            "caption": d["caption"],
            "imgw": d["imgw"], "imgh": d["imgh"],
            "decision": d.get("decision"),
            "faces": faces_by_img.get(d["id"], []),
            "tags": tags_by_img.get(d["id"], []),
        })
    return items


# ── Duplicate groups ──────────────────────────────────────────────────────────

@app.get("/api/groups")
def get_groups(
    offset: int = 0,
    limit:  int = Query(40, le=200),
    order:  str = "size",          # size | id
):
    """Return duplicate groups (paginated by group), each with all of its
    member photos ordered best-first. Powers the stacked 'Groups' view."""
    order_sql = "c DESC, dup_group ASC" if order == "size" else "dup_group ASC"
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(DISTINCT dup_group) FROM images WHERE dup_group IS NOT NULL"
        ).fetchone()[0]

        grp_rows = conn.execute(
            f"""SELECT dup_group, COUNT(*) c
                FROM images WHERE dup_group IS NOT NULL
                GROUP BY dup_group
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

        groups = []
        for gr in grp_rows:
            gid = gr["dup_group"]
            members = conn.execute(
                """SELECT i.*, d.decision
                   FROM images i
                   LEFT JOIN decisions d ON d.hash = i.content_hash
                   WHERE i.dup_group = ?
                   ORDER BY i.combined DESC, i.id ASC""",
                (gid,),
            ).fetchall()
            items = _rows_to_items(conn, members)
            groups.append({"dup_group": gid, "count": len(items), "items": items})

    return {"total": total, "offset": offset, "limit": limit, "groups": groups}


# ── Image serving ─────────────────────────────────────────────────────────────

@app.get("/thumb/{image_id}")
def serve_thumb(image_id: int):
    with db() as conn:
        row = conn.execute("SELECT thumb FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    path = THUMB_DIR / row["thumb"]
    if not path.exists():
        raise HTTPException(404, "thumbnail not generated")
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/img/{image_id}")
def serve_full(image_id: int):
    with db() as conn:
        row = conn.execute("SELECT path FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "original file missing")
    return FileResponse(path)


# ── Decisions (keep / delete) ─────────────────────────────────────────────────

@app.get("/api/decisions")
def get_decisions():
    with db() as conn:
        return {r["hash"]: r["decision"]
                for r in conn.execute("SELECT hash, decision FROM decisions")}


@app.post("/api/decisions")
def set_decision(payload: dict = Body(...)):
    h = payload.get("hash")
    decision = payload.get("decision")    # 'keep' | 'del' | None
    # Backward-compatible fallback: resolve a path to its content hash.
    if not h and payload.get("path"):
        with db() as conn:
            row = conn.execute("SELECT content_hash FROM images WHERE path=?",
                               (payload["path"],)).fetchone()
            h = row["content_hash"] if row else None
    if not h:
        raise HTTPException(400, "hash required")
    with db() as conn:
        if decision in ("keep", "del"):
            conn.execute(
                "INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                (h, decision))
        else:
            conn.execute("DELETE FROM decisions WHERE hash=?", (h,))
        conn.commit()
    return {"ok": True}


# ── Cluster names ─────────────────────────────────────────────────────────────

@app.get("/api/clusters")
def get_clusters():
    with db() as conn:
        return [dict(r) for r in
                conn.execute("SELECT cluster_id, name FROM clusters ORDER BY cluster_id")]


@app.post("/api/clusters")
def rename_cluster(payload: dict = Body(...)):
    cid = payload.get("cluster_id")
    name = payload.get("name")
    if cid is None:
        raise HTTPException(400, "cluster_id required")
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
                     (int(cid), name or None))
        conn.commit()
    return {"ok": True}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/api/export")
def export_decisions():
    with db() as conn:
        rows = conn.execute(
            """SELECT i.path, i.filename, i.combined, d.decision
               FROM images i LEFT JOIN decisions d ON d.hash = i.content_hash""").fetchall()
    out = {"kept": [], "deleted": [], "unmarked": []}
    for r in rows:
        entry = {"path": r["path"], "filename": r["filename"], "combined": r["combined"]}
        if r["decision"] == "keep":
            out["kept"].append(entry)
        elif r["decision"] == "del":
            out["deleted"].append(entry)
        else:
            out["unmarked"].append(entry)
    return JSONResponse(out, headers={
        "Content-Disposition": "attachment; filename=audit_decisions.json"})


# ── Bulk auto-cull duplicate groups ───────────────────────────────────────────

@app.post("/api/groups/autocull")
def autocull_groups():
    """For every duplicate group, mark the best-scoring photo 'keep' and the
    rest 'del'. Overwrites existing marks within groups. Marks only — files
    are not touched until /api/apply."""
    with db() as conn:
        gids = [r["dup_group"] for r in conn.execute(
            "SELECT DISTINCT dup_group FROM images WHERE dup_group IS NOT NULL")]
        kept = deleted = 0
        for gid in gids:
            members = conn.execute(
                """SELECT content_hash FROM images WHERE dup_group=?
                   ORDER BY combined DESC, id ASC""", (gid,)).fetchall()
            for i, m in enumerate(members):
                if not m["content_hash"]:
                    continue
                dec = "keep" if i == 0 else "del"
                conn.execute(
                    "INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                    (m["content_hash"], dec))
                kept += (i == 0)
                deleted += (i != 0)
        conn.commit()
    return {"groups": len(gids), "kept": kept, "deleted": deleted}


# ── Apply decisions: move 'del' files to a _rejected/ folder (reversible) ──────

def _ensure_moves_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applied_moves (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id  INTEGER,
            from_path TEXT,
            to_path   TEXT,
            ts        TEXT
        )""")


def _unique_dest(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, suf = Path(name).stem, Path(name).suffix
    k = 1
    while (dest_dir / f"{stem}_{k}{suf}").exists():
        k += 1
    return dest_dir / f"{stem}_{k}{suf}"


def _rejected_dir(conn) -> Path:
    root = conn.execute("SELECT value FROM meta WHERE key='folder'").fetchone()
    if not root:
        raise HTTPException(500, "library folder unknown")
    return Path(root["value"]) / "_rejected"


@app.get("/api/apply/status")
def apply_status():
    with db() as conn:
        _ensure_moves_table(conn)
        rej = _rejected_dir(conn)
        rej_str = str(rej)
        # Files marked 'del' that still live outside _rejected = movable.
        pending = 0
        for r in conn.execute(
            """SELECT i.path FROM images i
               JOIN decisions d ON d.hash = i.content_hash
               WHERE d.decision='del'"""):
            if not str(r["path"]).startswith(rej_str):
                pending += 1
        applied = conn.execute("SELECT COUNT(*) FROM applied_moves").fetchone()[0]
    return {"pending": pending, "applied": applied, "rejected_dir": rej_str}


@app.post("/api/apply")
def apply_decisions():
    """Move every 'del'-marked file into <library>/_rejected/. Updates each
    image's stored path and logs the move so it can be undone. Never deletes."""
    moved, skipped = 0, 0
    with db() as conn:
        _ensure_moves_table(conn)
        rej = _rejected_dir(conn)
        rej_str = str(rej)
        rows = conn.execute(
            """SELECT i.id, i.path FROM images i
               JOIN decisions d ON d.hash = i.content_hash
               WHERE d.decision='del'""").fetchall()
        if rows:
            rej.mkdir(parents=True, exist_ok=True)
        for r in rows:
            src = Path(r["path"])
            if str(src).startswith(rej_str) or not src.exists():
                skipped += 1
                continue
            dest = _unique_dest(rej, src.name)
            try:
                shutil.move(str(src), str(dest))
            except OSError:
                skipped += 1
                continue
            # Only the path moves; the decision stays attached via content hash.
            conn.execute("UPDATE images SET path=? WHERE id=?", (str(dest), r["id"]))
            conn.execute(
                "INSERT INTO applied_moves (image_id, from_path, to_path, ts) VALUES (?,?,?,?)",
                (r["id"], str(src), str(dest), datetime.now().isoformat(timespec="seconds")))
            moved += 1
        conn.commit()
    return {"moved": moved, "skipped": skipped, "rejected_dir": rej_str}


@app.post("/api/apply/undo")
def undo_apply():
    """Move every logged file back to its original location and clear the log."""
    restored, skipped = 0, 0
    with db() as conn:
        _ensure_moves_table(conn)
        rows = conn.execute(
            "SELECT id, image_id, from_path, to_path FROM applied_moves ORDER BY id DESC"
        ).fetchall()
        for r in rows:
            src = Path(r["to_path"])
            dst = Path(r["from_path"])
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                except OSError:
                    skipped += 1
                    continue
                conn.execute("UPDATE images SET path=? WHERE id=?", (str(dst), r["image_id"]))
                restored += 1
            else:
                skipped += 1
            conn.execute("DELETE FROM applied_moves WHERE id=?", (r["id"],))
        conn.commit()
    return {"restored": restored, "skipped": skipped}


# ── Serve built frontend (production) ─────────────────────────────────────────
# Mounted last so it doesn't shadow /api routes. Only if a build exists.

def _mount_frontend():
    if FRONTEND_DIST and FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
        print(f"  Serving frontend build from {FRONTEND_DIST}")
    else:
        print("  No frontend build found — run Vite dev server (npm run dev) on :5173")


def main() -> None:
    global DB_PATH, THUMB_DIR, FRONTEND_DIST
    ap = argparse.ArgumentParser()
    ap.add_argument("--db",     required=True)
    ap.add_argument("--thumbs", default=None)
    ap.add_argument("--host",   default="127.0.0.1")
    ap.add_argument("--port",   type=int, default=8000)
    args = ap.parse_args()

    DB_PATH = Path(args.db)
    if not DB_PATH.exists():
        print(f"Error: DB {DB_PATH} not found — run build_db.py first"); sys.exit(1)
    THUMB_DIR = Path(args.thumbs) if args.thumbs else DB_PATH.parent / ".thumbs"
    FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

    print(f"DB:     {DB_PATH}")
    print(f"Thumbs: {THUMB_DIR}")
    _mount_frontend()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
