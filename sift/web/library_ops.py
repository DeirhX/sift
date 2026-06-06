"""Shared write-side operations for the photo library.

Routes and background tasks both need to move files, update SQLite, and report
counts. Keeping those rules here avoids the sync API and task runner quietly
becoming two different products.
"""
from __future__ import annotations

import shutil
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
