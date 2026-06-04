"""Unified `sift` command-line dispatcher.

Three subcommands map to the three pipeline stages. Each stage owns its own
argparse contract (see ``sift.audit.cli``, ``sift.web.build_db``,
``sift.web.server``); this dispatcher just routes to the right ``main()`` and
re-points ``sys.argv`` so each stage sees a natural prog name plus its own args.

Subcommand modules are imported lazily, so ``sift serve`` never drags in the
heavy ML analysis stack and a web-only install (no ``[ml]`` extra) still works.
"""
import sys

_USAGE = """usage: sift <command> [options]

commands:
  analyze   run the analysis pipeline on a photo folder -> audit_report.json
  merge     combine per-root audit reports -> one library audit_report.json
  index     ingest an audit_report.json -> photos.db + WebP thumbnails
  serve     launch the FastAPI review web app

Run `sift <command> --help` for command-specific options.
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd == "analyze":
        from sift.audit.cli import main as run
    elif cmd == "merge":
        from sift.audit.merge_cli import main as run
    elif cmd == "index":
        from sift.web.build_db import main as run
    elif cmd == "serve":
        from sift.web.server import main as run
    else:
        sys.stderr.write(f"sift: unknown command {cmd!r}\n\n{_USAGE}")
        return 2

    # Each stage parses sys.argv directly; hand it just its own args.
    sys.argv = [f"sift {cmd}", *rest]
    rc = run()
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
