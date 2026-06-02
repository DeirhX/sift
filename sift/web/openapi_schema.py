"""Dump the FastAPI OpenAPI schema as JSON to stdout.

Used by the frontend type codegen (``npm run codegen``):

    python -m sift.web.openapi_schema > openapi.json
    npx openapi-typescript openapi.json -o src/api/schema.d.ts

Importing the app is side-effect free (no DB is opened until a request), so this
is safe to run anywhere the ``sift`` package is importable.
"""
import json

from sift.web.server import app


def main() -> None:
    print(json.dumps(app.openapi(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
