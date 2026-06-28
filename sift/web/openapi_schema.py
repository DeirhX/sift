"""Dump the FastAPI OpenAPI schema as JSON to stdout.

Used by the frontend type codegen (``npm run codegen``):

    python -m sift.web.openapi_schema > openapi.json
    npx openapi-typescript openapi.json -o src/api/schema.d.ts

Importing the app is side-effect free (no DB is opened until a request), so this
is safe to run anywhere the ``sift`` package is importable.
"""
import json
import sys

from sift.web.server import app


def main() -> None:
    # Write UTF-8 bytes explicitly. print() would use the OS locale encoding
    # (cp1252 on Windows CI), which openapi-typescript then misreads as UTF-8 —
    # corrupting non-ASCII docstrings (e.g. em dashes → U+FFFD) and making the
    # generated schema differ between machines. Bytes make codegen deterministic.
    data = json.dumps(app.openapi(), indent=2, ensure_ascii=False)
    sys.stdout.buffer.write(data.encode("utf-8"))


if __name__ == "__main__":
    main()
