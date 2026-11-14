"""Face and person operations for the People view.

Like ``library_ops``: plain functions over an open connection, shared by the
single-face routes and the batch People routes so both edit faces by exactly the
same rules (override recording, name re-anchoring, portrait re-aggregation).

The server still loads no models. Identity *similarity* (which people look
alike, which face is the odd one out) is read from the face embeddings that
``sift analyze`` already cached in the ``.embeddings.sqlite`` sidecar — plain
float vectors, compared with numpy.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from sift.web import photodb
from sift.web.photodb import MANUAL_CLUSTER_BASE, bbox_key, largest_face_aggregate
from sift.web.queries import DEC_JOIN, DECISION, TRASH_ON

# Clusters whose centroids are at least this similar are offered as "possibly
# the same person". Deliberately below the clusterer's own merge threshold
# (cosine 0.50): it is a suggestion for a human, not an automatic merge.
SUGGEST_MIN_SIM = 0.35
FACE_SORTS = {"quality", "date", "size", "match"}


class FaceNotFound(LookupError):
    pass


# ── Mutations ────────────────────────────────────────────────────────────────

def reaggregate_faces(conn, image_id) -> None:
    """Recompute an image's face count + portrait fields from its current faces.
    Mirrors build_db's override-replay recompute via the same
    largest_face_aggregate, so an API face edit and a fresh ingest can't disagree
    on n_faces/face_sharp/face_expr/portrait."""
    rows = conn.execute(
        "SELECT x1, y1, x2, y2, sharp, expr FROM faces WHERE image_id=?",
        (image_id,)).fetchall()
    n, fs, fe, portrait = largest_face_aggregate(rows)
    conn.execute(
        "UPDATE images SET n_faces=?, face_sharp=?, face_expr=?, portrait=? WHERE id=?",
        (n, fs, fe, portrait, image_id))


def reanchor_cluster(conn, cid: int) -> None:
    """Re-pin a cluster's current name across all of its faces. Called after
    membership changes (merge/assign) so the name follows the cluster on a
    rebuild and stale source-cluster anchors on moved faces are cleared."""
    row = conn.execute("SELECT name FROM clusters WHERE cluster_id=?", (cid,)).fetchone()
    photodb.set_cluster_name_anchors(conn, cid, row[0] if row else None)


def _face_rows(conn, face_ids) -> list:
    ids = list(dict.fromkeys(int(i) for i in face_ids))
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT f.id, f.x1, f.y1, f.x2, f.y2, f.image_id, i.content_hash
            FROM faces f JOIN images i ON i.id = f.image_id
            WHERE f.id IN ({ph})""", ids).fetchall()
    if len(rows) != len(ids):
        raise FaceNotFound("face not found")
    return rows


def assign_faces(conn, face_ids, *, cluster_id=None, new_person=False,
                 name=None) -> tuple[int, int]:
    """Move faces to a person: an existing `cluster_id`, or a fresh identity
    (`new_person`, optionally named). Each move is recorded as a persistent
    override so it survives re-ingest. Returns (target cluster id, moved)."""
    rows = _face_rows(conn, face_ids)
    if new_person:
        target = photodb.next_manual_cluster_id(conn)
        conn.execute("INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
                     (target, name or None))
    elif cluster_id is None:
        raise ValueError("cluster_id or new_person required")
    else:
        target = int(cluster_id)
        conn.execute("INSERT OR IGNORE INTO clusters (cluster_id, name) VALUES (?, NULL)",
                     (target,))
    # Resolved before anything moves: the reference must be a face the detector
    # itself put in the target, never one of the faces being moved in.
    target_ref = photodb.cluster_reference_face(
        conn, target, exclude_face_ids={r[0] for r in rows})
    for fid, x1, y1, x2, y2, _img, h in rows:
        conn.execute("UPDATE faces SET cluster_id=? WHERE id=?", (target, fid))
        photodb.record_face_override(conn, h, (x1, y1, x2, y2), "assign", target, target_ref)
    # Keep the target's name anchors in step with its new membership.
    reanchor_cluster(conn, target)
    return target, len(rows)


