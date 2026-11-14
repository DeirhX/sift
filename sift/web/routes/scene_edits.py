"""Routes that reshape scenes: the granularity gap, manual merge / unmerge, and
splitting a scene at a photo. Reading scenes stays in server.py with the other
list queries; the rules themselves live in library_ops."""
from typing import Callable

from fastapi import APIRouter, HTTPException

from sift.web import library_ops
from sift.web.schemas import (
    MergeScenesRequest,
    RegroupRequest,
    RegroupResponse,
    SplitSceneRequest,
    UnmergeSceneRequest,
)

DbFactory = Callable[[], object]


def create_router(db_factory: DbFactory) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scenes/regroup", response_model=RegroupResponse)
    def regroup_scenes(req: RegroupRequest):
        """Re-segment scenes by capture-time gap only (the 'scene granularity' knob).
        Clamped to a sane range; recomputes scene_group in place and remembers the
        chosen gap so the slider re-opens where the user left it."""
        gap = max(5.0, min(86400.0, float(req.gap)))
        with db_factory() as conn:
            return library_ops.recompute_scenes(conn, gap)


    @router.post("/api/scenes/merge", response_model=RegroupResponse)
    def merge_scenes(req: MergeScenesRequest):
        """Pin two or more scenes into one (a manual merge that survives recuts and
        re-analysis). Recomputes at the current gap and returns the new scene count."""
        with db_factory() as conn:
            return library_ops.merge_scenes(conn, req.scene_groups)


    @router.post("/api/scenes/unmerge", response_model=RegroupResponse)
    def unmerge_scene(req: UnmergeSceneRequest):
        """Drop the manual-merge pins touching a scene, returning it to automatic
        time-gap segmentation."""
        with db_factory() as conn:
            return library_ops.unmerge_scene(conn, req.scene_group)


    @router.post("/api/scenes/split", response_model=RegroupResponse)
    def split_scene(req: SplitSceneRequest):
        """Split a scene before the selected photo's capture time."""
        with db_factory() as conn:
            result = library_ops.split_scene(conn, req.scene_group, req.image_id, req.hash)
        if result is None:
            raise HTTPException(409, "choose a later photo in this scene")
        return result

    return router
