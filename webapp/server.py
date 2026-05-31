#!/usr/bin/env python3
"""
server.py — FastAPI backend for the photo audit web app.

Serves:
  GET  /api/meta                  metadata + facets (clusters, tags, ranges)
  GET  /api/images                faceted, sorted, paginated image query
  GET  /thumb/{id}                cached WebP thumbnail
  GET  /img/{id}                  full-resolution original (served on click)
  POST /api/decisions             { hash, decision|null }   set one decision
                                  (decisions are keyed by content hash, so they
                                   survive the underlying files being moved)
  POST /api/clusters              { cluster_id, name|null }  rename a cluster
  POST /api/clusters/merge        { from, into }   merge people
  POST /api/faces/{id}/assign     { cluster_id | new_person } reassign a face
  DELETE /api/faces/{id}          remove a false-positive face box
  GET  /api/export                full decisions export (kept/deleted/unmarked)

In production it also serves the built React app from ./frontend/dist.

Usage:
  python server.py --db "E:\\F\\!To Pictures\\photos.db"
  # open http://localhost:8000   (prod build)  or run Vite dev on :5173
"""

import os
import sys
import json
import time
import string
import shutil
import asyncio
import sqlite3
import argparse
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import photodb
from photodb import bbox_key, MANUAL_CLUSTER_BASE

# ── Globals set in init() ─────────────────────────────────────────────────────
DB_PATH:    Path = Path()
THUMB_DIR:  Path = Path()
FRONTEND_DIST: Path | None = None
# Directories the /api/reveal guardrail will open into. Bounds reveals to the
# photo library so this can't open arbitrary parts of the filesystem.
# PHOTO_ROOT_DIRS holds the stored (display) paths; PHOTO_ROOTS holds their
# case-normalised forms used for prefix matching. Both are refreshed from the
# photo_roots table whenever it changes.
PHOTO_ROOT_DIRS: list[str] = []
PHOTO_ROOTS: list[str] = []

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


def _ensure_schema():
    """Run the shared migration authority so the server works against an older
    photos.db without a rebuild. New score columns stay NULL until the next
    build_db ingest populates them."""
    with db() as conn:
        photodb.ensure_schema(conn)
        conn.commit()


def _has_fts(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images_fts'"
    ).fetchone()
    return row is not None


# Decisions are keyed by content hash, so every query that needs a photo's
# keep/del state joins on it the same way. One definition so the join key can't
# drift across the half-dozen queries that use it.
DEC_ON = "decisions d ON d.hash = i.content_hash"


# ── Metadata + facets ─────────────────────────────────────────────────────────

@app.get("/api/meta")
def get_meta():
    with db() as conn:
        meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}

        clusters = [
            c for c in (
                {"cluster_id": r["cluster_id"], "name": r["name"],
                 "count": conn.execute(
                     "SELECT COUNT(*) FROM faces WHERE cluster_id=?",
                     (r["cluster_id"],)).fetchone()[0]}
                for r in conn.execute(
                    "SELECT cluster_id, name FROM clusters ORDER BY cluster_id"))
            # Hide clusters drained by manual reassignment/deletion.
            if c["count"] > 0
        ]

        tags = [
            {"tag": r["tag"], "count": r["c"]}
            for r in conn.execute(
                "SELECT tag, COUNT(*) c FROM image_tags GROUP BY tag ORDER BY c DESC")
        ]

        has_portrait_col = "portrait" in {c[1] for c in conn.execute("PRAGMA table_info(images)")}

        rng = conn.execute(
            """SELECT MIN(combined) cmin, MAX(combined) cmax,
                      MIN(sharpness) smin, MAX(sharpness) smax,
                      MIN(para_aesthetic) amin, MAX(para_aesthetic) amax
               FROM images""").fetchone()

        n_portrait = (conn.execute(
            "SELECT COUNT(*) FROM images WHERE portrait IS NOT NULL").fetchone()[0]
            if has_portrait_col else 0)

        counts = {
            "total":    conn.execute("SELECT COUNT(*) FROM images").fetchone()[0],
            "with_faces": conn.execute("SELECT COUNT(*) FROM images WHERE n_faces>0").fetchone()[0],
            "with_portrait": n_portrait,
            "dup_groups": int(meta.get("duplicate_groups", 0)),
        }

        # Fixed-domain [0,1] histograms so the slider track shows the value
        # distribution behind each range control.
        histograms = {
            "combined":  _histogram(conn, "combined"),
            "sharpness": _histogram(conn, "sharpness"),
            "aesthetic": _histogram(conn, "COALESCE(para_aesthetic, clip_iqa)"),
        }
        if has_portrait_col:
            histograms["portrait"] = _histogram(conn, "portrait")

    return {
        "meta": meta,
        "clusters": clusters,
        "tags": tags,
        "ranges": dict(rng),
        "counts": counts,
        "histograms": histograms,
        "has_para": rng["amin"] is not None,
        "has_portrait": n_portrait > 0,
        "photo_roots": PHOTO_ROOT_DIRS,
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
    "portrait":  "i.portrait",
    "filename":  "i.filename",
}