def delete_faces(conn, face_ids) -> int:
    """Drop false-positive face boxes, re-aggregate their photos, and record
    persistent overrides so they stay deleted across re-ingest."""
    rows = _face_rows(conn, face_ids)
    for fid, x1, y1, x2, y2, _img, h in rows:
        conn.execute("DELETE FROM faces WHERE id=?", (fid,))
        photodb.record_face_override(conn, h, (x1, y1, x2, y2), "delete")
    for img in {r[5] for r in rows}:
        reaggregate_faces(conn, img)
    return len(rows)


# ── Embeddings (read-only view of the analyze sidecar) ───────────────────────

_EMB_CACHE: dict[str, tuple] = {}


def load_face_embeddings(path: Path) -> dict:
    """{(content_hash, bbox_key): unit vector} from the analyze sidecar, or {}
    when there is none (faces never analyzed, or the report lives elsewhere).
    Cached per file modification time."""
    try:
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return {}
    hit = _EMB_CACHE.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    out: dict = {}
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for h, b, dim, blob in conn.execute(
                    "SELECT hash, bbox, dim, emb FROM face_embeddings"):
                v = np.frombuffer(blob, dtype=np.float32, count=dim)
                n = float(np.linalg.norm(v))
                if n > 0:
                    out[(h, b)] = v / n
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    _EMB_CACHE[str(path)] = (stamp, out)
    return out


def _centroid(vectors):
    c = np.mean(vectors, axis=0)
    n = float(np.linalg.norm(c))
    return c / n if n > 0 else None


def _cluster_vectors(conn, embeddings: dict) -> dict:
    """{cluster_id: [unit vectors]} for every non-trashed face with a cached
    embedding."""
    out: dict = {}
    if not embeddings:
        return out
    for cid, h, x1, y1, x2, y2 in conn.execute(
            f"""SELECT f.cluster_id, i.content_hash, f.x1, f.y1, f.x2, f.y2
                FROM faces f JOIN images i ON i.id = f.image_id
                LEFT JOIN {TRASH_ON} WHERE tm.id IS NULL"""):
        v = embeddings.get((h, bbox_key(x1, y1, x2, y2)))
        if v is not None and cid is not None and cid >= 0:
            out.setdefault(cid, []).append(v)
    return out


def similar_people(conn, embeddings: dict, cluster_id: int, limit: int = 6) -> list[dict]:
    """Other people whose faces look like this person's — merge candidates,
    best first. Empty when embeddings aren't available."""
    vecs = _cluster_vectors(conn, embeddings)
    if cluster_id not in vecs:
        return []
    mine = _centroid(vecs[cluster_id])
    scored = []
    for cid, vs in vecs.items():
        if cid == cluster_id:
            continue
        c = _centroid(vs)
        if mine is None or c is None:
            continue
        sim = float(mine @ c)
        if sim >= SUGGEST_MIN_SIM:
            scored.append((sim, cid))
    scored.sort(reverse=True)
    wanted = {cid: sim for sim, cid in scored[:limit]}
    people = {p["cluster_id"]: p for p in list_people(conn)}
    return [{**people[cid], "similarity": round(sim, 3)}
            for cid, sim in wanted.items() if cid in people]


# ── Reads ────────────────────────────────────────────────────────────────────

def _face_quality(sharp, expr, rel) -> float:
    """How good a *picture of the person* a face crop is: sharp first, then big
    enough to see, then a pleasant expression. Picks covers and the default
    sort; not a stored score."""
    s = 0.5 if sharp is None else sharp
    e = 0.5 if expr is None else expr
    size = min(1.0, (rel or 0.0) / 0.10)
    return round(0.55 * s + 0.25 * size + 0.20 * e, 4)


