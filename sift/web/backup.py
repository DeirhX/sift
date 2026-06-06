#!/usr/bin/env python3
"""
backup.py — Snapshot / restore safety net for the single centralized photos.db.

The library is one SQLite file holding two very different kinds of data:

  - Rebuildable: images / faces / tags / thumbnails. Lost? Re-run analyze+index.
  - Irreplaceable: decisions, cluster names, face overrides, cluster-name
    anchors, photo roots, trash ledger. These are *user labor*; there is no
    upstream to regenerate them from.

Because everything lives in one file, one corruption event (power loss mid-write,
a disk error, a bug in the rebuild) could take the irreplaceable part with it.
This module is the parachute: consistent online snapshots, rotation, integrity
checks, and a validated restore.

Design choices:
  - Snapshots use SQLite's online backup API (Connection.backup), which produces
    a transactionally consistent copy of a *live* WAL database without needing a
    checkpoint or a quiet moment. Naive file copies of a WAL DB can be torn.
  - Restore validates the snapshot's integrity *before* overwriting, copies the
    current (possibly corrupt) DB aside first, and clears stale -wal/-shm
    sidecars so the restored file isn't shadowed by a leftover write-ahead log.
"""

import time
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime

DEFAULT_KEEP = 10


def backups_dir(db_path) -> Path:
    """Where snapshots live: a `backups/` sibling of the DB file."""
    return Path(db_path).parent / "backups"


def quick_check(db_path) -> bool:
    """True if the DB opens and passes PRAGMA quick_check. A missing file is
    treated as healthy (nothing to corrupt — e.g. a cold start)."""
    p = Path(db_path)
    if not p.exists():
        return True
    try:
        conn = sqlite3.connect(p)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        return bool(row) and row[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def _online_copy(src_path, dest_path) -> None:
    """Consistent hot copy of a live SQLite DB via the backup API."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def _has_rows(db_path) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        finally:
            conn.close()
        return bool(n)
    except sqlite3.Error:
        return False


def snapshot(db_path, *, label: str | None = None, keep: int = DEFAULT_KEEP,
             skip_if_empty: bool = True) -> Path | None:
    """Write a timestamped consistent snapshot into the backups dir and rotate
    old ones down to `keep`. Returns the snapshot path, or None when there is
    nothing worth saving (missing file, or — when skip_if_empty — an empty
    library). Best-effort: never raises on a backend hiccup, just returns None."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    if skip_if_empty and not _has_rows(db_path):
        return None
    try:
        d = backups_dir(db_path)
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"-{label}" if label else ""
        dest = d / f"{db_path.stem}-{ts}{suffix}.db"
        i = 1
        while dest.exists():                       # avoid same-second collisions
            dest = d / f"{db_path.stem}-{ts}{suffix}.{i}.db"
            i += 1
        _online_copy(db_path, dest)
        _prune(d, db_path.stem, keep)
        return dest
    except (sqlite3.Error, OSError) as e:
        print(f"  backup: snapshot failed ({e})")
        return None


def snapshot_if_stale(db_path, *, max_age_hours: float = 24,
                      keep: int = DEFAULT_KEEP) -> Path | None:
    """Snapshot only if the newest existing backup is older than max_age_hours.
    Throttles automatic startup backups so a reload/restart loop can't spam
    them, while still guaranteeing a recent copy exists during normal use."""
    bks = list_backups(db_path)
    if bks and (time.time() - bks[0]["mtime"]) < max_age_hours * 3600:
        return None
    return snapshot(db_path, label="auto", keep=keep)


def list_backups(db_path) -> list[dict]:
    """Snapshots for this DB, newest first."""
    d = backups_dir(db_path)
    stem = Path(db_path).stem
    if not d.exists():
        return []
    out = []
    for f in d.glob(f"{stem}-*.db"):
        try:
            st = f.stat()
        except OSError:
            continue
        out.append({"path": str(f), "name": f.name, "size": st.st_size,
                    "mtime": st.st_mtime})
    out.sort(key=lambda b: b["mtime"], reverse=True)
    return out


def _prune(d: Path, stem: str, keep: int) -> None:
    files = sorted(d.glob(f"{stem}-*.db"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[max(0, keep):]:
        try:
            f.unlink()
        except OSError:
            pass


def restore(db_path, backup_path) -> Path | None:
    """Replace the live DB with a snapshot. Validates the snapshot first, copies
    the current DB aside (so a mistaken restore is itself reversible), and clears
    stale WAL/SHM sidecars. Returns the safety-copy path (or None if there was no
    live DB to preserve). Raises if the backup is missing or fails integrity."""
    db_path = Path(db_path)
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise FileNotFoundError(f"backup not found: {backup_path}")
    if not quick_check(backup_path):
        raise ValueError(f"backup failed integrity check, refusing: {backup_path}")

    safety = None
    if db_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
        shutil.copy2(db_path, safety)

    # A leftover write-ahead log would otherwise be replayed onto the restored
    # file and undo the restore. Drop the sidecars.
    for ext in ("-wal", "-shm"):
        side = Path(str(db_path) + ext)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass

    shutil.copy2(backup_path, db_path)
    return safety


# ── CLI: `sift backup [create|list|restore <file>]` ──────────────────────────

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="sift backup",
        description="Snapshot, list, or restore the photo library database.")
    ap.add_argument("action", nargs="?", default="create",
                    choices=["create", "list", "restore"],
                    help="create a snapshot (default), list snapshots, or "
                         "restore one")
    ap.add_argument("file", nargs="?",
                    help="snapshot to restore (path, or bare name from the "
                         "backups dir); required for 'restore'")
    ap.add_argument("--db", default=None,
                    help="library DB path (defaults to the app-data location "
                         "used by `sift serve`)")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"snapshots to retain (default {DEFAULT_KEEP})")
    args = ap.parse_args()

    if args.db:
        db_path = Path(args.db)
    else:
        from sift.web.server import _default_db_path
        db_path = _default_db_path()

    if args.action == "create":
        dest = snapshot(db_path, label="manual", keep=args.keep,
                        skip_if_empty=False)
        if dest:
            print(f"Backup written: {dest}")
            return 0
        print(f"Nothing to back up — {db_path} is missing.")
        return 1

    if args.action == "list":
        bks = list_backups(db_path)
        if not bks:
            print(f"No backups in {backups_dir(db_path)}")
            return 0
        print(f"Backups in {backups_dir(db_path)} (newest first):")
        for b in bks:
            ts = datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {ts}   {b['size'] / 1e6:8.2f} MB   {b['name']}")
        return 0

    # restore
    if not args.file:
        print("restore needs a snapshot (see `sift backup list`)")
        return 2
    bp = Path(args.file)
    if not bp.exists():
        cand = backups_dir(db_path) / args.file
        if cand.exists():
            bp = cand
    try:
        safety = restore(db_path, bp)
    except (FileNotFoundError, ValueError) as e:
        print(f"Restore failed: {e}")
        return 1
    print(f"Restored {bp} -> {db_path}")
    if safety:
        print(f"Previous DB saved at {safety}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