def _image_where(conn, *, score_min, score_max, sharp_min, sharp_max,
                 aes_min, aes_max, tags, people, q, decision,
                 portrait_min=0.0, portrait_max=1.0):
    """Build the shared image-level WHERE clauses + params used by both
    /api/images and /api/groups. Clauses reference alias `i` (images) and
    `d` (decisions LEFT JOIN on content hash)."""
    where = ["i.combined BETWEEN ? AND ?",
             "i.sharpness BETWEEN ? AND ?"]
    params: list = [score_min, score_max, sharp_min, sharp_max]

    # Aesthetic range only constrains rows that have a score.
    where.append("(i.para_aesthetic IS NULL OR i.para_aesthetic BETWEEN ? AND ?)")
    params += [aes_min, aes_max]

    # Portrait range only constrains rows that have a portrait score (faces).
    if portrait_min > 0.0 or portrait_max < 1.0:
        where.append("(i.portrait IS NOT NULL AND i.portrait BETWEEN ? AND ?)")
        params += [portrait_min, portrait_max]

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

    if q:
        if _has_fts(conn):
            where.append("i.id IN (SELECT rowid FROM images_fts WHERE images_fts MATCH ?)")
            params.append(q)
        else:
            where.append("i.caption LIKE ?")
            params.append(f"%{q}%")

    return where, params


