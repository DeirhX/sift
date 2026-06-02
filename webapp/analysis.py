#!/usr/bin/env python3
"""
analysis.py — the reanalysis subsystem.

Owns everything behind the /api/analyze/* endpoints: translating the UI payload
into validated argv (`build_analyze_steps`) and running those steps in a
background thread while capturing tqdm-style progress output (`AnalysisJob`).

Kept config-agnostic: `build_analyze_steps` takes the DB/thumb paths and a
connection factory as arguments rather than reaching into server globals, so the
subsystem has a clean seam and no import cycle with ``server.py``.
"""
import os
import sys
import time
import threading
import subprocess
from pathlib import Path

from fastapi import HTTPException

AUDIT_SCRIPT = Path(__file__).resolve().parent.parent / "photo_audit.py"
BUILD_SCRIPT = Path(__file__).resolve().parent / "build_db.py"
REPO_ROOT    = AUDIT_SCRIPT.parent

_BACKENDS = {"para", "clip-iqa", "both"}


def shell_quote(s: str) -> str:
    s = str(s)
    return f'"{s}"' if (" " in s or "!" in s) else s


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
                "commands": [" ".join(shell_quote(a) for a in argv)
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
            self._commit(f"$ {' '.join(shell_quote(a) for a in argv)}")
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


def build_analyze_steps(payload: dict, *, db_path: Path, thumb_dir: Path,
                        db_factory) -> list[tuple[str, list[str]]]:
    """Translate the UI payload into argv for photo_audit + build_db. Only
    known flags are emitted; the folder is validated. Raises HTTPException.

    `db_path`/`thumb_dir` target the DB the server is serving; `db_factory` is a
    context-manager connection factory used to look up the default folder."""
    folder = (payload.get("folder") or "").strip()
    if not folder:
        with db_factory() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='folder'").fetchone()
            folder = row["value"] if row else ""
    fpath = Path(folder)
    if not folder or not fpath.is_dir():
        raise HTTPException(400, f"folder not found: {folder!r}")

    report_path = db_path.parent / "audit_report.json"
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
    if payload.get("no_scenes"):
        audit.append("--no-scenes")

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
    _num("scene_time_gap", "--scene-time-gap", 1, 1440, float)
    _num("scene_sim", "--scene-sim", 0.0, 1.0, float)

    build = [py, str(BUILD_SCRIPT), str(report_path),
             "--db", str(db_path), "--thumbs", str(thumb_dir)]

    # scope=both (confirmed): audit then rebuild the DB the server is serving.
    return [("photo_audit", audit), ("build_db", build)]
