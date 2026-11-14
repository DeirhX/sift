"""Routes for keep / Trash marks: setting them (single or guarded batch),
exporting and importing them as JSON, and auto-culling duplicate groups.
Moving files on the strength of those marks is routes/trash.py."""
from typing import Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from sift.web import library_ops
from sift.web.queries import DEC_JOIN, DECISION, TRASH_ON
from sift.web.schemas import AutocullResponse, OkResponse

DbFactory = Callable[[], object]


def create_router(db_factory: DbFactory) -> APIRouter:
    router = APIRouter()

    @router.post("/api/decisions", response_model=OkResponse)
    def set_decision(payload: dict = Body(...)):
        updates = payload.get("updates", [payload])
        if not isinstance(updates, list) or any(not isinstance(u, dict) for u in updates):
            raise HTTPException(400, "expected a list of decision updates")
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for update in updates:
                h, path, image_id = update.get("hash"), update.get("path"), update.get("id")
                decision = update.get("decision")
                if decision not in ("keep", "del", None):
                    raise HTTPException(400, "invalid decision")
                if image_id is not None:
                    row = conn.execute(
                        f"""SELECT i.path, i.content_hash, ({DECISION}) AS decision, tm.state AS trash_state
                            FROM images i {DEC_JOIN} LEFT JOIN {TRASH_ON} WHERE i.id=?""",
                        (image_id,)).fetchone()
                    if not row or row["content_hash"] != h or (path and row["path"] != path):
                        raise HTTPException(409, "photo changed, refresh and try again")
                    path = row["path"]
                    if "expected_decision" in update and (
                        row["decision"] != update["expected_decision"] or row["trash_state"] is not None
                    ):
                        raise HTTPException(409, "Marks changed. Refresh the preview and try again.")
                    if update.get("guard_only") and "expected_decision" in update:
                        continue
                elif not h and path:
                    row = conn.execute("SELECT content_hash FROM images WHERE path=?", (path,)).fetchone()
                    h = row["content_hash"] if row else None
                if not h:
                    raise HTTPException(400, "hash required")
                library_ops.write_decision(conn, h, decision, path)
            conn.commit()
        return {"ok": True}



    @router.get("/api/export")
    def export_decisions():
        with db_factory() as conn:
            rows = conn.execute(
                f"""SELECT i.path, i.filename, i.combined, i.content_hash,
                           ({DECISION}) AS decision, cd.path AS copy_path
                   FROM images i {DEC_JOIN}""").fetchall()
        out = {"kept": [], "deleted": [], "unmarked": []}
        for r in rows:
            # `hash` is the stable key (decisions are hash-keyed); path/filename are
            # for humans and a fallback when re-importing into a moved library.
            entry = {"hash": r["content_hash"], "path": r["path"],
                     "filename": r["filename"], "combined": r["combined"]}
            if r["copy_path"] is not None:
                entry["copy_specific"] = True
            if r["decision"] == "keep":
                out["kept"].append(entry)
            elif r["decision"] == "del":
                out["deleted"].append(entry)
            else:
                out["unmarked"].append(entry)
        return JSONResponse(out, headers={
            "Content-Disposition": "attachment; filename=audit_decisions.json"})


    @router.post("/api/import")
    def import_decisions(payload: dict = Body(...)):
        """Re-apply keep/del verdicts from an exported audit_decisions.json.

        Each entry is matched by content hash (preferred) or, failing that, by path.
        Additive and non-destructive: it overwrites marks only for hashes present in
        the file and leaves every other existing mark untouched. Unmarked entries
        apply only when they explicitly override a copy's inherited decision.
        A hash that isn't in the
        current catalog is still recorded — it binds if that photo is indexed later,
        exactly like decisions preserved across a rebuild."""
        if "kept" not in payload and "deleted" not in payload:
            raise HTTPException(400, "unrecognized export: expected 'kept'/'deleted' lists")
        buckets = (("keep", payload.get("kept") or []),
                   ("del", payload.get("deleted") or []),
                   (None, payload.get("unmarked") or []))
        applied = 0
        unmatched = 0
        with db_factory() as conn:
            for decision, entries in buckets:
                for e in entries:
                    if decision is None and (not isinstance(e, dict) or not e.get("copy_specific")):
                        continue
                    if not isinstance(e, dict):
                        unmatched += 1
                        continue
                    h = e.get("hash") or e.get("content_hash")
                    if e.get("copy_specific"):
                        row = conn.execute(
                            "SELECT 1 FROM images WHERE path=? AND content_hash=?",
                            (e.get("path"), h)).fetchone()
                        if row:
                            conn.execute(
                                "INSERT OR REPLACE INTO copy_decisions (path, hash, decision) VALUES (?,?,?)",
                                (e["path"], h, decision))
                            applied += 1
                        else:
                            unmatched += 1
                        continue
                    if not h and e.get("path"):
                        row = conn.execute(
                            "SELECT content_hash FROM images WHERE path=?",
                            (e["path"],)).fetchone()
                        h = row["content_hash"] if row else None
                    if not h:
                        unmatched += 1
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO decisions (hash, decision) VALUES (?,?)",
                        (h, decision))
                    applied += 1
            conn.commit()
        return {"ok": True, "applied": applied, "unmatched": unmatched}



    @router.get("/api/groups/autocull/preview")
    def preview_autocull_groups():
        with db_factory() as conn:
            conn.execute("BEGIN")
            return library_ops.preview_autocull(conn)


    @router.post("/api/groups/autocull", response_model=AutocullResponse)
    def autocull_groups():
        """Mark exact copies and clear winners in tight, active duplicate sets."""
        with db_factory() as conn:
            return library_ops.autocull_duplicates(conn)

    return router
