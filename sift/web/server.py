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
import string
import shutil
import asyncio
import sqlite3
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from sift.web import library_ops, photodb
from sift.web.photodb import bbox_key, largest_face_aggregate, MANUAL_CLUSTER_BASE

from sift.web import tasks
from sift.web.queries import (DEC_ON, TRASH_ON, SORT_COLUMNS, histogram, image_where,
                              rows_to_items, grouped_page)
from sift.web.routes.trash import create_router as create_trash_router
from sift.web.schemas import (
    MetaResponse, ImagesResponse, GroupsResponse, ScenesResponse,
    LocationsResponse, RootsResponse, FsCompleteResponse, OkResponse,
    MergeResponse, AssignFaceResponse, AutocullResponse, AnalyzeStatus,
    TaskStartRequest, TaskSnapshot, TaskListResponse)

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
_RUNTIME_INITIALIZED = False
_FRONTEND_MOUNTED = False

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _startup_from_reload_env()
    yield


app = FastAPI(title="Photo Audit", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # local single-user tool
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def db():
    conn = photodb.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema():
    """Bring the served DB fully up to schema, creating it from scratch if needed.

    Creates the base tables (so a cold/empty photos.db is usable — the whole
    point of starting the server before anything is analyzed), then runs the
    shared migration authority so an older DB also works without a rebuild. Every
    statement is idempotent (CREATE/columns guarded), so this is safe on first
    boot, on an existing library, and on every restart alike."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        photodb.create_base_schema(conn)
        try:
            conn.executescript(photodb.FTS_SCHEMA)
        except sqlite3.OperationalError:
            pass                                   # SQLite built without FTS5
        photodb.ensure_schema(conn)
        conn.commit()


def _configure_tasks():
    """Keep the task runner pointed at the DB/thumb paths currently being served.
    Tests patch these globals directly, so routes call this defensively instead
    of assuming `main()` was the only initializer."""
    tasks.MANAGER.configure(db_path=DB_PATH, thumb_dir=THUMB_DIR, db_factory=db)


app.include_router(create_trash_router(db))


# ── Metadata + facets ─────────────────────────────────────────────────────────

@app.get("/api/meta", response_model=MetaResponse)
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
            "scene_groups": int(meta.get("scene_groups", 0)),
        }

        # Fixed-domain [0,1] histograms so the slider track shows the value
        # distribution behind each range control.
        histograms = {
            "combined":  histogram(conn, "combined"),
            "sharpness": histogram(conn, "sharpness"),
            "aesthetic": histogram(conn, "COALESCE(para_aesthetic, clip_iqa)"),
        }
        if has_portrait_col:
            histograms["portrait"] = histogram(conn, "portrait")

        # Folder facet: every directory that directly holds images, with its
        # direct image count. The sidebar reconstructs the tree (compressing
        # single-child chains) and sums subtree totals client-side. dirname is
        # done in Python because SQLite has no portable path-dirname function.
        folder_counts: dict[str, int] = {}
        for (p,) in conn.execute("SELECT path FROM images"):
            d = os.path.dirname(p)
            folder_counts[d] = folder_counts.get(d, 0) + 1
        folders = [{"path": k, "count": v} for k, v in sorted(folder_counts.items())]

    return {
        "meta": meta,
        "clusters": clusters,
        "tags": tags,
        "folders": folders,
        "ranges": dict(rng),
        "counts": counts,
        "histograms": histograms,
        "has_para": rng["amin"] is not None,
        "has_portrait": n_portrait > 0,
        "photo_roots": PHOTO_ROOT_DIRS,
    }


# ── Faceted image query ───────────────────────────────────────────────────────

@app.get("/api/images", response_model=ImagesResponse)
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
    folder:  str | None = None,     # folder-hierarchy prefix filter
    folder_recursive: bool = True,  # include photos in subfolders of `folder`
    dup_mode: str = "all",          # all | groups-only | hide-dups | no-groups
    decision: str = "all",          # all | keep | del | unmarked
    q:       str | None = None,     # caption text search
):
    with db() as conn:
        where, params = image_where(
            conn, score_min=score_min, score_max=score_max,
            sharp_min=sharp_min, sharp_max=sharp_max,
            aes_min=aes_min, aes_max=aes_max,
            portrait_min=portrait_min, portrait_max=portrait_max,
            tags=tags, people=people, q=q, decision=decision,
            folder=folder, folder_recursive=folder_recursive)

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
            LEFT JOIN {TRASH_ON}
            WHERE {where_sql}
        """

        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

        rows = conn.execute(
            f"""SELECT i.*, d.decision, tm.state AS trash_state,
                       tm.from_path AS original_path, tm.trashed_at {base}
                ORDER BY {sort_col} {sort_dir}, i.id ASC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = rows_to_items(conn, rows)

    return {"total": total, "offset": offset, "limit": limit, "items": items}


# ── Duplicate groups ──────────────────────────────────────────────────────────

@app.get("/api/groups", response_model=GroupsResponse)
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
    folder:  str | None = None,
    folder_recursive: bool = True,
    decision: str = "all",
    q:       str | None = None,
):
    """Return duplicate groups (paginated by group), each with all of its
    member photos ordered best-first. The same filters as /api/images apply:
    a group is included when at least one of its members matches, but every
    member is returned so the full duplicate set stays reviewable."""
    order_sql = "c DESC, dup_group ASC" if order == "size" else "dup_group ASC"
    with db() as conn:
        where, params = image_where(
            conn, score_min=score_min, score_max=score_max,
            sharp_min=sharp_min, sharp_max=sharp_max,
            aes_min=aes_min, aes_max=aes_max,
            portrait_min=portrait_min, portrait_max=portrait_max,
            tags=tags, people=people, q=q, decision=decision,
            folder=folder, folder_recursive=folder_recursive)
        total, page = grouped_page(conn, "dup_group", where, params,
                                   order_sql, offset, limit)
        groups = [
            {"dup_group": gr["dup_group"], "count": len(items),
             "match_count": sum(it["matches"] for it in items), "items": items}
            for gr, items in page
        ]
    return {"total": total, "offset": offset, "limit": limit, "groups": groups}


# ── Scenes (rough hierarchy: scene -> nested near-dup sets) ────────────────────

@app.get("/api/scenes", response_model=ScenesResponse)
def get_scenes(
    offset: int = 0,
    limit:  int = Query(40, le=200),
    order:  str = "time",          # time | size | id
    score_min: float = 0.0, score_max: float = 1.0,
    sharp_min: float = 0.0, sharp_max: float = 1.0,
    aes_min:   float = 0.0, aes_max:   float = 1.0,
    portrait_min: float = 0.0, portrait_max: float = 1.0,
    tags:    str | None = None,
    people:  str | None = None,
    folder:  str | None = None,
    folder_recursive: bool = True,
    decision: str = "all",
    q:       str | None = None,
):
    """Return rough scene groups (paginated by scene), each with all member
    photos ordered best-first. Every item carries its `dup_group`, so the client
    nests near-duplicate sub-piles with a cheap groupBy(dup_group). Same filter
    semantics as /api/groups: a scene is included when at least one member
    matches, but every member is returned so the full scene stays reviewable."""
    if order == "size":
        order_sql = "c DESC, scene_group ASC"
    elif order == "id":
        order_sql = "scene_group ASC"
    else:  # time: oldest scene first (by earliest capture)
        order_sql = "tmin ASC, scene_group ASC"

    with db() as conn:
        where, params = image_where(
            conn, score_min=score_min, score_max=score_max,
            sharp_min=sharp_min, sharp_max=sharp_max,
            aes_min=aes_min, aes_max=aes_max,
            portrait_min=portrait_min, portrait_max=portrait_max,
            tags=tags, people=people, q=q, decision=decision,
            folder=folder, folder_recursive=folder_recursive)
        total, page = grouped_page(
            conn, "scene_group", where, params, order_sql, offset, limit,
            agg_extra=", MIN(capture_time) tmin, MAX(capture_time) tmax")
        scenes = [
            {"scene_group": gr["scene_group"], "count": len(items),
             "match_count": sum(it["matches"] for it in items),
             # how many distinct near-dup sets this scene contains
             "dup_sets": len({it["dup_group"] for it in items
                              if it["dup_group"] is not None}),
             "time_start": gr["tmin"], "time_end": gr["tmax"], "items": items}
            for gr, items in page
        ]
    return {"total": total, "offset": offset, "limit": limit, "scenes": scenes}


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


@app.get("/api/images/{image_id}/locations", response_model=LocationsResponse)
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


@app.post("/api/reveal", response_model=OkResponse)
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


@app.get("/api/settings/roots", response_model=RootsResponse)
def get_roots():
    return _roots_payload()


@app.post("/api/settings/roots", response_model=RootsResponse)
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


@app.delete("/api/settings/roots", response_model=RootsResponse)
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


@app.get("/api/fs/complete", response_model=FsCompleteResponse)
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

@app.post("/api/decisions", response_model=OkResponse)
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

@app.post("/api/clusters", response_model=OkResponse)
def rename_cluster(payload: dict = Body(...)):
    cid = payload.get("cluster_id")
    name = payload.get("name")
    if cid is None:
        raise HTTPException(400, "cluster_id required")
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
                     (int(cid), name or None))
        # Anchor the name to this cluster's faces so it survives re-clustering.
        photodb.set_cluster_name_anchors(conn, int(cid), name or None)
        conn.commit()
    return {"ok": True}


def _record_override(conn, hash_, bbox, action, cluster_id=None):
    if not hash_:
        return
    photodb.ensure_overrides(conn)
    conn.execute(
        "INSERT OR REPLACE INTO face_overrides (hash, bbox, action, cluster_id) "
        "VALUES (?,?,?,?)", (hash_, bbox, action, cluster_id))


def _reanchor_cluster(conn, cid: int) -> None:
    """Re-pin a cluster's current name across all of its faces. Called after
    membership changes (merge/assign) so the name follows the cluster on a
    rebuild and stale source-cluster anchors on moved faces are cleared."""
    row = conn.execute("SELECT name FROM clusters WHERE cluster_id=?", (cid,)).fetchone()
    photodb.set_cluster_name_anchors(conn, cid, row[0] if row else None)


def _reaggregate_faces(conn, image_id) -> None:
    """Recompute an image's face count + portrait fields from its current faces.
    Mirrors build_db's override-replay recompute via the same
    largest_face_aggregate, so an API face edit and a fresh ingest can't disagree
    on n_faces/face_sharp/face_expr/portrait."""
    rows = conn.execute(
        "SELECT x1, y1, x2, y2, sharp, expr FROM faces WHERE image_id=?",
        (image_id,)).fetchall()
    n, fs, fe, portrait = largest_face_aggregate(rows)
    conn.execute(
        "UPDATE images SET n_faces=?, face_sharp=?, face_expr=?, portrait=? WHERE id=?",
        (n, fs, fe, portrait, image_id))


@app.post("/api/clusters/merge", response_model=MergeResponse)
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
        # Re-anchor the destination to its current name across all of its faces
        # (now including the moved ones); this also clears the moved faces' old
        # source-cluster anchors so a stale name can't resurface on rebuild.
        _reanchor_cluster(conn, into)
        conn.commit()
    return {"ok": True, "moved": moved}


def _face_key(conn, face_id):
    r = conn.execute(
        """SELECT i.content_hash AS h, f.x1, f.y1, f.x2, f.y2, f.image_id AS img
           FROM faces f JOIN images i ON i.id = f.image_id WHERE f.id=?""",
        (face_id,)).fetchone()
    return r


@app.post("/api/faces/{face_id}/assign", response_model=AssignFaceResponse)
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
        # Keep the target's name anchors in step with its new membership.
        _reanchor_cluster(conn, target)
        conn.commit()
    return {"ok": True, "cluster_id": target}


@app.delete("/api/faces/{face_id}", response_model=OkResponse)
def delete_face(face_id: int):
    """Drop a false-positive face box, decrement the image's face count, and
    record a persistent override so it stays deleted across re-ingest."""
    with db() as conn:
        r = _face_key(conn, face_id)
        if not r:
            raise HTTPException(404, "face not found")
        conn.execute("DELETE FROM faces WHERE id=?", (face_id,))
        _reaggregate_faces(conn, r["img"])
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

@app.post("/api/groups/autocull", response_model=AutocullResponse)
def autocull_groups():
    """For every duplicate group, mark the best-scoring photo 'keep' and the
    rest 'del'. Overwrites existing marks within groups. Marks only — files
    are not touched until /api/apply."""
    with db() as conn:
        return library_ops.autocull_duplicates(conn)


# ── Generic web tasks (analyze, index, apply, undo, autocull) ─────────────────

CURRENT_ANALYZE_TASK_ID: str | None = None


def _sse(event: str, data, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/tasks", response_model=TaskSnapshot)
def start_task(payload: TaskStartRequest):
    _configure_tasks()
    return tasks.MANAGER.start(payload.type, payload.params)


@app.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(limit: int = Query(20, le=100)):
    _configure_tasks()
    return tasks.MANAGER.list_recent(limit)


@app.get("/api/tasks/{task_id}", response_model=TaskSnapshot)
def task_status(task_id: str):
    _configure_tasks()
    return tasks.MANAGER.snapshot(task_id)


@app.post("/api/tasks/{task_id}/cancel", response_model=TaskSnapshot)
def task_cancel(task_id: str):
    _configure_tasks()
    return tasks.MANAGER.cancel(task_id)


@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str, after: int = 0):
    """SSE replay + tail for any persisted task."""
    _configure_tasks()
    # Validate before returning the StreamingResponse so bad ids get a normal 404.
    first = tasks.MANAGER.snapshot(task_id)

    async def gen():
        yield _sse("snapshot", first)
        seq = max(0, after)
        while True:
            events = tasks.MANAGER.events_after(task_id, seq)
            for ev in events:
                seq = ev["seq"]
                et = ev["event_type"]
                if et == "end":
                    yield _sse("end", ev["payload"], seq)
                    return
                yield _sse(et, ev["payload"], seq)
            snap = tasks.MANAGER.snapshot(task_id)
            if snap["state"] != "running" and not events:
                yield _sse("end", {"state": snap["state"], "error": snap.get("error")})
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# Backward-compatible analyze endpoints. The new UI should use /api/tasks; these
# keep older clients working while the web app migrates off the bespoke route set.
@app.post("/api/analyze", response_model=AnalyzeStatus, response_model_exclude_unset=True)
def start_analyze(payload: dict = Body(...)):
    global CURRENT_ANALYZE_TASK_ID
    _configure_tasks()
    snap = tasks.MANAGER.start("analyze_library", payload)
    CURRENT_ANALYZE_TASK_ID = snap["id"]
    return {"ok": True, "state": snap["state"], "started": snap["started"],
            "ended": snap["ended"], "commands": snap["commands"]}


@app.get("/api/analyze/status", response_model=AnalyzeStatus, response_model_exclude_unset=True)
def analyze_status():
    _configure_tasks()
    if not CURRENT_ANALYZE_TASK_ID:
        return {"state": "idle", "commands": []}
    try:
        snap = tasks.MANAGER.snapshot(CURRENT_ANALYZE_TASK_ID)
    except HTTPException:
        return {"state": "idle", "commands": []}
    return {"state": snap["state"], "started": snap["started"], "ended": snap["ended"],
            "commands": snap["commands"]}


@app.post("/api/analyze/cancel", response_model=OkResponse)
def analyze_cancel():
    _configure_tasks()
    if not CURRENT_ANALYZE_TASK_ID:
        raise HTTPException(409, "no analysis running")
    tasks.MANAGER.cancel(CURRENT_ANALYZE_TASK_ID)
    return {"ok": True}


@app.get("/api/analyze/stream")
async def analyze_stream():
    _configure_tasks()
    task_id = CURRENT_ANALYZE_TASK_ID

    async def gen():
        if not task_id:
            yield _sse("end", {"state": "idle"})
            return
        seq = 0
        while True:
            events = tasks.MANAGER.events_after(task_id, seq)
            for ev in events:
                seq = ev["seq"]
                et = ev["event_type"]
                if et == "command":
                    continue
                if et == "end":
                    yield _sse("end", ev["payload"])
                    return
                yield _sse(et, ev["payload"])
            snap = tasks.MANAGER.snapshot(task_id)
            if snap["state"] != "running" and not events:
                yield _sse("end", {"state": snap["state"], "error": snap.get("error")})
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── Serve built frontend (production) ─────────────────────────────────────────
# Mounted last so it doesn't shadow /api routes. Only if a build exists.

def _mount_frontend():
    global _FRONTEND_MOUNTED
    if _FRONTEND_MOUNTED:
        return
    if FRONTEND_DIST and FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
        _FRONTEND_MOUNTED = True
        print(f"  Serving frontend build from {FRONTEND_DIST}")
    else:
        print("  No frontend build found — run Vite dev server (npm run dev) on :5173")


def _resolve_frontend_dist(override: str | None) -> Path:
    """Locate the built React app. CLI flag wins, then $SIFT_FRONTEND_DIST, then
    the repo's frontend/dist (works for editable installs: this file lives at
    sift/web/server.py, so the repo root is two parents up)."""
    if override:
        return Path(override)
    env = os.environ.get("SIFT_FRONTEND_DIST")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _runtime_env_photo_roots() -> list[str] | None:
    raw = os.environ.get("SIFT_PHOTO_ROOTS")
    if not raw:
        return None
    return json.loads(raw)


def _init_runtime(db_path: Path, thumb_dir: Path, frontend_dist: Path | None,
                  photo_roots: list[str] | None) -> None:
    global DB_PATH, THUMB_DIR, FRONTEND_DIST, _RUNTIME_INITIALIZED
    DB_PATH = db_path
    THUMB_DIR = thumb_dir
    FRONTEND_DIST = frontend_dist
    if _RUNTIME_INITIALIZED:
        return
    print(f"DB:     {DB_PATH}")
    print(f"Thumbs: {THUMB_DIR}")
    from sift.web import backup
    if not backup.quick_check(DB_PATH):
        print(f"WARNING: integrity check FAILED on {DB_PATH} — the library may be "
              f"corrupt. Restore a snapshot with `sift backup restore` "
              f"(see `sift backup list`).")
    _ensure_schema()
    # Throttled safety snapshot: guarantees a recent copy of accumulating
    # decisions/names exists, without spamming on reload restarts.
    try:
        snap = backup.snapshot_if_stale(DB_PATH)
        if snap:
            print(f"Backup: {snap}")
    except Exception as e:                          # never block startup on this
        print(f"Backup: skipped ({e})")
    _configure_tasks()
    tasks.MANAGER.abandon_running()
    _init_photo_roots(photo_roots)
    _mount_frontend()
    _RUNTIME_INITIALIZED = True


def _startup_from_reload_env() -> None:
    db_env = os.environ.get("SIFT_DB_PATH")
    if not db_env:
        return
    db_path = Path(db_env)
    thumb_dir = Path(os.environ["SIFT_THUMB_DIR"])
    frontend_env = os.environ.get("SIFT_RUNTIME_FRONTEND_DIST")
    frontend_dist = Path(frontend_env) if frontend_env else None
    _init_runtime(db_path, thumb_dir, frontend_dist, _runtime_env_photo_roots())


def _default_db_path() -> Path:
    """Where the library lives when --db is omitted: a per-user app-data dir, so
    `sift serve` works cold with zero arguments and the first folder can be
    analyzed entirely from the web UI.

    The DB, its thumbnail cache (.thumbs) and the analyze report (audit_report.json)
    all colocate here, deliberately keeping the user's photo folders pristine —
    no photos.db / .thumbs / audit_report.json scattered among the originals.
    (Rejected/trashed files still move next to their originals, not here.)"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "PhotoOrganizer" / "photos.db"


def main() -> None:
    global DB_PATH, THUMB_DIR, FRONTEND_DIST
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None,
                    help="SQLite library path. Defaults to a per-user app-data "
                         "location, created empty on first run so you can analyze "
                         "your first folder entirely from the web UI.")
    ap.add_argument("--thumbs", default=None)
    ap.add_argument("--host",   default="127.0.0.1")
    ap.add_argument("--port",   type=int, default=8000)
    ap.add_argument("--photo-root", action="append", default=None, metavar="DIR",
                    help="Directory the file-reveal feature may open into (repeatable). "
                         "Defaults to the library folder stored in the DB.")
    ap.add_argument("--frontend-dist", default=None, metavar="DIR",
                    help="Built React app to serve (default: $SIFT_FRONTEND_DIST, "
                         "else the repo's frontend/dist for editable installs).")
    ap.add_argument("--reload", action="store_true",
                    help="Restart the API server automatically when Python files change. "
                         "Use with the Vite dev server for near-immediate web/API updates.")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        print(f"No library at {db_path} — starting empty. Open the web UI and use "
              f"'Library operations' to analyze your first folder.")
    thumb_dir = Path(args.thumbs) if args.thumbs else db_path.parent / ".thumbs"
    frontend_dist = _resolve_frontend_dist(args.frontend_dist)

    import uvicorn
    if args.reload:
        os.environ["SIFT_DB_PATH"] = str(db_path)
        os.environ["SIFT_THUMB_DIR"] = str(thumb_dir)
        os.environ["SIFT_RUNTIME_FRONTEND_DIST"] = str(frontend_dist)
        if args.photo_root:
            os.environ["SIFT_PHOTO_ROOTS"] = json.dumps(args.photo_root)
        else:
            os.environ.pop("SIFT_PHOTO_ROOTS", None)
        repo_root = Path(__file__).resolve().parents[2]
        uvicorn.run("sift.web.server:app", host=args.host, port=args.port,
                    reload=True, reload_dirs=[str(repo_root / "sift")])
    else:
        _init_runtime(db_path, thumb_dir, frontend_dist, args.photo_root)
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
