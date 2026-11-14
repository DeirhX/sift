"""Routes behind the People view: people, one person's faces, face crops, and
batch face edits. Config (thumbnail dir, DB path) is injected as callables
because tests and dev-reload repoint the server's globals at runtime."""
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from sift.web import face_ops
from sift.web.photodb import bbox_key
from sift.web.schemas import (
    AssignFacesRequest,
    AssignFacesResponse,
    DeleteFacesResponse,
    FaceBatchRequest,
    PeopleResponse,
    PersonFacesResponse,
)

DbFactory = Callable[[], object]

# Face crops are cut this much wider than the detected box (which hugs the
# face), so a tile shows the head, not a mask.
CROP_MARGIN = 1.6
CROP_SIZES = (96, 160, 320)


def face_crop_box(box, width: int, height: int) -> tuple[int, int, int, int]:
    """Square crop around a face box, expanded by CROP_MARGIN and shifted (not
    squashed) to stay inside the image."""
    x1, y1, x2, y2 = box
    edge = min(max(x2 - x1, y2 - y1) * CROP_MARGIN, width, height)
    left = min(max(0.0, (x1 + x2) / 2 - edge / 2), width - edge)
    top = min(max(0.0, (y1 + y2) / 2 - edge / 2), height - edge)
    return round(left), round(top), round(left + edge), round(top + edge)


def create_router(db_factory: DbFactory, *, thumb_dir: Callable[[], Path],
                  db_path: Callable[[], Path]) -> APIRouter:
    router = APIRouter()

    def embeddings() -> dict:
        # The sidecar sits next to the report; by default that's the DB's folder.
        return face_ops.load_face_embeddings(db_path().parent / ".embeddings.sqlite")

    @router.get("/api/people", response_model=PeopleResponse)
    def people():
        with db_factory() as conn:
            return {"people": face_ops.list_people(conn)}

    @router.get("/api/people/{cluster_id}/faces", response_model=PersonFacesResponse)
    def person_faces(cluster_id: int, sort: str = "quality", offset: int = 0,
                     limit: int = Query(200, ge=1, le=1000)):
        if sort not in face_ops.FACE_SORTS:
            raise HTTPException(400, f"sort must be one of {sorted(face_ops.FACE_SORTS)}")
        embs = embeddings()
        with db_factory() as conn:
            out = face_ops.person_faces(conn, cluster_id, sort=sort, offset=max(0, offset),
                                        limit=limit, embeddings=embs)
            out["similar"] = (face_ops.similar_people(conn, embs, cluster_id)
                              if cluster_id >= 0 else [])
        return out

    @router.post("/api/faces/assign", response_model=AssignFacesResponse)
    def assign_faces(req: AssignFacesRequest):
        if not req.new_person and req.cluster_id is None:
            raise HTTPException(400, "cluster_id or new_person required")
        with db_factory() as conn:
            try:
                target, moved = face_ops.assign_faces(
                    conn, req.face_ids, cluster_id=req.cluster_id,
                    new_person=req.new_person, name=req.name)
            except face_ops.FaceNotFound:
                raise HTTPException(404, "face not found; refresh and try again")
            conn.commit()
        return {"ok": True, "cluster_id": target, "moved": moved}

    @router.post("/api/faces/delete", response_model=DeleteFacesResponse)
    def delete_faces(req: FaceBatchRequest):
        with db_factory() as conn:
            try:
                deleted = face_ops.delete_faces(conn, req.face_ids)
            except face_ops.FaceNotFound:
                raise HTTPException(404, "face not found; refresh and try again")
            conn.commit()
        return {"ok": True, "deleted": deleted}

    @router.get("/face/{face_id}")
    def face_crop(face_id: int, size: int = 160):
        """A square, upright crop of one face. Cut from the original on first
        request and cached on disk by (content hash, box, size) — a key that,
        unlike the face id, survives re-indexing."""
        if size not in CROP_SIZES:
            raise HTTPException(400, f"size must be one of {list(CROP_SIZES)}")
        with db_factory() as conn:
            row = conn.execute(
                """SELECT f.x1, f.y1, f.x2, f.y2, i.path, i.content_hash
                   FROM faces f JOIN images i ON i.id = f.image_id WHERE f.id=?""",
                (face_id,)).fetchone()
            coords = conn.execute("SELECT value FROM meta WHERE key='coords'").fetchone()
        if not row:
            raise HTTPException(404, "face not found")
        box, path, content_hash = tuple(row[:4]), Path(row[4]), row[5]

        cache = None
        if content_hash:
            key = bbox_key(*box).replace(",", "_")
            cache = thumb_dir() / "faces" / f"{content_hash}_{key}_{size}.webp"
        headers = {"Cache-Control": "public, max-age=86400"}
        if cache is not None and cache.exists():
            return FileResponse(cache, media_type="image/webp", headers=headers)
        if not path.exists():
            raise HTTPException(404, "original file missing")

        try:
            from PIL import Image, ImageOps
            from sift.audit.imaging import (exif_orientation, load_rgb, load_upright,
                                            upright_box)
            if not (coords and coords[0] == "upright"):
                # Library not re-analyzed since boxes became upright: this box is
                # still in raw sensor coordinates. Bring it into the upright frame.
                with load_rgb(path) as raw:
                    box = upright_box(box, raw.size, exif_orientation(raw))
            img = load_upright(path)
            crop = img.crop(face_crop_box(box, img.width, img.height))
            crop = ImageOps.fit(crop, (size, size), Image.Resampling.LANCZOS)
        except Exception as e:
            raise HTTPException(500, f"failed to cut face crop: {e}")

        if cache is None:
            from io import BytesIO
            from fastapi import Response
            buf = BytesIO()
            crop.save(buf, "WEBP", quality=85)
            return Response(buf.getvalue(), media_type="image/webp", headers=headers)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{face_id}.tmp")
        crop.save(tmp, "WEBP", quality=85)
        tmp.replace(cache)                      # atomic: concurrent requests race safely
        return FileResponse(cache, media_type="image/webp", headers=headers)

    return router