@app.get("/api/images")
def get_images(
    offset: int = 0,
    limit:  int = Query(60, le=300),
    sort:   str = "combined",
    dir:    str = "asc",
    score_min: float = 0.0, score_max: float = 1.0,
    sharp_min: float = 0.0, sharp_max: float = 1.0,
    aes_min:   float = 0.0, aes_max:   float = 1.0,
    portrait_min: float = 0.0, portrait_max: float = 1.0,
    tags:    str | None = None,     # comma-separated, OR match
    people:  str | None = None,     # comma-separated cluster ids, OR match
    dup_mode: str = "all",          # all | groups-only | hide-dups | no-groups
    decision: str = "all",          # all | keep | del | unmarked
    q:       str | None = None,     # caption text search
):
    with db() as conn:
        where, params = _image_where(
            conn, score_min=score_min, score_max=score_max,
            sharp_min=sharp_min, sharp_max=sharp_max,
            aes_min=aes_min, aes_max=aes_max,
            portrait_min=portrait_min, portrait_max=portrait_max,
            tags=tags, people=people, q=q, decision=decision)

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

        sort_col = SORT_COLUMNS.get(sort, "i.combined")
        sort_dir = "DESC" if dir.lower() == "desc" else "ASC"
        where_sql = " AND ".join(where)

        base = f"""
            FROM images i
            LEFT JOIN {DEC_ON}
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
            f"""SELECT id, image_id, x1, y1, x2, y2, prob, cluster_id, sharp, expr
                FROM faces WHERE image_id IN ({ph})""", ids):
            faces_by_img[fr["image_id"]].append({
                "id": fr["id"],
                "bbox": [fr["x1"], fr["y1"], fr["x2"], fr["y2"]],
                "prob": fr["prob"], "cluster_id": fr["cluster_id"],
                "sharp": fr["sharp"], "expr": fr["expr"],
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
            "face_sharp": d.get("face_sharp"), "face_expr": d.get("face_expr"),
            "portrait": d.get("portrait"),
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
    score_min: float = 0.0, score_max: float = 1.0,
    sharp_min: float = 0.0, sharp_max: float = 1.0,
    aes_min:   float = 0.0, aes_max:   float = 1.0,
    portrait_min: float = 0.0, portrait_max: float = 1.0,
    tags:    str | None = None,
    people:  str | None = None,
    decision: str = "all",
    q:       str | None = None,
):
    """Return duplicate groups (paginated by group), each with all of its
    member photos ordered best-first. The same filters as /api/images apply:
    a group is included when at least one of its members matches, but every
    member is returned so the full duplicate set stays reviewable."""
    order_sql = "c DESC, dup_group ASC" if order == "size" else "dup_group ASC"
    with db() as conn:
        where, params = _image_where(
            conn, score_min=score_min, score_max=score_max,
            sharp_min=sharp_min, sharp_max=sharp_max,
            aes_min=aes_min, aes_max=aes_max,
            portrait_min=portrait_min, portrait_max=portrait_max,
            tags=tags, people=people, q=q, decision=decision)
        where.append("i.dup_group IS NOT NULL")
        where_sql = " AND ".join(where)

        # dup_groups with at least one member passing the filters
        qual = (f"SELECT DISTINCT i.dup_group FROM images i "
                f"LEFT JOIN {DEC_ON} "
                f"WHERE {where_sql}")

        total = conn.execute(f"SELECT COUNT(*) FROM ({qual})", params).fetchone()[0]

        grp_rows = conn.execute(
            f"""SELECT dup_group, COUNT(*) c
                FROM images WHERE dup_group IN ({qual})
                GROUP BY dup_group
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        page_gids = [gr["dup_group"] for gr in grp_rows]

        # Which member ids on this page actually pass the filters — so the UI
        # can show the full group but flag the members outside the filter.
        match_ids: set[int] = set()
        if page_gids:
            gph = ",".join("?" * len(page_gids))
            match_sql = (f"SELECT i.id FROM images i "
                         f"LEFT JOIN {DEC_ON} "
                         f"WHERE {where_sql} AND i.dup_group IN ({gph})")
            match_ids = {r[0] for r in conn.execute(match_sql, params + page_gids)}

        groups = []
        for gr in grp_rows:
            gid = gr["dup_group"]
            members = conn.execute(
                f"""SELECT i.*, d.decision
                   FROM images i
                   LEFT JOIN {DEC_ON}
                   WHERE i.dup_group = ?
                   ORDER BY i.combined DESC, i.id ASC""",
                (gid,),
            ).fetchall()
            items = _rows_to_items(conn, members)
            n_match = 0
            for it in items:
                it["matches"] = it["id"] in match_ids
                n_match += it["matches"]
            groups.append({"dup_group": gid, "count": len(items),
                           "match_count": n_match, "items": items})

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


