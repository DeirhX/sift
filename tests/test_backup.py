"""Backup/restore safety net for the single centralized photos.db.

These guard the parachute: snapshots are consistent and rotated, restore
round-trips and refuses corrupt input, integrity checks catch a trashed file,
and the hardened connection actually applies its durability pragmas.
"""
import sqlite3

import pytest

from sift.web import backup, photodb


def _decisions(db_path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT hash, decision FROM decisions").fetchall())
    finally:
        conn.close()


def _set_decision(db_path, h, d):
    conn = photodb.connect(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                     (h, d))
        conn.commit()
    finally:
        conn.close()


def test_connect_applies_pragmas(tmp_path):
    p = tmp_path / "x.db"
    conn = photodb.connect(p)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_snapshot_and_restore_roundtrip(env):
    db = env.db

    # A snapshot of the pristine library (no decisions yet).
    b1 = backup.snapshot(db, skip_if_empty=False)
    assert b1 is not None and b1.exists()
    assert backup.quick_check(b1)
    assert _decisions(db) == {}

    # Mutate the live DB, then roll back to the snapshot.
    _set_decision(db, "abc", "del")
    assert _decisions(db) == {"abc": "del"}

    safety = backup.restore(db, b1)
    assert safety is not None and safety.exists()      # old DB preserved
    assert _decisions(db) == {}                        # mutation undone


def test_snapshot_skips_empty_library(tmp_path):
    # Schema-only DB with zero images: nothing worth snapshotting by default.
    db = tmp_path / "photos.db"
    conn = photodb.connect(db)
    photodb.create_base_schema(conn)
    conn.commit(); conn.close()

    assert backup.snapshot(db) is None                 # skip_if_empty default
    assert backup.snapshot(db, skip_if_empty=False) is not None


def test_snapshot_missing_db_returns_none(tmp_path):
    assert backup.snapshot(tmp_path / "nope.db") is None


def test_quick_check_detects_corruption(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is definitely not a sqlite database")
    assert backup.quick_check(bad) is False


def test_quick_check_missing_is_healthy(tmp_path):
    assert backup.quick_check(tmp_path / "absent.db") is True


def test_restore_rejects_corrupt_backup(env, tmp_path):
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"nope")
    with pytest.raises(ValueError):
        backup.restore(env.db, bad)


def test_restore_missing_backup_raises(env, tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.restore(env.db, tmp_path / "ghost.db")


def test_prune_keeps_only_n(env):
    for _ in range(5):
        backup.snapshot(env.db, skip_if_empty=False, keep=3)
    assert len(backup.list_backups(env.db)) <= 3


def test_snapshot_if_stale_throttles(env):
    first = backup.snapshot_if_stale(env.db, max_age_hours=24)
    assert first is not None
    again = backup.snapshot_if_stale(env.db, max_age_hours=24)
    assert again is None                               # recent backup exists

    forced = backup.snapshot_if_stale(env.db, max_age_hours=0)
    assert forced is not None                          # stale threshold -> new


def test_list_backups_newest_first(env):
    import os, time
    a = backup.snapshot(env.db, skip_if_empty=False, keep=10)
    # Force a distinguishable mtime so ordering is unambiguous.
    os.utime(a, (time.time() - 100, time.time() - 100))
    b = backup.snapshot(env.db, skip_if_empty=False, keep=10)
    names = [x["name"] for x in backup.list_backups(env.db)]
    assert names[0] == b.name


def test_restore_safety_copy_includes_commits_still_in_the_wal(tmp_path):
    """The pre-restore copy is the undo for a mistaken restore, so it must hold
    everything committed — including rows that only exist in the -wal file."""
    import shutil

    src_dir = tmp_path / "live"
    src_dir.mkdir()
    live = src_dir / "photos.db"
    conn = photodb.connect(live)
    photodb.create_base_schema(conn)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")           # schema into the main file
    snap = backup.snapshot(live, skip_if_empty=False)        # the "old" state
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO decisions (hash, decision) VALUES ('wal-only', 'keep')")
    conn.commit()
    # Freeze the on-disk state while the commit is still only in the WAL (as
    # after a crash), in a place with no open connection.
    crashed_dir = tmp_path / "crashed"
    crashed_dir.mkdir()
    crashed = crashed_dir / "photos.db"
    shutil.copy2(live, crashed)
    shutil.copy2(str(live) + "-wal", str(crashed) + "-wal")
    conn.close()

    main_file_only = tmp_path / "main_only.db"
    shutil.copy2(crashed, main_file_only)
    assert _decisions(main_file_only) == {}                  # the row is WAL-only

    safety = backup.restore(crashed, snap)

    assert _decisions(crashed) == {}                         # snapshot is live
    assert _decisions(safety) == {"wal-only": "keep"}        # nothing was dropped
    assert not (crashed_dir / "photos.db.restoring").exists()
