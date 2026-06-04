"""Content-hash-keyed embedding cache.

A small SQLite sidecar that persists the expensive, pixel-invariant vectors so
they are computed once and reused forever:

  - CLIP ViT-B/32 scene embeddings, keyed by content hash.
  - Face (VGGFace2) embeddings, keyed by content hash + face bbox.

Because the key is the content hash (not the path), a moved or renamed file
reuses its vectors with zero recompute, and the global face-clustering / scene
regroup steps can run on cached embeddings without re-reading a single pixel.

Stored as raw float32 bytes; callers get back 1-D numpy arrays. The store is a
cache, never a source of truth — a missing row just means "recompute it".
"""
import sqlite3
from pathlib import Path

import numpy as np

_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS clip_embeddings (
    hash TEXT PRIMARY KEY,
    dim  INTEGER NOT NULL,
    emb  BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS face_embeddings (
    hash TEXT NOT NULL,
    bbox TEXT NOT NULL,
    dim  INTEGER NOT NULL,
    emb  BLOB NOT NULL,
    PRIMARY KEY (hash, bbox)
);
"""


def bbox_key(bbox) -> str:
    """Canonical face-bbox key. Mirrors photodb.bbox_key's 1-decimal rounding so
    a cached embedding lines up with the same face across the analyze/merge/index
    boundary. Both ends MUST format identically."""
    return ",".join(f"{round(float(v), 1)}" for v in bbox)


def _to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


class EmbedStore:
    """Thin wrapper over the sidecar DB. Open once per run; cheap to construct."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── CLIP scene embeddings (keyed by content hash) ────────────────────────
    def get_clip(self, hashes) -> dict:
        """Return {hash: 1-D ndarray} for the subset of `hashes` that are cached."""
        out: dict = {}
        hashes = list(hashes)
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            ph = ",".join("?" * len(chunk))
            for h, dim, blob in self.conn.execute(
                    f"SELECT hash, dim, emb FROM clip_embeddings WHERE hash IN ({ph})",
                    chunk):
                out[h] = _from_blob(blob, dim)
        return out

    def put_clip(self, items) -> None:
        """Persist an iterable of (hash, vector)."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO clip_embeddings (hash, dim, emb) VALUES (?,?,?)",
            [(h, int(np.size(v)), _to_blob(v)) for h, v in items])
        self.conn.commit()

    # ── Face embeddings (keyed by content hash + bbox) ───────────────────────
    def get_faces(self, hash_: str) -> dict:
        """Return {bbox_key: 1-D ndarray} for one image's cached face embeddings."""
        return {b: _from_blob(blob, dim) for b, dim, blob in self.conn.execute(
            "SELECT bbox, dim, emb FROM face_embeddings WHERE hash=?", (hash_,))}

    def put_faces(self, hash_: str, items) -> None:
        """Persist an iterable of (bbox, vector) for one image's faces."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO face_embeddings (hash, bbox, dim, emb) "
            "VALUES (?,?,?,?)",
            [(hash_, bbox_key(b), int(np.size(v)), _to_blob(v)) for b, v in items])
        self.conn.commit()

    def all_faces(self):
        """Yield (hash, bbox_key, 1-D ndarray) for every cached face embedding —
        the global clustering pass reads the whole library through this."""
        for h, b, dim, blob in self.conn.execute(
                "SELECT hash, bbox, dim, emb FROM face_embeddings"):
            yield h, b, _from_blob(blob, dim)
