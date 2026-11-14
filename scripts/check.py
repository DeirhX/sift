#!/usr/bin/env python3
"""Run every check CI runs, in CI's order, and stop at the first failure.

    python scripts/check.py           # pytest, API types, typecheck, unit tests
    python scripts/check.py --e2e     # ... plus the Playwright browser tests
    python scripts/check.py --fast    # pytest + typecheck only (inner loop)

One command so a change is never "probably fine": if this passes, the required
CI job passes. It also papers over two local traps — a globally set
NODE_ENV=production, and something else already listening on the e2e port.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tool(name: str) -> str:
    path = shutil.which(name)           # resolves npm.cmd / npx.cmd on Windows
    if not path:
        sys.exit(f"`{name}` not found on PATH (install Node 18+).")
    return path


def run(label: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    print(f"\n── {label} " + "─" * max(4, 66 - len(label)), flush=True)
    start = time.monotonic()
    code = subprocess.call(cmd, cwd=cwd, env=env)
    took = time.monotonic() - start
    if code != 0:
        sys.exit(f"\nFAILED: {label} (exit {code}, {took:.0f}s)\n  {' '.join(cmd)}")
    print(f"ok: {label} ({took:.0f}s)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--e2e", action="store_true", help="also run the Playwright suite")
    ap.add_argument("--fast", action="store_true", help="pytest + typecheck only")
    args = ap.parse_args()

    env = dict(os.environ)
    env.pop("NODE_ENV", None)           # production would break React's test helpers

    run("Python tests", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], ROOT, env)

    if not (FRONTEND / "node_modules").exists():
        sys.exit("frontend/node_modules is missing: run `npm ci` in frontend/ first.")
    npm, npx = tool("npm"), tool("npx")

    if not args.fast:
        run("Regenerate API types", [npm, "run", "codegen"], FRONTEND, env)
        run("API types are committed (schema.d.ts unchanged)",
            ["git", "diff", "--exit-code", "--stat", "--", "src/api/schema.d.ts"], FRONTEND, env)
    run("Typecheck", [npx, "tsc", "--noEmit"], FRONTEND, env)
    if not args.fast:
        run("Frontend unit tests", [npx, "vitest", "run"], FRONTEND, env)
    if args.e2e:
        e2e_env = {**env, "E2E_PORT": env.get("E2E_PORT") or str(free_port())}
        run("Build frontend", [npx, "vite", "build"], FRONTEND, e2e_env)
        run("Browser tests (Playwright)", [npx, "playwright", "test"], FRONTEND, e2e_env)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