@app.get("/api/images/{image_id}/locations")
def image_locations(image_id: int):
    """Every path holding the exact same file bytes (matched on content_hash) as
    this image. Lets the UI surface 'this file also lives at N other locations'
    to aid organizing/consolidation. Read-only; decisions stay hash-keyed so all
    copies share one verdict."""
    with db() as conn:
        row = conn.execute(
            "SELECT content_hash FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, "image not found")
        chash = row["content_hash"]
        if not chash:
            return {"hash": None, "count": 1, "locations": []}
        rows = conn.execute(
            "SELECT id, path FROM images WHERE content_hash=? ORDER BY path", (chash,)
        ).fetchall()
        locations = [{"id": r["id"], "path": r["path"],
                      "exists": Path(r["path"]).exists()} for r in rows]
    return {"hash": chash, "count": len(locations), "locations": locations}


def _norm_path(p) -> str:
    """Absolute, case-normalised, trailing-separator-stripped path for prefix
    comparisons (Windows is case-insensitive; normcase also unifies slashes)."""
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _within_roots(norm: str) -> bool:
    """True if `norm` is one of the configured photo roots or sits below it.
    Crucially this also rejects *ancestors* of the roots (e.g. a drive root),
    which the old per-image check wrongly allowed."""
    return any(norm == r or norm.startswith(r + os.sep) for r in PHOTO_ROOTS)


def _minimal_roots(dirs: set[str]) -> list[str]:
    """Collapse a set of dirs to the minimal covering set (drop any dir that is
    a descendant of another), so we don't keep redundant nested roots."""
    out: list[str] = []
    for d in sorted(dirs, key=len):
        if not any(d == o or d.startswith(o + os.sep) for o in out):
            out.append(d)
    return out


def _refresh_roots(conn) -> None:
    """Reload PHOTO_ROOT_DIRS (display) + PHOTO_ROOTS (normalised) from the
    photo_roots table. Call after any mutation and at startup."""
    global PHOTO_ROOT_DIRS, PHOTO_ROOTS
    PHOTO_ROOT_DIRS = photodb.get_photo_roots(conn)
    PHOTO_ROOTS = [_norm_path(p) for p in PHOTO_ROOT_DIRS]


def _default_roots(conn, explicit: list[str] | None) -> list[str]:
    """Seed roots for a DB that has none configured yet. Priority:
      1. explicit --photo-root args,
      2. the DB's stored library folder (meta.folder),
      3. the minimal set of directories that actually contain indexed images."""
    if explicit:
        good = [str(Path(p).resolve()) for p in explicit if Path(p).is_dir()]
        missing = [p for p in explicit if not Path(p).is_dir()]
        if missing:
            print(f"  Warning: --photo-root not found, ignored: {missing}")
        if good:
            return good
    row = conn.execute("SELECT value FROM meta WHERE key='folder'").fetchone()
    if row and row[0] and Path(row[0]).is_dir():
        return [str(Path(row[0]).resolve())]
    parents = {_norm_path(Path(r[0]).parent)
               for r in conn.execute("SELECT path FROM images")}
    return _minimal_roots({d for d in parents if os.path.isdir(d)})


def _init_photo_roots(explicit: list[str] | None) -> None:
    """Load persisted photo roots; if none are stored yet, seed them from the
    launch flag / library folder so the guardrail works out of the box. After
    seeding, roots are managed at runtime via /api/settings."""
    with db() as conn:
        if not photodb.get_photo_roots(conn):
            for p in _default_roots(conn, explicit):
                photodb.add_photo_root(conn, p)
            conn.commit()
        elif explicit:
            print("  Note: --photo-root ignored; roots are configured in settings")
        _refresh_roots(conn)
    print(f"Reveal roots: {PHOTO_ROOT_DIRS or '(none — folder reveal disabled)'}")


def _reveal_in_os(target: Path) -> None:
    """Open a path in the OS file manager: a directory opens directly, a file
    opens its folder with the file selected."""
    if sys.platform.startswith("win"):
        if target.is_dir():
            os.startfile(str(target))                       # noqa: S606 (trusted, validated)
        else:
            # explorer returns exit 1 even on success — fire and forget.
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(str(target))}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open"] + (["-R", str(target)] if target.is_file() else [str(target)]))
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])


@app.post("/api/reveal")
def reveal_path(payload: dict = Body(...)):
    """Open a path in the OS file manager. Bounded to the configured photo
    root(s) (see --photo-root / meta.folder): the target must be a root or sit
    below one, so this can't be turned into an open-anything primitive — and
    unlike the old check, it won't open a root's ancestors (e.g. a drive)."""
    raw = (payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "path required")
    if not PHOTO_ROOTS:
        raise HTTPException(403, "no photo root configured")
    target = Path(raw)
    if not target.exists():
        raise HTTPException(404, "path not found")
    if not _within_roots(_norm_path(target)):
        raise HTTPException(403, "path is outside the configured photo root")

    try:
        _reveal_in_os(target)
    except OSError as e:
        raise HTTPException(500, f"could not open path: {e}")
    return {"ok": True}


# ── Settings: photo roots (runtime-configurable reveal guardrail) ─────────────

def _roots_payload():
    return {"photo_roots": PHOTO_ROOT_DIRS}


@app.get("/api/settings/roots")
def get_roots():
    return _roots_payload()


