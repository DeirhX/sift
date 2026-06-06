"""Generic web task runner for long-running local operations.

The first web job implementation was analysis-specific. This module keeps the
same useful properties (background thread, subprocess log capture, SSE replay)
but moves them behind a task ledger so analyze, index, apply/undo, and later
operations share one lifecycle.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from sift.web import library_ops
from sift.web.analysis import REPO_ROOT, build_analyze_steps

TaskParams = dict[str, Any]
DbFactory = Callable[[], Any]

TERMINAL_STATES = {"done", "failed", "cancelled", "abandoned"}


def shell_quote(s: str) -> str:
    s = str(s)
    return f'"{s}"' if (" " in s or "!" in s) else s


def command_string(argv: list[str]) -> str:
    return " ".join(shell_quote(a) for a in argv)


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _now() -> float:
    return time.time()


class TaskContext:
    """Per-task helpers passed to executable task bodies."""

    def __init__(self, manager: "TaskManager", task_id: str):
        self.manager = manager
        self.task_id = task_id
        self._cancel = False
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancel or self.manager.cancel_requested(self.task_id)

    def line(self, text: str) -> None:
        self.manager.emit(self.task_id, "line", text)

    def partial(self, text: str) -> None:
        self.manager.emit(self.task_id, "partial", text)

    def progress(self, *, phase: str | None = None, pct: float | None = None,
                 message: str | None = None, current: int | None = None,
                 total: int | None = None) -> None:
        payload = {
            "phase": phase,
            "pct": pct,
            "message": message,
            "current": current,
            "total": total,
        }
        self.manager.update_task(self.task_id, phase=phase, progress=pct,
                                 message=message)
        self.manager.emit(self.task_id, "progress", payload)

    def run_commands(self, steps: list[tuple[str, list[str]]]) -> dict[str, Any]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        total = max(1, len(steps))
        for idx, (label, argv) in enumerate(steps):
            if self.cancelled:
                raise TaskCancelled()
            base_pct = idx / total
            self.progress(phase=label, pct=base_pct,
                          message=f"Starting {label}", current=idx, total=total)
            self.line(f"$ {command_string(argv)}")
            try:
                self._proc = subprocess.Popen(
                    argv, cwd=str(REPO_ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except Exception as e:
                raise RuntimeError(f"failed to launch {label}: {e}") from e
            self._pump(self._proc.stdout, label, idx, total)
            code = self._proc.wait()
            self.line(f"[{label} exited with code {code}]")
            if self.cancelled:
                raise TaskCancelled()
            if code != 0:
                raise RuntimeError(f"{label} exited with code {code}")
            self.progress(phase=label, pct=(idx + 1) / total,
                          message=f"Finished {label}", current=idx + 1, total=total)
        return {"exit_code": 0}

    def _pump(self, stream, label: str, step_idx: int, total_steps: int) -> None:
        buf = bytearray()
        pending_cr = False

        def commit(data: bytearray) -> None:
            text = data.decode("utf-8", "replace")
            if self._consume_progress_line(text, label, step_idx, total_steps):
                return
            self.line(text)

        while True:
            if self.cancelled:
                self.cancel()
                break
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            for b in chunk:
                if pending_cr:
                    pending_cr = False
                    if b == 0x0A:
                        commit(buf)
                        buf = bytearray()
                        continue
                    self.partial(buf.decode("utf-8", "replace"))
                    buf = bytearray()
                if b == 0x0D:
                    pending_cr = True
                elif b == 0x0A:
                    commit(buf)
                    buf = bytearray()
                else:
                    buf.append(b)
        if pending_cr or buf:
            commit(buf)

    def _consume_progress_line(self, text: str, label: str,
                               step_idx: int, total_steps: int) -> bool:
        marker = "SIFT_PROGRESS "
        # tqdm renders its bar to the merged stream with a bare \r (no newline),
        # so a progress print can arrive appended to a leftover bar fragment on
        # the same line (e.g. "Sharpness: 5%|… ?it/s]SIFT_PROGRESS {…}"). Match the
        # marker anywhere and parse the JSON tail; the bar prefix is transient
        # noise we drop (the live progress bar already conveys it).
        idx = text.find(marker)
        if idx == -1:
            return False
        try:
            payload = json.loads(text[idx + len(marker):])
        except Exception:
            return False
        phase_pct = payload.get("pct")
        overall = None
        if isinstance(phase_pct, (int, float)):
            overall = (step_idx + max(0.0, min(1.0, float(phase_pct)))) / total_steps
        phase = payload.get("phase") or label
        message = payload.get("message")
        self.progress(phase=phase, pct=overall, message=message,
                      current=payload.get("current"), total=payload.get("total"))
        return True


class TaskCancelled(Exception):
    pass


TaskBody = Callable[[TaskContext, TaskParams], dict[str, Any] | None]


class TaskManager:
    def __init__(self):
        self.db_path: Path | None = None
        self.thumb_dir: Path | None = None
        self.db_factory: DbFactory | None = None
        self._active: dict[str, TaskContext] = {}
        self._lock = threading.Lock()
        self._seq_lock = threading.Lock()

    def configure(self, *, db_path: Path, thumb_dir: Path, db_factory: DbFactory) -> None:
        self.db_path = db_path
        self.thumb_dir = thumb_dir
        self.db_factory = db_factory

    def _db(self):
        if self.db_factory is None:
            raise RuntimeError("task manager not configured")
        return self.db_factory()

    def abandon_running(self) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE tasks SET state='abandoned', ended=?, message=? "
                "WHERE state IN ('queued','running')",
                (_now(), "Server restarted before the task finished"))
            conn.commit()

    def start(self, task_type: str, params: TaskParams | None = None) -> dict:
        params = params or {}
        body, commands = self._build(task_type, params)
        with self._lock:
            if self.current_running() is not None:
                raise HTTPException(409, "a task is already running")
            task_id = uuid.uuid4().hex
            started = _now()
            with self._db() as conn:
                conn.execute(
                    """INSERT INTO tasks
                       (id, type, state, phase, progress, message, params_json, started)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (task_id, task_type, "running", None, 0.0, "Queued",
                     json.dumps(params), started))
                conn.commit()
            for cmd in commands:
                self.emit(task_id, "command", cmd)
            ctx = TaskContext(self, task_id)
            self._active[task_id] = ctx
            threading.Thread(target=self._run, args=(task_id, body, params, ctx),
                             daemon=True).start()
            return self.snapshot(task_id)

    def _run(self, task_id: str, body: TaskBody, params: TaskParams,
             ctx: TaskContext) -> None:
        try:
            self.update_task(task_id, message="Running")
            result = body(ctx, params) or {}
            if ctx.cancelled:
                self.finish(task_id, "cancelled", result=result)
            else:
                self.finish(task_id, "done", result=result, message="Done")
        except TaskCancelled:
            self.finish(task_id, "cancelled", message="Cancelled")
        except Exception as e:
            ctx.line(f"[error] {e}")
            self.finish(task_id, "failed", error=str(e), message="Failed")
        finally:
            with self._lock:
                self._active.pop(task_id, None)

    def cancel(self, task_id: str) -> dict:
        snap = self.snapshot(task_id)
        if snap["state"] not in ("queued", "running"):
            raise HTTPException(409, "task is not running")
        with self._db() as conn:
            conn.execute("UPDATE tasks SET cancel_requested=1, message=? WHERE id=?",
                         ("Cancelling", task_id))
            conn.commit()
        ctx = self._active.get(task_id)
        if ctx:
            ctx.cancel()
        self.emit(task_id, "progress", {"message": "Cancelling"})
        return self.snapshot(task_id)

    def cancel_requested(self, task_id: str) -> bool:
        with self._db() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def current_running(self) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE state IN ('queued','running') "
                "ORDER BY started DESC LIMIT 1").fetchone()
        return self.snapshot(row["id"]) if row else None

    def list_recent(self, limit: int = 20) -> dict:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id FROM tasks ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        tasks = [self.snapshot(r["id"]) for r in rows]
        return {"tasks": tasks, "current": self.current_running()}

    def snapshot(self, task_id: str) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise HTTPException(404, "task not found")
            cmd_rows = conn.execute(
                "SELECT payload_json FROM task_events "
                "WHERE task_id=? AND event_type='command' ORDER BY seq",
                (task_id,)).fetchall()
        return {
            "id": row["id"],
            "type": row["type"],
            "state": row["state"],
            "phase": row["phase"],
            "progress": row["progress"],
            "message": row["message"],
            "result": _json_loads(row["result_json"], None),
            "error": row["error"],
            "cancel_requested": bool(row["cancel_requested"]),
            "started": row["started"],
            "ended": row["ended"],
            "commands": [_json_loads(r["payload_json"], "") for r in cmd_rows],
        }

    def events_after(self, task_id: str, seq: int) -> list[dict]:
        # Validate the task exists so stream consumers get 404 for bad ids.
        self.snapshot(task_id)
        with self._db() as conn:
            rows = conn.execute(
                """SELECT seq, event_type, payload_json, ts
                   FROM task_events
                   WHERE task_id=? AND seq>?
                   ORDER BY seq""", (task_id, seq)).fetchall()
        return [{
            "seq": r["seq"],
            "event_type": r["event_type"],
            "payload": _json_loads(r["payload_json"], None),
            "ts": r["ts"],
        } for r in rows]

    def emit(self, task_id: str, event_type: str, payload: Any) -> None:
        with self._seq_lock:
            with self._db() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_id=?",
                    (task_id,)).fetchone()
                seq = int(row[0])
                conn.execute(
                    """INSERT INTO task_events
                       (task_id, seq, ts, event_type, payload_json)
                       VALUES (?,?,?,?,?)""",
                    (task_id, seq, _now(), event_type, json.dumps(payload)))
                conn.commit()

    def update_task(self, task_id: str, *, phase=None, progress=None,
                    message=None) -> None:
        fields = []
        vals: list[Any] = []
        if phase is not None:
            fields.append("phase=?")
            vals.append(phase)
        if progress is not None:
            fields.append("progress=?")
            vals.append(max(0.0, min(1.0, float(progress))))
        if message is not None:
            fields.append("message=?")
            vals.append(message)
        if not fields:
            return
        vals.append(task_id)
        with self._db() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", vals)
            conn.commit()

    def finish(self, task_id: str, state: str, *, result=None, error=None,
               message=None) -> None:
        ended = _now()
        progress = 1.0 if state == "done" else None
        with self._db() as conn:
            conn.execute(
                """UPDATE tasks
                   SET state=?, ended=?, progress=COALESCE(?, progress),
                       result_json=?, error=?, message=COALESCE(?, message)
                   WHERE id=?""",
                (state, ended, progress,
                 json.dumps(result) if result is not None else None,
                 error, message, task_id))
            conn.commit()
        self.emit(task_id, "end", {"state": state, "error": error})

    def _build(self, task_type: str, params: TaskParams) -> tuple[TaskBody, list[str]]:
        if task_type == "analyze_library":
            return self._build_analyze(params)
        if task_type == "index_library":
            return self._build_index(params)
        if task_type == "apply_decisions":
            return self._build_trash(params)
        if task_type == "trash_decisions":
            return self._build_trash(params)
        if task_type == "undo_apply":
            return self._build_restore(params)
        if task_type == "restore_trash":
            return self._build_restore(params)
        if task_type == "empty_trash":
            return self._build_empty_trash(params)
        if task_type == "autocull_duplicates":
            return self._build_autocull(params)
        raise HTTPException(400, f"unknown task type: {task_type!r}")

    def _build_analyze(self, params: TaskParams) -> tuple[TaskBody, list[str]]:
        if self.db_path is None or self.thumb_dir is None:
            raise RuntimeError("task manager not configured")
        steps = build_analyze_steps(params, db_path=self.db_path,
                                    thumb_dir=self.thumb_dir,
                                    db_factory=self._db)
        steps = [(label, [*argv, "--progress-json"]) for label, argv in steps]
        commands = [command_string(argv) for _, argv in steps]

        def body(ctx: TaskContext, _params: TaskParams):
            return ctx.run_commands(steps)
        return body, commands

    def _build_index(self, params: TaskParams) -> tuple[TaskBody, list[str]]:
        if self.db_path is None or self.thumb_dir is None:
            raise RuntimeError("task manager not configured")
        report = Path(params.get("report") or (self.db_path.parent / "audit_report.json"))
        if not report.exists():
            raise HTTPException(400, f"report not found: {str(report)!r}")
        argv = [sys.executable, "-m", "sift", "index", str(report),
                "--db", str(self.db_path), "--thumbs", str(self.thumb_dir),
                "--progress-json"]
        for key, flag in (("thumb_size", "--thumb-size"),
                          ("thumb_quality", "--thumb-quality"),
                          ("workers", "--workers")):
            if params.get(key) not in (None, ""):
                argv += [flag, str(params[key])]
        if params.get("skip_thumbs"):
            argv.append("--skip-thumbs")
        if params.get("force_thumbs"):
            argv.append("--force-thumbs")
        if params.get("no_prune"):
            argv.append("--no-prune")
        steps = [("index", argv)]

        def body(ctx: TaskContext, _params: TaskParams):
            return ctx.run_commands(steps)
        return body, [command_string(argv)]

    def _build_trash(self, _params: TaskParams) -> tuple[TaskBody, list[str]]:
        return self._run_trash_decisions, ["move marked photos to trash"]

    def _build_restore(self, _params: TaskParams) -> tuple[TaskBody, list[str]]:
        return self._run_restore_trash, ["restore trash"]

    def _build_empty_trash(self, _params: TaskParams) -> tuple[TaskBody, list[str]]:
        return self._run_empty_trash, ["empty trash"]

    def _build_autocull(self, _params: TaskParams) -> tuple[TaskBody, list[str]]:
        return self._run_autocull, ["autocull duplicate groups"]

    def _run_autocull(self, ctx: TaskContext, _params: TaskParams) -> dict[str, Any]:
        ctx.progress(phase="autocull", pct=0.0, message="Loading duplicate groups")
        with self._db() as conn:
            try:
                result = library_ops.autocull_duplicates(
                    conn,
                    progress=lambda current, total, message: ctx.progress(
                        phase="autocull",
                        pct=(current / total if total else 1.0),
                        message=message, current=current, total=total),
                    cancelled=lambda: ctx.cancelled,
                )
            except library_ops.OperationCancelled:
                raise TaskCancelled()
        ctx.line(
            f"Autoculled {result['groups']} groups: kept {result['kept']}, "
            f"deleted {result['deleted']}")
        return result

    def _run_trash_decisions(self, ctx: TaskContext, _params: TaskParams) -> dict[str, Any]:
        with self._db() as conn:
            try:
                result = library_ops.trash_decisions(
                    conn,
                    image_ids=_params.get("image_ids"),
                    progress=lambda current, total, message: ctx.progress(
                        phase="apply",
                        pct=(current / total if total else 1.0),
                        message=message, current=current, total=total),
                    cancelled=lambda: ctx.cancelled,
                    log=ctx.line,
                )
            except library_ops.OperationCancelled:
                raise TaskCancelled()
        ctx.line(f"Trash complete: moved {result['moved']}, skipped {result['skipped']}")
        return result

    def _run_restore_trash(self, ctx: TaskContext, _params: TaskParams) -> dict[str, Any]:
        with self._db() as conn:
            try:
                result = library_ops.restore_trash(
                    conn,
                    progress=lambda current, total, message: ctx.progress(
                        phase="undo",
                        pct=(current / total if total else 1.0),
                        message=message, current=current, total=total),
                    cancelled=lambda: ctx.cancelled,
                    log=ctx.line,
                )
            except library_ops.OperationCancelled:
                raise TaskCancelled()
        ctx.line(f"Undo complete: restored {result['restored']}, skipped {result['skipped']}")
        return result

    def _run_empty_trash(self, ctx: TaskContext, _params: TaskParams) -> dict[str, Any]:
        with self._db() as conn:
            try:
                result = library_ops.empty_trash(
                    conn,
                    progress=lambda current, total, message: ctx.progress(
                        phase="empty-trash",
                        pct=(current / total if total else 1.0),
                        message=message, current=current, total=total),
                    cancelled=lambda: ctx.cancelled,
                    log=ctx.line,
                )
            except library_ops.OperationCancelled:
                raise TaskCancelled()
        ctx.line(
            f"Empty Trash complete: deleted {result['deleted']}, "
            f"skipped {result['skipped']}")
        return result


MANAGER = TaskManager()

