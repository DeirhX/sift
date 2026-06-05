"""Cold start: the server must boot against a non-existent DB, create an empty
but fully-schema'd library, and serve the API so the first folder can be
analyzed entirely from the web UI (no CLI bootstrap)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sift.web import server


def test_ensure_schema_creates_usable_empty_db(tmp_path):
    # Parent dir does not exist yet — _ensure_schema must create the whole path.
    db_path = tmp_path / "newlib" / "photos.db"
    server.DB_PATH = db_path
    server.THUMB_DIR = db_path.parent / ".thumbs"

    server._ensure_schema()

    assert db_path.exists()

    client = TestClient(server.app)

    meta = client.get("/api/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert body["meta"].get("folder") in (None, "")     # nothing analyzed yet
    assert body["counts"]["total"] == 0
    assert body["clusters"] == []

    images = client.get("/api/images?limit=10")
    assert images.status_code == 200
    assert images.json()["items"] == []
    assert images.json()["total"] == 0


def test_default_db_path_lives_in_app_data():
    p = server._default_db_path()
    assert p.name == "photos.db"
    assert p.parent.name == "PhotoOrganizer"
    assert p.is_absolute()
