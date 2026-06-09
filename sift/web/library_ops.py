"""Shared write-side operations for the photo library.

Routes and background tasks both need to move files, update SQLite, and report
counts. Keeping those rules here avoids the sync API and task runner quietly
becoming two different products.
"""
from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sift.web import photodb
from sift.web.queries import DEC_ON

ProgressFn = Callable[[int, int, str], None]
LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


class OperationCancelled(Exception):
    """Raised by long-running operations when their caller requested cancel."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _library_root(conn) -> Path:
    row = conn.execute("SELECT value FROM meta WHERE key='folder'").fetchone()
    if not row:
        raise RuntimeError("library folder unknown")
    return Path(row["value"])


def trash_dir(conn) -> Path:
    return _library_root(conn) / "_trash"


def unique_dest(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, suf = Path(name).stem, Path(name).suffix
    k = 1
    while (dest_dir / f"{stem}_{k}{suf}").exists():
        k += 1
    return dest_dir / f"{stem}_{k}{suf}"


def ensure_library_schema(conn) -> None:
    photodb.ensure_schema(conn)


def trash_counts(conn) -> dict[str, int]:
    ensure_library_schema(conn)
    trash = trash_dir(conn)
    trash_str = str(trash)
    pending = 0
    for row in conn.execute(
        f"""SELECT i.path FROM images i
           JOIN {DEC_ON}
           LEFT JOIN trash_moves tm ON tm.image_id=i.id AND tm.state='trashed'
           WHERE d.decision='del' AND tm.id IS NULL"""
    ):
        if not str(row["path"]).startswith(trash_str):
            pending += 1
    trashed = conn.execute(
        "SELECT COUNT(*) FROM trash_moves WHERE state='trashed'").fetchone()[0]
    emptied = conn.execute(
        "SELECT COUNT(*) FROM trash_moves WHERE state='emptied'").fetchone()[0]
    return {"pending": pending, "trashed": trashed, "emptied": emptied}


def list_trash(conn) -> list[dict[str, Any]]:
    ensure_library_schema(conn)
    rows = conn.execute(
        """SELECT id, image_id, hash, from_path, trash_path, state, trashed_at
           FROM trash_moves WHERE state='trashed' ORDER BY trashed_at DESC, id DESC"""
    ).fetchall()
    return [{
        "id": row["id"], "image_id": row["image_id"],
        "filename": Path(row["trash_path"]).name,
        "hash": row["hash"], "original_path": row["from_path"],
        "trash_path": row["trash_path"], "state": row["state"],
        "trashed_at": row["trashed_at"],
    } for row in rows]


DEFAULT_SCENE_GAP = 120.0


def current_scene_gap(conn) -> float:
    """The last scene-granularity gap the user chose, or the default."""
    # Positional indexing: callers include build_db, whose connection has no
    # Row factory.
    row = conn.execute("SELECT value FROM meta WHERE key='scene_gap'").fetchone()
    try:
        return float(row[0]) if row and row[0] is not None else DEFAULT_SCENE_GAP
    except (TypeError, ValueError):
        return DEFAULT_SCENE_GAP


def scene_merge_path_groups(conn) -> list[list[str]]:
    """Manual scene-merge pins resolved to current image paths, one list per
    merge group (groups with fewer than two present images are dropped). A hash
    can map to several paths (byte-identical copies); all are included so the
    merge survives reorganisation."""
    photodb.ensure_scene_merges(conn)
    paths_by_hash: dict = defaultdict(list)
    for r in conn.execute(
        "SELECT content_hash, path FROM images WHERE content_hash IS NOT NULL"):
        paths_by_hash[r[0]].append(r[1])
    groups: dict = defaultdict(list)
    for r in conn.execute("SELECT group_id, hash FROM scene_merges"):
        groups[r[0]].extend(paths_by_hash.get(r[1], []))
    return [g for g in groups.values() if len(g) > 1]


def recompute_scenes(conn, gap_seconds: float) -> dict[str, Any]:
    """Re-segment scenes purely by capture-time gap, no visual splitting.

    A new scene starts wherever two consecutive shots are more than
    ``gap_seconds`` apart; everything tighter than that stays together. This is
    the "scene granularity" knob: there is no objectively correct gap (the gap
    distribution is a smooth continuum, not bimodal), so the value is a user
    choice rather than something to detect. Near-duplicate groups are coarsened
    back inside a single scene afterwards, so a dup set can never straddle a
    boundary. Only multi-member scenes get an id; lone frames stay scene-less.

    Untimed photos (no EXIF capture time) can't be ordered and are left out of
    scenes. Writes the new ``scene_group`` per image and records the chosen gap
    (and resulting scene count) in ``meta``. Returns {scene_groups, gap}."""
    from sift.audit.grouping import group_scenes, coarsen_scenes_for_dups

    ensure_library_schema(conn)
    rows = conn.execute(
        "SELECT id, path, capture_time, dup_group FROM images").fetchall()
    id_by_path = {r["path"]: r["id"] for r in rows}
    timed = [r for r in rows if r["capture_time"] is not None]
    times = {r["path"]: r["capture_time"] for r in timed}
    timed_paths = [r["path"] for r in timed]

    # Pure time: with no embeddings/hashes every pair reads "not visually
    # similar", so setting small_gap == big_gap == gap splits exactly where the
    # capture-time gap exceeds the threshold.
    scene_of, _ = group_scenes(
        timed_paths, times, small_gap=gap_seconds, big_gap=gap_seconds)

    dgmap: dict = defaultdict(list)
    for r in rows:
        if r["dup_group"] is not None:
            dgmap[r["dup_group"]].append(r["path"])
    dup_groups = [v for v in dgmap.values() if len(v) > 1]

    # Manual merges are pinned by content hash and re-applied on every recut, so
    # a user-declared "one scene" survives any slider move.
    merge_groups = scene_merge_path_groups(conn)

    scene_assign, count = coarsen_scenes_for_dups(
        timed_paths, scene_of, dup_groups, times=times,
        extra_groups=merge_groups)

    conn.execute("UPDATE images SET scene_group = NULL")
    for path, sid in scene_assign.items():
        if sid is not None:
            conn.execute("UPDATE images SET scene_group=? WHERE id=?",
                         (sid, id_by_path[path]))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('scene_gap', ?)",
                 (str(gap_seconds),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('scene_groups', ?)",
                 (str(count),))
    conn.commit()
    return {"scene_groups": count, "gap": gap_seconds}


def _hashes_in_scenes(conn, scene_groups: list[int]) -> set[str]:
    if not scene_groups:
        return set()
    ph = ",".join("?" * len(scene_groups))
    return {r[0] for r in conn.execute(
        f"""SELECT DISTINCT content_hash FROM images
            WHERE scene_group IN ({ph}) AND content_hash IS NOT NULL""",
        scene_groups)}


def merge_scenes(conn, scene_groups: list[int]) -> dict[str, Any]:
    """Pin the given scenes into one: every image hash they contain is recorded
    in a single scene_merges group (folding in any pre-existing overlapping
    pins), then scenes are recomputed at the current gap so the merge takes hold
    immediately and persists across future recuts/rebuilds."""
    photodb.ensure_scene_merges(conn)
    sgs = sorted({int(s) for s in scene_groups})
    hashes = _hashes_in_scenes(conn, sgs)
    if len(hashes) < 2:
        return recompute_scenes(conn, current_scene_gap(conn))

    # Fold in any existing pin group that already touches one of these hashes,
    # so repeated merges stay transitive and the table holds disjoint groups.
    ph = ",".join("?" * len(hashes))
    overlapping = [r[0] for r in conn.execute(
        f"SELECT DISTINCT group_id FROM scene_merges WHERE hash IN ({ph})",
        list(hashes))]
    if overlapping:
        oph = ",".join("?" * len(overlapping))
        for r in conn.execute(
            f"SELECT hash FROM scene_merges WHERE group_id IN ({oph})", overlapping):
            hashes.add(r[0])
        conn.execute(
            f"DELETE FROM scene_merges WHERE group_id IN ({oph})", overlapping)

    row = conn.execute("SELECT COALESCE(MAX(group_id), 0) + 1 m FROM scene_merges").fetchone()
    gid = row[0]
    conn.executemany(
        "INSERT OR IGNORE INTO scene_merges (group_id, hash) VALUES (?, ?)",
        [(gid, h) for h in hashes])
    conn.commit()
    return recompute_scenes(conn, current_scene_gap(conn))


def unmerge_scene(conn, scene_group: int) -> dict[str, Any]:
    """Drop any manual-merge pins touching this scene, then recompute, letting
    the scene fall back to pure time-gap segmentation."""
    photodb.ensure_scene_merges(conn)
    hashes = _hashes_in_scenes(conn, [int(scene_group)])
    if hashes:
        ph = ",".join("?" * len(hashes))
        gids = [r[0] for r in conn.execute(
            f"SELECT DISTINCT group_id FROM scene_merges WHERE hash IN ({ph})",
            list(hashes))]
        if gids:
            gph = ",".join("?" * len(gids))
            conn.execute(f"DELETE FROM scene_merges WHERE group_id IN ({gph})", gids)
            conn.commit()
    return recompute_scenes(conn, current_scene_gap(conn))


def autocull_duplicates(conn, *, progress: ProgressFn | None = None,
                        cancelled: CancelFn | None = None) -> dict[str, int]:
    gids = [row["dup_group"] for row in conn.execute(
        "SELECT DISTINCT dup_group FROM images WHERE dup_group IS NOT NULL")]
    kept = deleted = 0
    total = len(gids)
    for idx, gid in enumerate(gids):
        if cancelled and cancelled():
            raise OperationCancelled()
        members = conn.execute(
            """SELECT content_hash FROM images WHERE dup_group=?
               ORDER BY combined DESC, id ASC""", (gid,)).fetchall()
        for i, member in enumerate(members):
            if not member["content_hash"]:
                continue
            decision = "keep" if i == 0 else "del"
            conn.execute(
                "INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                (member["content_hash"], decision))
            kept += i == 0
            deleted += i != 0
        conn.commit()
        if progress:
            progress(idx + 1, total, f"Culled group {idx + 1}/{total}")
    return {"groups": total, "kept": kept, "deleted": deleted}


def trash_decisions(conn, *, image_ids: list[int] | None = None,
                    progress: ProgressFn | None = None,
                    cancelled: CancelFn | None = None,
                    log: LogFn | None = None) -> dict[str, Any]:
    ensure_library_schema(conn)
    trash = trash_dir(conn)
    trash_str = str(trash)
    ids = [int(v) for v in (image_ids or []) if str(v).lstrip("-").isdigit()]
    if ids:
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT i.id, i.path, i.content_hash FROM images i
               LEFT JOIN trash_moves tm
                 ON tm.image_id=i.id AND tm.state='trashed'
               WHERE i.id IN ({ph}) AND tm.id IS NULL""", ids).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT i.id, i.path, i.content_hash FROM images i
               JOIN {DEC_ON}
               LEFT JOIN trash_moves tm
                 ON tm.image_id=i.id AND tm.state='trashed'
               WHERE d.decision='del' AND tm.id IS NULL""").fetchall()

    moved = skipped = 0
    total = len(rows)
    if rows:
        trash.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0, total, f"Moving {total} file(s) to Trash")
    for idx, row in enumerate(rows):
        if cancelled and cancelled():
            raise OperationCancelled()
        src = Path(row["path"])
        if str(src).startswith(trash_str) or not src.exists():
            skipped += 1
        else:
            dest = unique_dest(trash, src.name)
            try:
                shutil.move(str(src), str(dest))
            except OSError as exc:
                skipped += 1
                if log:
                    log(f"[skip] {src}: {exc}")
            else:
                conn.execute("UPDATE images SET path=? WHERE id=?", (str(dest), row["id"]))
                conn.execute(
                    """INSERT INTO trash_moves
                       (image_id, hash, from_path, trash_path, state, trashed_at)
                       VALUES (?,?,?,?,?,?)""",
                    (row["id"], row["content_hash"], str(src), str(dest), "trashed", _now()))
                moved += 1
        conn.commit()
        if progress:
            progress(idx + 1, total, f"Trashed {moved}, skipped {skipped}")
    return {"moved": moved, "skipped": skipped,
            "trash_dir": trash_str, "rejected_dir": trash_str}


def restore_trash(conn, *, progress: ProgressFn | None = None,
                  cancelled: CancelFn | None = None,
                  log: LogFn | None = None) -> dict[str, int]:
    ensure_library_schema(conn)
    rows = conn.execute(
        """SELECT id, image_id, from_path, trash_path
           FROM trash_moves WHERE state='trashed' ORDER BY id DESC"""
    ).fetchall()
    restored = skipped = 0
    total = len(rows)
    if progress:
        progress(0, total, f"Restoring {total} file(s) from Trash")
    for idx, row in enumerate(rows):
        if cancelled and cancelled():
            raise OperationCancelled()
        src = Path(row["trash_path"])
        dst = Path(row["from_path"])
        if not src.exists():
            skipped += 1
            conn.execute(
                "UPDATE trash_moves SET state=?, restored_at=? WHERE id=?",
                ("missing", _now(), row["id"]))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = unique_dest(dst.parent, dst.name)
            try:
                shutil.move(str(src), str(dst))
            except OSError as exc:
                skipped += 1
                if log:
                    log(f"[skip] {src}: {exc}")
            else:
                conn.execute("UPDATE images SET path=? WHERE id=?", (str(dst), row["image_id"]))
                conn.execute(
                    "UPDATE trash_moves SET state=?, restored_at=? WHERE id=?",
                    ("restored", _now(), row["id"]))
                restored += 1
        conn.commit()
        if progress:
            progress(idx + 1, total, f"Restored {restored}, skipped {skipped}")
    return {"restored": restored, "skipped": skipped}


def empty_trash(conn, *, progress: ProgressFn | None = None,
                cancelled: CancelFn | None = None,
                log: LogFn | None = None) -> dict[str, int]:
    ensure_library_schema(conn)
    rows = conn.execute(
        "SELECT id, trash_path FROM trash_moves WHERE state='trashed' ORDER BY id"
    ).fetchall()
    deleted = skipped = 0
    total = len(rows)
    if progress:
        progress(0, total, f"Deleting {total} file(s) permanently")
    for idx, row in enumerate(rows):
        if cancelled and cancelled():
            raise OperationCancelled()
        path = Path(row["trash_path"])
        if not path.exists():
            skipped += 1
            conn.execute(
                "UPDATE trash_moves SET state=?, emptied_at=? WHERE id=?",
                ("missing", _now(), row["id"]))
        else:
            try:
                path.unlink()
            except OSError as exc:
                skipped += 1
                if log:
                    log(f"[skip] {path}: {exc}")
            else:
                deleted += 1
                conn.execute(
                    "UPDATE trash_moves SET state=?, emptied_at=? WHERE id=?",
                    ("emptied", _now(), row["id"]))
        conn.commit()
        if progress:
            progress(idx + 1, total, f"Deleted {deleted}, skipped {skipped}")
    return {"deleted": deleted, "skipped": skipped}