@app.post("/api/settings/roots")
def add_root(payload: dict = Body(...)):
    """Add a directory to the configured photo roots. Validates it exists and is
    a directory, stores its resolved absolute path, and de-dupes case-insensitively."""
    raw = (payload.get("path") or "").strip().strip('"')
    if not raw:
        raise HTTPException(400, "path required")
    p = Path(raw)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {raw}")
    resolved = str(p.resolve())
    norm = _norm_path(resolved)
    if norm in PHOTO_ROOTS:
        raise HTTPException(409, "already a photo root")
    # Refuse a root nested under an existing one (or vice versa) — redundant and
    # confusing. The narrower/wider pair would both match the same files.
    for existing in PHOTO_ROOTS:
        if norm.startswith(existing + os.sep) or existing.startswith(norm + os.sep):
            raise HTTPException(409, f"overlaps an existing root: {existing}")
    with db() as conn:
        photodb.add_photo_root(conn, resolved)
        conn.commit()
        _refresh_roots(conn)
    return _roots_payload()


@app.delete("/api/settings/roots")
def delete_root(payload: dict = Body(...)):
    """Remove a configured photo root by its stored path."""
    raw = (payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "path required")
    with db() as conn:
        photodb.remove_photo_root(conn, raw)
        conn.commit()
        _refresh_roots(conn)
    return _roots_payload()


# ── Filesystem path autocomplete (settings folder field) ──────────────────────
#
# A browser can't return an absolute folder path from a native picker, so the
# add-root field needs server-side completion instead. This enumerates directory
# *names* only (never file contents), but it is still broader than /api/reveal:
# it is NOT bounded to the configured roots, because you must be able to browse
# anywhere to add a new root. That is consistent with /api/settings/roots, which
# already accepts arbitrary directory paths — but it does mean the endpoint leaks
# directory structure. Keep the server bound to 127.0.0.1.

_FS_COMPLETE_LIMIT = 60


def _list_drives() -> list[str]:
    """Top-level entries when the query is empty: drive roots on Windows, the
    filesystem root on POSIX."""
    if not sys.platform.startswith("win"):
        return ["/"]
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


@app.get("/api/fs/complete")
def fs_complete(q: str = Query("")):
    """Directory autocomplete for the settings folder field. Given a partial
    path, return matching child directories (full paths). If the query ends in a
    separator we list that directory's children; otherwise the last segment is
    treated as a case-insensitive prefix filter against its parent's children."""
    raw = (q or "").strip().strip('"')
    if not raw:
        return {"entries": _list_drives(), "truncated": False}

    # A bare drive ("E:") means that drive's root.
    if sys.platform.startswith("win") and len(raw) == 2 and raw[1] == ":":
        raw += "\\"

    if raw.endswith(("\\", "/")):
        base, prefix = Path(raw), ""
    else:
        p = Path(raw)
        base, prefix = p.parent, p.name

    if not base.is_dir():
        return {"entries": [], "truncated": False}

    pl = prefix.lower()
    entries: list[str] = []
    try:
        with os.scandir(base) as it:
            for e in it:
                try:
                    if e.is_dir() and (not pl or e.name.lower().startswith(pl)):
                        entries.append(e.path)
                except OSError:
                    continue        # unreadable/locked child — skip it
    except (OSError, PermissionError):
        return {"entries": [], "truncated": False}

    entries.sort(key=str.lower)
    return {"entries": entries[:_FS_COMPLETE_LIMIT],
            "truncated": len(entries) > _FS_COMPLETE_LIMIT}


# ── Decisions (keep / delete) ─────────────────────────────────────────────────

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


def _record_override(conn, hash_, bbox, action, cluster_id=None):
    if not hash_:
        return
    photodb.ensure_overrides(conn)
    conn.execute(
        "INSERT OR REPLACE INTO face_overrides (hash, bbox, action, cluster_id) "
        "VALUES (?,?,?,?)", (hash_, bbox, action, cluster_id))


