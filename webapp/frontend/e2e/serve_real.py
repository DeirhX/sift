#!/usr/bin/env python3
"""Launch the real FastAPI server against a COPY of a real photo library, for
the opt-in "real pipeline" e2e (analysis + reorganization). The original folder
is never touched — we copy a small, configurable subset into a temp working dir
and serve a fresh empty DB. The actual photo_audit + build_db run is driven from
the browser via the Re-analyze panel during the test.

Configuration (all via env):
  E2E_SOURCE_FOLDER  source library to copy from   (default: E:\\F\\!To Pictures)
  E2E_SOURCE_LIMIT   how many images to copy        (default: 12; 0 = all)
  E2E_REAL_PORT      port to serve on               (default: 8766)

Writes the absolute path of the copied library to .real/libpath.txt so the spec
can both type it into the folder field and verify on-disk file moves.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # webapp/frontend/e2e
FRONTEND = HERE.parent                                  # webapp/frontend
WEBAPP = FRONTEND.parent                                # webapp
sys.path.insert(0, str(WEBAPP))

import server                                           # noqa: E402
import photodb                                          # noqa: E402
import sqlite3                                          # noqa: E402

PORT = int(os.environ.get("E2E_REAL_PORT", "8766"))
SOURCE = Path(os.environ.get("E2E_SOURCE_FOLDER", r"E:\F\!To Pictures"))
LIMIT = int(os.environ.get("E2E_SOURCE_LIMIT", "12"))

REAL = HERE / ".real"
LIB = REAL / "lib"
DB = REAL / "photos.db"
THUMBS = REAL / ".thumbs"
LIBPATH_FILE = REAL / "libpath.txt"

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def prepare():
    # Clean slate so each run is deterministic.
    if REAL.exists():
        shutil.rmtree(REAL, ignore_errors=True)
    LIB.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(parents=True, exist_ok=True)

    copied = 0
    if SOURCE.is_dir():
        srcs = sorted(
            (p for p in SOURCE.rglob("*") if p.is_file() and p.suffix.lower() in EXTS),
            key=lambda p: str(p).lower(),
        )
        if LIMIT > 0:
            srcs = srcs[:LIMIT]
        seen = {}
        for p in srcs:
            name = p.name
            if name in seen:                              # avoid basename collisions
                seen[name] += 1
                name = f"{p.stem}_{seen[name]}{p.suffix}"
            else:
                seen[name] = 0
            try:
                shutil.copy2(p, LIB / name)
                copied += 1
            except OSError:
                pass
    else:
        print(f"WARNING: source folder not found: {SOURCE}", file=sys.stderr)

    LIBPATH_FILE.write_text(str(LIB), encoding="utf-8")
    print(f"copied {copied} image(s) from {SOURCE} -> {LIB}")


def main():
    prepare()
    server.DB_PATH = DB
    server.THUMB_DIR = THUMBS
    server.FRONTEND_DIST = FRONTEND / "dist"
    # Fresh, empty DB: create the base tables (then migrations) so the server
    # answers queries on an empty library until analysis populates it.
    conn = sqlite3.connect(DB)
    photodb.create_base_schema(conn)
    photodb.ensure_schema(conn)
    conn.commit()
    conn.close()
    server._mount_frontend()
    import uvicorn
    print(f"real e2e server on http://127.0.0.1:{PORT}  (db={DB})")
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
