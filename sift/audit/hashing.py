"""Content hashing — the one definition of a photo's stable identity.

A photo's blake2b-128 content hash (32 hex chars) keys everything that must
survive the file being moved, renamed, or re-sorted: keep/delete decisions,
face overrides, person-name anchors, and the cached embeddings/scores. Both the
analysis layer (cli) and the index layer (build_db) hash the same way, so the
key can never drift between producing a record and ingesting it.
"""
import hashlib
from pathlib import Path

HASH_CHUNK = 1 << 20   # 1 MiB read blocks


def content_hash(path) -> str:
    """blake2b-128 of the file's bytes (32 hex chars). Falls back to hashing the
    path string when the file can't be read, so an unreadable image still gets a
    stable key rather than crashing the caller."""
    p = Path(path)
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return hashlib.blake2b(str(p).encode("utf-8"), digest_size=16).hexdigest()