@app.post("/api/clusters/merge")
def merge_clusters(payload: dict = Body(...)):
    """Reassign every face of the `from` cluster(s) into `into`, then drop the
    now-empty source cluster name rows. Pure DB edit — no re-embedding. Each
    moved face is also recorded as a persistent override."""
    into = payload.get("into")
    frm = payload.get("from")
    if into is None or frm is None:
        raise HTTPException(400, "from and into required")
    into = int(into)
    if isinstance(frm, (int, str)):
        frm = [frm]
    frm = [int(c) for c in frm if int(c) != into]
    if not frm:
        return {"ok": True, "moved": 0}
    with db() as conn:
        ph = ",".join("?" * len(frm))
        # Record an override per moved face before the cluster_id changes.
        moving = conn.execute(
            f"""SELECT i.content_hash AS h, f.x1, f.y1, f.x2, f.y2
                FROM faces f JOIN images i ON i.id = f.image_id
                WHERE f.cluster_id IN ({ph})""", frm).fetchall()
        for r in moving:
            _record_override(conn, r["h"], bbox_key(r["x1"], r["y1"], r["x2"], r["y2"]),
                             "assign", into)
        cur = conn.execute(
            f"UPDATE faces SET cluster_id=? WHERE cluster_id IN ({ph})", [into] + frm)
        moved = cur.rowcount
        conn.execute(f"DELETE FROM clusters WHERE cluster_id IN ({ph})", frm)
        conn.execute("INSERT OR IGNORE INTO clusters (cluster_id, name) VALUES (?, NULL)",
                     (into,))
        conn.commit()
    return {"ok": True, "moved": moved}


def _face_key(conn, face_id):
    r = conn.execute(
        """SELECT i.content_hash AS h, f.x1, f.y1, f.x2, f.y2, f.image_id AS img
           FROM faces f JOIN images i ON i.id = f.image_id WHERE f.id=?""",
        (face_id,)).fetchone()
    return r


@app.post("/api/faces/{face_id}/assign")
def assign_face(face_id: int, payload: dict = Body(...)):
    """Move one face box to a different person. Either pass an existing
    `cluster_id`, or `new_person: true` (with optional `name`) to spin up a
    fresh identity. Recorded as a persistent override so it survives re-ingest."""
    with db() as conn:
        r = _face_key(conn, face_id)
        if not r:
            raise HTTPException(404, "face not found")
        if payload.get("new_person"):
            target = photodb.next_manual_cluster_id(conn)
            conn.execute("INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
                         (target, payload.get("name") or None))
        else:
            cid = payload.get("cluster_id")
            if cid is None:
                raise HTTPException(400, "cluster_id or new_person required")
            target = int(cid)
            conn.execute("INSERT OR IGNORE INTO clusters (cluster_id, name) VALUES (?, NULL)",
                         (target,))
        conn.execute("UPDATE faces SET cluster_id=? WHERE id=?", (target, face_id))
        _record_override(conn, r["h"], bbox_key(r["x1"], r["y1"], r["x2"], r["y2"]),
                         "assign", target)
        conn.commit()
    return {"ok": True, "cluster_id": target}


@app.delete("/api/faces/{face_id}")
def delete_face(face_id: int):
    """Drop a false-positive face box, decrement the image's face count, and
    record a persistent override so it stays deleted across re-ingest."""
    with db() as conn:
        r = _face_key(conn, face_id)
        if not r:
            raise HTTPException(404, "face not found")
        conn.execute("DELETE FROM faces WHERE id=?", (face_id,))
        conn.execute("UPDATE images SET n_faces = MAX(0, n_faces - 1) WHERE id=?",
                     (r["img"],))
        _record_override(conn, r["h"], bbox_key(r["x1"], r["y1"], r["x2"], r["y2"]),
                         "delete")
        conn.commit()
    return {"ok": True}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/api/export")
def export_decisions():
    with db() as conn:
        rows = conn.execute(
            f"""SELECT i.path, i.filename, i.combined, d.decision
               FROM images i LEFT JOIN {DEC_ON}""").fetchall()
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
            f"""SELECT i.path FROM images i
               JOIN {DEC_ON}
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
            f"""SELECT i.id, i.path FROM images i
               JOIN {DEC_ON}
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


# ── Web-driven re-analysis (run photo_audit.py + build_db.py, stream output) ──
# A single job at a time shells out to the existing scripts and streams their
# stdout/stderr live (including tqdm carriage-return progress) to the browser
# via Server-Sent Events. The launcher is *constrained*: the server builds the
# argv from a fixed set of known flags — only the target folder is free text,
# and it's validated to be an existing directory. No raw commands.

