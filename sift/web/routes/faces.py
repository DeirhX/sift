"""Routes for editing people from the sidebar and photo cards: rename or merge a
person (cluster), reassign one face, delete one face box. The People view's
batch edits are routes/people.py; both delegate to face_ops."""
from typing import Callable

from fastapi import APIRouter, Body, HTTPException

from sift.web import face_ops, photodb
from sift.web.schemas import AssignFaceResponse, MergeResponse, OkResponse

DbFactory = Callable[[], object]


def create_router(db_factory: DbFactory) -> APIRouter:
    router = APIRouter()

    @router.post("/api/clusters", response_model=OkResponse)
    def rename_cluster(payload: dict = Body(...)):
        cid = payload.get("cluster_id")
        name = payload.get("name")
        if cid is None:
            raise HTTPException(400, "cluster_id required")
        with db_factory() as conn:
            conn.execute("INSERT OR REPLACE INTO clusters (cluster_id, name) VALUES (?,?)",
                         (int(cid), name or None))
            # Anchor the name to this cluster's faces so it survives re-clustering.
            photodb.set_cluster_name_anchors(conn, int(cid), name or None)
            conn.commit()
        return {"ok": True}


    @router.post("/api/clusters/merge", response_model=MergeResponse)
    def merge_clusters(payload: dict = Body(...)):
        """Reassign every face of the `from` cluster(s) into `into`, then drop the
        now-empty source cluster name rows. Pure DB edit — no re-embedding. Each
        moved face is also recorded as a persistent override."""
        into = payload.get("into")
        frm = payload.get("from")
        if into is None or frm is None:
            raise HTTPException(400, "from and into required")
        into = int(into)
        if isinstance(frm, (int, str)):
            frm = [frm]
        frm = [int(c) for c in frm if int(c) != into]
        if not frm:
            return {"ok": True, "moved": 0}
        with db_factory() as conn:
            ph = ",".join("?" * len(frm))
            # Record an override per moved face before the cluster_id changes.
            moving = conn.execute(
                f"""SELECT i.content_hash AS h, f.x1, f.y1, f.x2, f.y2
                    FROM faces f JOIN images i ON i.id = f.image_id
                    WHERE f.cluster_id IN ({ph})""", frm).fetchall()
            # Pinned to a face the detector put in `into`, so the merge still lands
            # on the same person after re-clustering renumbers the ids.
            target_ref = photodb.cluster_reference_face(conn, into)
            for r in moving:
                photodb.record_face_override(
                    conn, r["h"], (r["x1"], r["y1"], r["x2"], r["y2"]),
                    "assign", into, target_ref)
            cur = conn.execute(
                f"UPDATE faces SET cluster_id=? WHERE cluster_id IN ({ph})", [into] + frm)
            moved = cur.rowcount
            conn.execute(f"DELETE FROM clusters WHERE cluster_id IN ({ph})", frm)
            conn.execute("INSERT OR IGNORE INTO clusters (cluster_id, name) VALUES (?, NULL)",
                         (into,))
            # Re-anchor the destination to its current name across all of its faces
            # (now including the moved ones); this also clears the moved faces' old
            # source-cluster anchors so a stale name can't resurface on rebuild.
            face_ops.reanchor_cluster(conn, into)
            conn.commit()
        return {"ok": True, "moved": moved}


    @router.post("/api/faces/{face_id}/assign", response_model=AssignFaceResponse)
    def assign_face(face_id: int, payload: dict = Body(...)):
        """Move one face box to a different person. Either pass an existing
        `cluster_id`, or `new_person: true` (with optional `name`) to spin up a
        fresh identity. Recorded as a persistent override so it survives re-ingest."""
        if not payload.get("new_person") and payload.get("cluster_id") is None:
            raise HTTPException(400, "cluster_id or new_person required")
        with db_factory() as conn:
            try:
                target, _ = face_ops.assign_faces(
                    conn, [face_id], cluster_id=payload.get("cluster_id"),
                    new_person=bool(payload.get("new_person")), name=payload.get("name"))
            except face_ops.FaceNotFound:
                raise HTTPException(404, "face not found")
            conn.commit()
        return {"ok": True, "cluster_id": target}


    @router.delete("/api/faces/{face_id}", response_model=OkResponse)
    def delete_face(face_id: int):
        """Drop a false-positive face box, decrement the image's face count, and
        record a persistent override so it stays deleted across re-ingest."""
        with db_factory() as conn:
            try:
                face_ops.delete_faces(conn, [face_id])
            except face_ops.FaceNotFound:
                raise HTTPException(404, "face not found")
            conn.commit()
        return {"ok": True}

    return router
