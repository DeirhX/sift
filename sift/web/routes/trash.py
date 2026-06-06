from typing import Callable

from fastapi import APIRouter

from sift.web import library_ops
from sift.web.schemas import (
    ApplyResponse,
    ApplyStatusResponse,
    TrashListResponse,
    TrashStatusResponse,
    UndoResponse,
)

DbFactory = Callable[[], object]


def create_router(db_factory: DbFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/apply/status", response_model=ApplyStatusResponse)
    def apply_status():
        with db_factory() as conn:
            trash = library_ops.trash_dir(conn)
            counts = library_ops.trash_counts(conn)
            trash_str = str(trash)
        return {"pending": counts["pending"], "applied": counts["trashed"],
                "trashed": counts["trashed"], "trash_dir": trash_str,
                "rejected_dir": trash_str}

    @router.get("/api/trash/status", response_model=TrashStatusResponse)
    def trash_status():
        with db_factory() as conn:
            trash = library_ops.trash_dir(conn)
            counts = library_ops.trash_counts(conn)
        return {**counts, "trash_dir": str(trash)}

    @router.get("/api/trash", response_model=TrashListResponse)
    def trash_list():
        with db_factory() as conn:
            items = library_ops.list_trash(conn)
        return {"total": len(items), "items": items}

    @router.post("/api/apply", response_model=ApplyResponse)
    def apply_decisions():
        """Compatibility endpoint for moving 'del' photos into Trash.

        The web UI uses task type `trash_decisions`; this route stays for older
        clients and focused API tests.
        """
        with db_factory() as conn:
            return library_ops.trash_decisions(conn)

    @router.post("/api/apply/undo", response_model=UndoResponse)
    def undo_apply():
        """Compatibility endpoint for restoring trashed photos."""
        with db_factory() as conn:
            return library_ops.restore_trash(conn)

    return router