AUDIT_SCRIPT = Path(__file__).resolve().parent.parent / "photo_audit.py"
BUILD_SCRIPT = Path(__file__).resolve().parent / "build_db.py"
REPO_ROOT    = AUDIT_SCRIPT.parent

_BACKENDS = {"para", "clip-iqa", "both"}


class AnalysisJob:
    """Runs an ordered list of (label, argv) steps in a background thread,
    capturing output as committed lines (split on \\n) plus a single live
    'partial' line that tqdm's \\r progress updates overwrite in place."""

    def __init__(self, steps: list[tuple[str, list[str]]], cwd: Path):
        self.steps = steps
        self.cwd = str(cwd)
        self.lines: list[str] = []      # committed (newline-terminated) lines
        self.partial: str = ""          # current in-progress line (\r updates)
        self.state = "running"          # running | done | failed | cancelled
        self.exit_code: int | None = None
        self.started = time.time()
        self.ended: float | None = None
        self._proc: subprocess.Popen | None = None
        self._cancel = False
        self._cond = threading.Condition()

    # ── output buffer (thread-safe) ──
    def _commit(self, text: str):
        with self._cond:
            self.lines.append(text)
            self.partial = ""
            self._cond.notify_all()

    def _set_partial(self, text: str):
        with self._cond:
            self.partial = text
            self._cond.notify_all()

    def _finish(self, state: str, code: int | None):
        with self._cond:
            self.state = state
            self.exit_code = code
            self.ended = time.time()
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return {
                "state": self.state, "exit_code": self.exit_code,
                "started": self.started, "ended": self.ended,
                "commands": [" ".join(_shell_quote(a) for a in argv)
                             for _, argv in self.steps],
            }

    def cancel(self):
        self._cancel = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def run(self):
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        for label, argv in self.steps:
            if self._cancel:
                self._finish("cancelled", None)
                return
            self._commit(f"$ {' '.join(_shell_quote(a) for a in argv)}")
            try:
                self._proc = subprocess.Popen(
                    argv, cwd=self.cwd, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except Exception as e:
                self._commit(f"[error] failed to launch {label}: {e}")
                self._finish("failed", -1)
                return
            self._pump(self._proc.stdout)
            code = self._proc.wait()
            self._commit(f"[{label} exited with code {code}]")
            if self._cancel:
                self._finish("cancelled", code)
                return
            if code != 0:
                self._finish("failed", code)
                return
        self._finish("done", 0)

    def _pump(self, stream):
        """Read raw bytes, splitting into lines. A lone \\r is a tqdm progress
        update (overwrites the live 'partial' line); \\r\\n and \\n commit a line.
        Distinguishing the two matters on Windows, where child stdout turns every
        '\\n' print into '\\r\\n' — naive \\r handling would blank every line."""
        buf = bytearray()
        pending_cr = False
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            for b in chunk:
                if pending_cr:
                    pending_cr = False
                    if b == 0x0A:            # \r\n → one committed line
                        self._commit(buf.decode("utf-8", "replace"))
                        buf = bytearray()
                        continue
                    # lone \r → progress update; show buf, then handle b below
                    self._set_partial(buf.decode("utf-8", "replace"))
                    buf = bytearray()
                if b == 0x0D:                # \r → defer (could be \r\n)
                    pending_cr = True
                elif b == 0x0A:              # bare \n → commit
                    self._commit(buf.decode("utf-8", "replace"))
                    buf = bytearray()
                else:
                    buf.append(b)
        if pending_cr or buf:
            self._commit(buf.decode("utf-8", "replace"))


def _shell_quote(s: str) -> str:
    s = str(s)
    return f'"{s}"' if (" " in s or "!" in s) else s


CURRENT_JOB: AnalysisJob | None = None


def _build_analyze_steps(payload: dict) -> list[tuple[str, list[str]]]:
    """Translate the UI payload into argv for photo_audit + build_db. Only
    known flags are emitted; the folder is validated. Raises HTTPException."""
    folder = (payload.get("folder") or "").strip()
    if not folder:
        with db() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='folder'").fetchone()
            folder = row["value"] if row else ""
    fpath = Path(folder)
    if not folder or not fpath.is_dir():
        raise HTTPException(400, f"folder not found: {folder!r}")

    report_path = DB_PATH.parent / "audit_report.json"
    py = sys.executable

    audit = [py, str(AUDIT_SCRIPT), str(fpath), "--out", str(report_path)]
    if payload.get("recurse"):
        audit.append("--recurse")
    if payload.get("no_clip"):
        audit.append("--no-clip")
    else:
        backend = payload.get("backend", "para")
        if backend not in _BACKENDS:
            raise HTTPException(400, f"bad backend: {backend!r}")
        audit += ["--backend", backend]
    if payload.get("caption"):
        audit.append("--caption")
    if payload.get("faces"):
        audit.append("--faces")
        if payload.get("face_expr"):
            audit.append("--face-expr")
    if payload.get("no_cache"):
        audit.append("--no-cache")

    # Numeric knobs — parsed/clamped, never passed through verbatim.
    def _num(key, flag, lo, hi, cast):
        v = payload.get(key)
        if v is None or v == "":
            return
        try:
            v = cast(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"bad {key}: {v!r}")
        audit.extend([flag, str(max(lo, min(hi, v)))])

    _num("dup_threshold", "--dup-threshold", 0, 64, int)
    _num("face_min_rel", "--face-min-rel", 0.0, 1.0, float)
    _num("face_eps", "--face-eps", 0.05, 1.5, float)

    build = [py, str(BUILD_SCRIPT), str(report_path),
             "--db", str(DB_PATH), "--thumbs", str(THUMB_DIR)]

    # scope=both (confirmed): audit then rebuild the DB the server is serving.
    return [("photo_audit", audit), ("build_db", build)]


@app.post("/api/analyze")
def start_analyze(payload: dict = Body(...)):
    global CURRENT_JOB
    if CURRENT_JOB and CURRENT_JOB.state == "running":
        raise HTTPException(409, "an analysis is already running")
    steps = _build_analyze_steps(payload)
    CURRENT_JOB = AnalysisJob(steps, cwd=REPO_ROOT)
    threading.Thread(target=CURRENT_JOB.run, daemon=True).start()
    return {"ok": True, **CURRENT_JOB.snapshot()}


@app.get("/api/analyze/status")
def analyze_status():
    if not CURRENT_JOB:
        return {"state": "idle", "commands": []}
    return CURRENT_JOB.snapshot()


@app.post("/api/analyze/cancel")
def analyze_cancel():
    if not CURRENT_JOB or CURRENT_JOB.state != "running":
        raise HTTPException(409, "no analysis running")
    CURRENT_JOB.cancel()
    return {"ok": True}


@app.get("/api/analyze/stream")
async def analyze_stream():
    """SSE stream of the current job's output. Replays from the start so a
    reconnect/late join gets the full log, then tails live."""
    job = CURRENT_JOB

    def _sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def gen():
        if not job:
            yield _sse("end", {"state": "idle"})
            return
        cursor = 0
        last_partial = None
        while True:
            with job._cond:
                new = job.lines[cursor:]
                cursor = len(job.lines)
                partial = job.partial
                state = job.state
                code = job.exit_code
            for ln in new:
                yield _sse("line", ln)
            if partial != last_partial:
                last_partial = partial
                yield _sse("partial", partial)
            if state != "running" and cursor >= len(job.lines):
                yield _sse("end", {"state": state, "exit_code": code})
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


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
    ap.add_argument("--photo-root", action="append", default=None, metavar="DIR",
                    help="Directory the file-reveal feature may open into (repeatable). "
                         "Defaults to the library folder stored in the DB.")
    args = ap.parse_args()

    DB_PATH = Path(args.db)
    if not DB_PATH.exists():
        print(f"Error: DB {DB_PATH} not found — run build_db.py first"); sys.exit(1)
    THUMB_DIR = Path(args.thumbs) if args.thumbs else DB_PATH.parent / ".thumbs"
    FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

    print(f"DB:     {DB_PATH}")
    print(f"Thumbs: {THUMB_DIR}")
    _ensure_schema()
    _init_photo_roots(args.photo_root)
    _mount_frontend()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