def list_people(conn) -> list[dict[str, Any]]:
    """One row per person with at least one face in the active library: counts
    plus the best face to use as a cover. Named people first, then by size."""
    names = dict(conn.execute("SELECT cluster_id, name FROM clusters").fetchall())
    people: dict[int, dict] = {}
    for r in conn.execute(
            f"""SELECT f.id, f.cluster_id, f.image_id, f.x1, f.x2, f.sharp, f.expr,
                       i.imgw, i.content_hash
                FROM faces f JOIN images i ON i.id = f.image_id
                LEFT JOIN {TRASH_ON} WHERE tm.id IS NULL"""):
        fid, cid, img, x1, x2, sharp, expr, imgw, h = r
        cid = -1 if cid is None or cid < 0 else cid
        p = people.setdefault(cid, {
            "cluster_id": cid, "name": names.get(cid), "faces": 0, "_images": set(),
            "manual": cid >= MANUAL_CLUSTER_BASE,
            "cover_face_id": None, "cover_hash": None, "_best": -1.0})
        p["faces"] += 1
        p["_images"].add(img)
        q = _face_quality(sharp, expr, (x2 - x1) / imgw if imgw else None)
        if q > p["_best"]:
            p["_best"], p["cover_face_id"], p["cover_hash"] = q, fid, h
    out = []
    for p in people.values():
        p["photos"] = len(p.pop("_images"))
        p.pop("_best")
        if isinstance(p["name"], str) and not p["name"].strip():
            p["name"] = None
        out.append(p)
    out.sort(key=lambda p: (p["cluster_id"] < 0, p["name"] is None, -p["faces"],
                            p["cluster_id"]))
    return out


def person_faces(conn, cluster_id: int, *, sort: str = "quality", offset: int = 0,
                 limit: int = 200, embeddings: dict | None = None) -> dict[str, Any]:
    """Every face of one person (active library only), each with its photo's
    identity, so the UI can show the crop and act on the face or the photo.
    `match` is the face's cosine to the person's centroid — low values are the
    likeliest mis-assignments — or None without cached embeddings."""
    where = "f.cluster_id < 0 OR f.cluster_id IS NULL" if cluster_id < 0 else "f.cluster_id = ?"
    params = [] if cluster_id < 0 else [cluster_id]
    rows = conn.execute(
        f"""SELECT f.id, f.image_id, f.x1, f.y1, f.x2, f.y2, f.prob, f.sharp, f.expr,
                   f.cluster_id, i.content_hash, i.filename, i.path, i.imgw, i.imgh,
                   i.capture_time, ({DECISION}) AS decision
            FROM faces f JOIN images i ON i.id = f.image_id
            {DEC_JOIN} LEFT JOIN {TRASH_ON}
            WHERE tm.id IS NULL AND ({where})""", params).fetchall()

    items = []
    for r in rows:
        (fid, img, x1, y1, x2, y2, prob, sharp, expr, cid, h, filename, path,
         imgw, imgh, ctime, decision) = r
        rel = (x2 - x1) / imgw if imgw else None
        items.append({
            "id": fid, "image_id": img, "hash": h, "filename": filename, "path": path,
            "bbox": [x1, y1, x2, y2], "imgw": imgw, "imgh": imgh,
            "prob": prob, "sharp": sharp, "expr": expr, "cluster_id": cid,
            "rel": None if rel is None else round(rel, 4),
            "quality": _face_quality(sharp, expr, rel),
            "capture_time": ctime, "decision": decision, "match": None,
        })

    if embeddings and cluster_id >= 0:
        vecs = [embeddings.get((it["hash"], bbox_key(*it["bbox"]))) for it in items]
        have = [v for v in vecs if v is not None]
        centroid = _centroid(have) if len(have) >= 2 else None
        if centroid is not None:
            for it, v in zip(items, vecs):
                if v is not None:
                    it["match"] = round(float(v @ centroid), 3)

    if sort == "date":
        items.sort(key=lambda it: (it["capture_time"] is None, it["capture_time"] or 0, it["id"]))
    elif sort == "size":
        items.sort(key=lambda it: (-(it["rel"] or 0), it["id"]))
    elif sort == "match":           # least like the rest first: review order
        items.sort(key=lambda it: (it["match"] is None, it["match"] or 0, it["id"]))
    else:
        items.sort(key=lambda it: (-it["quality"], it["id"]))

    name = conn.execute("SELECT name FROM clusters WHERE cluster_id=?",
                        (cluster_id,)).fetchone()
    return {"cluster_id": cluster_id, "name": name[0] if name else None,
            "total": len(items), "offset": offset, "limit": limit,
            "has_match": any(it["match"] is not None for it in items),
            "items": items[offset:offset + limit]}
