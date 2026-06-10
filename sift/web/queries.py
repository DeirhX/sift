#!/usr/bin/env python3
"""
queries.py — the read layer for the photo-audit API.

Pure SQL → DTO helpers shared by the route handlers in ``server.py``. Every
function takes an open sqlite3 connection (with ``row_factory = sqlite3.Row``)
and never touches server globals or the FastAPI app, so they are trivially
unit-testable and reusable across endpoints. Anything that builds the JSON the
frontend consumes lives here; anything that mutates state or serves bytes stays
in ``server.py``.
"""
import os


# Decisions are keyed by content hash, so every query that needs a photo's
# keep/del state joins on it the same way. One definition so the join key can't
# drift across the half-dozen queries that use it.
DEC_ON = "decisions d ON d.hash = i.content_hash"
TRASH_ON = ("trash_moves tm ON tm.image_id = i.id "
            "AND tm.state IN ('trashed', 'emptied', 'missing')")

# Whitelisted sort columns for /api/images. Keys are the public API values;
# values are the SQL expressions (aliased to `i`, the images table).
SORT_COLUMNS = {
    "combined":  "i.combined",
    "sharpness": "i.sharpness",
    "aesthetic": "COALESCE(i.para_aesthetic, i.clip_iqa)",
    "portrait":  "i.portrait",
    "filename":  "i.filename",
}


def _parse_tokens(raw, allowed: set[str]) -> set[str]:
    """Lower-cased token set from a comma-separated query value, keeping only
    `allowed` tokens. Tolerates None, surrounding spaces, and the legacy single
    values that predate the multi-select filters."""
    if not raw:
        return set()
    return {t.strip().lower() for t in raw.split(",")
            if t.strip().lower() in allowed}


def has_fts(conn) -> bool:
    """True when the FTS5 mirror table exists (full-text caption search)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images_fts'"
    ).fetchone()
    return row is not None


def histogram(conn, expr: str, bins: int = 24) -> list[int]:
    """Count rows of `expr` into `bins` equal buckets across the [0,1] domain.
    Values are clamped into [0,1]; NULLs are ignored."""
    counts = [0] * bins
    rows = conn.execute(
        f"SELECT {expr} AS v FROM images WHERE {expr} IS NOT NULL")
    for r in rows:
        v = r["v"]
        if v < 0:
            v = 0.0
        elif v > 1:
            v = 1.0
        b = int(v * bins)
        if b >= bins:
            b = bins - 1
        counts[b] += 1
    return counts


def folder_sep(folder: str) -> str:
    """Path separator embedded in `folder`. Falls back to the server's native
    separator for degenerate inputs like a bare drive letter ("E:")."""
    if "\\" in folder:
        return "\\"
    if "/" in folder:
        return "/"
    return os.sep


def image_where(conn, *, score_min, score_max, sharp_min, sharp_max,
                aes_min, aes_max, tags, people, q, decision,
                trash="active",
                portrait_min=0.0, portrait_max=1.0,
                folder=None, folder_recursive=True):
    """Build the shared image-level WHERE clauses + params used by both
    /api/images and /api/groups. Clauses reference alias `i` (images),
    `d` (decisions LEFT JOIN on content hash) and `tm` (trash_moves LEFT JOIN).

    Composition law: clauses across categories are AND'd; within a multi-value
    category the chosen values are combined per that category's operator below.

      Score sliders (combined, sharpness, aesthetic, portrait) — ONE uniform NULL
        rule so they behave identically: a non-default range [lo,hi] keeps only
        rows that HAVE that score and fall inside it; a full [0,1] range adds no
        clause at all (so it never drops unscored rows). No more three different
        NULL policies across the four sliders.
      `tags`   — AND within: a photo must carry EVERY selected tag.
      `people` — OR within: a photo must contain ANY selected person/cluster.
      `decision` — verdicts: any of keep | del | none(=unmarked), OR'd;
        empty/'all' (and selecting all three) = any.
      `trash`    — lifecycle: any of active(library) | trashed, OR'd;
        empty = active only.
    The two status axes are orthogonal and compose freely, e.g. trash='trashed'
    + decision='keep,del' = keep- or del-marked files that are in Trash. (The old
    single control conflated these and, as a bug, let 'keep' leak trashed rows;
    both are fixed by the split.)"""
    where: list[str] = []
    params: list = []

    # Score ranges share one rule so they compose predictably: a non-default
    # range constrains AND requires the score to exist; a full [0,1] range is a
    # no-op. Pairing clause+params here keeps their order in lockstep.
    def add_range(col: str, lo: float, hi: float) -> None:
        if lo > 0.0 or hi < 1.0:
            where.append(f"({col} IS NOT NULL AND {col} BETWEEN ? AND ?)")
            params.extend([lo, hi])

    add_range("i.combined", score_min, score_max)
    add_range("i.sharpness", sharp_min, sharp_max)
    add_range("i.para_aesthetic", aes_min, aes_max)
    add_range("i.portrait", portrait_min, portrait_max)

    # Folder filter: prefix-match the stored path. Recursive matches everything
    # below `folder`; non-recursive additionally requires no further separator
    # after the prefix, i.e. the file sits directly in `folder`. Done with
    # substr/instr (not LIKE) to sidestep LIKE's %/_ wildcard escaping across
    # both Windows and POSIX separators.
    if folder:
        sep = folder_sep(folder)
        prefix = folder.rstrip("\\/") + sep
        plen = len(prefix)
        where.append("substr(i.path, 1, ?) = ?")
        params += [plen, prefix]
        if not folder_recursive:
            where.append("instr(substr(i.path, ?), ?) = 0")
            params += [plen + 1, sep]

    # Tags are AND'd: the photo must carry every selected tag. One subquery
    # counts how many of the chosen tags it has and requires the full set.
    if tags:
        tag_list = [t for t in tags.split(",") if t]
        if tag_list:
            ph = ",".join("?" * len(tag_list))
            where.append(
                f"i.id IN (SELECT image_id FROM image_tags WHERE tag IN ({ph}) "
                f"GROUP BY image_id HAVING COUNT(DISTINCT tag) = ?)")
            params += tag_list + [len(tag_list)]

    if people:
        cids = [int(c) for c in people.split(",") if c.lstrip("-").isdigit()]
        if cids:
            ph = ",".join("?" * len(cids))
            where.append(f"i.id IN (SELECT image_id FROM faces WHERE cluster_id IN ({ph}))")
            params += cids

    # Decision axis — a multi-select verdict partition. The value is a comma list
    # of any of keep / del / none(=unmarked); the chosen verdicts are OR'd. An
    # empty set (or the legacy 'all') — and selecting every verdict — adds no
    # constraint. Legacy single values (keep|del|unmarked|new) still parse.
    verdicts = _parse_tokens(decision, {"keep", "del", "none", "unmarked", "new"})
    want_keep = "keep" in verdicts
    want_del = "del" in verdicts
    want_none = bool(verdicts & {"none", "unmarked", "new"})
    chosen = [on for on in (want_keep, want_del, want_none) if on]
    if chosen and len(chosen) < 3:   # a strict subset constrains; full set = all
        ors = []
        if want_keep:
            ors.append("d.decision = 'keep'")
        if want_del:
            ors.append("d.decision = 'del'")
        if want_none:
            ors.append("d.decision IS NULL")
        where.append("(" + " OR ".join(ors) + ")")

    # Lifecycle axis — a multi-select of active(library) / trashed, OR'd. Legacy
    # 'any' spans everything (incl. emptied/missing); an empty set defaults to
    # active so the grid isn't haunted by recoverable JPEGs unless asked.
    states = _parse_tokens(trash, {"active", "trashed", "any"})
    if "any" in states:
        pass                                   # no lifecycle constraint
    elif "active" in states and "trashed" in states:
        where.append("(tm.id IS NULL OR tm.state = 'trashed')")
    elif "trashed" in states:
        where.append("tm.state = 'trashed'")
    else:                                       # {'active'} or empty → library
        where.append("tm.id IS NULL")

    if q:
        if has_fts(conn):
            where.append("i.id IN (SELECT rowid FROM images_fts WHERE images_fts MATCH ?)")
            params.append(q)
        else:
            where.append("i.caption LIKE ?")
            params.append(f"%{q}%")

    return where, params


def rows_to_items(conn, rows) -> list[dict]:
    """Attach face boxes + tags to a batch of image rows and shape them
    into the JSON payload used by /api/images and /api/groups."""
    ids = [r["id"] for r in rows]
    faces_by_img: dict[int, list] = {i: [] for i in ids}
    tags_by_img:  dict[int, list] = {i: [] for i in ids}
    if ids:
        ph = ",".join("?" * len(ids))
        for fr in conn.execute(
            f"""SELECT id, image_id, x1, y1, x2, y2, prob, cluster_id, sharp, expr
                FROM faces WHERE image_id IN ({ph})""", ids):
            faces_by_img[fr["image_id"]].append({
                "id": fr["id"],
                "bbox": [fr["x1"], fr["y1"], fr["x2"], fr["y2"]],
                "prob": fr["prob"], "cluster_id": fr["cluster_id"],
                "sharp": fr["sharp"], "expr": fr["expr"],
            })
        for tr in conn.execute(
            f"SELECT image_id, tag FROM image_tags WHERE image_id IN ({ph})", ids):
            tags_by_img[tr["image_id"]].append(tr["tag"])

    items = []
    for r in rows:
        d = dict(r)
        items.append({
            "id": d["id"], "filename": d["filename"], "path": d["path"],
            "hash": d["content_hash"],
            "combined": d["combined"], "sharpness": d["sharpness"],
            "para_aesthetic": d["para_aesthetic"],
            "para_quality": d["para_quality"],
            "para_composition": d["para_composition"],
            "para_light": d["para_light"],
            "para_color": d["para_color"],
            "para_dof": d["para_dof"],
            "para_content": d["para_content"],
            "clip_iqa": d["clip_iqa"],
            "dup_group": d["dup_group"],
            "dup_central": d.get("dup_central"),
            "scene_group": d.get("scene_group"),
            "capture_time": d.get("capture_time"),
            "caption": d["caption"],
            "imgw": d["imgw"], "imgh": d["imgh"],
            "face_sharp": d.get("face_sharp"), "face_expr": d.get("face_expr"),
            "portrait": d.get("portrait"),
            "decision": d.get("decision"),
            "trash_state": d.get("trash_state"),
            "original_path": d.get("original_path"),
            "trashed_at": d.get("trashed_at"),
            "faces": faces_by_img.get(d["id"], []),
            "tags": tags_by_img.get(d["id"], []),
        })
    return items


def grouped_page(conn, group_col, where, params, order_sql, offset, limit,
                 agg_extra=""):
    """Shared skeleton for /api/groups and /api/scenes.

    Pages over a grouping column (`dup_group` or `scene_group`): a group is
    included when at least one member passes the filter `where`, but every
    member is returned (best-first) with an added `matches` flag so the UI can
    grey out members outside the filter. Returns (total, [(group_row, items)]);
    callers add their own per-group metadata. `agg_extra` injects extra aggregate
    columns into the GROUP BY query (e.g. scene time span), exposed on group_row.
    """
    where = where + [f"i.{group_col} IS NOT NULL"]
    where_sql = " AND ".join(where)

    # Groups with at least one member passing the filters.
    qual = (f"SELECT DISTINCT i.{group_col} FROM images i "
            f"LEFT JOIN {DEC_ON} LEFT JOIN {TRASH_ON} WHERE {where_sql}")
    total = conn.execute(f"SELECT COUNT(*) FROM ({qual})", params).fetchone()[0]

    grp_rows = conn.execute(
        f"""SELECT {group_col}, COUNT(*) c{agg_extra}
            FROM images WHERE {group_col} IN ({qual})
            GROUP BY {group_col}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()

    page_gids = [gr[group_col] for gr in grp_rows]
    match_ids: set[int] = set()
    if page_gids:
        ph = ",".join("?" * len(page_gids))
        match_ids = {r[0] for r in conn.execute(
            f"SELECT i.id FROM images i LEFT JOIN {DEC_ON} LEFT JOIN {TRASH_ON} "
            f"WHERE {where_sql} AND i.{group_col} IN ({ph})", params + page_gids)}

    page = []
    for gr in grp_rows:
        members = conn.execute(
            f"""SELECT i.*, d.decision, tm.state AS trash_state,
                       tm.from_path AS original_path, tm.trashed_at
                FROM images i LEFT JOIN {DEC_ON} LEFT JOIN {TRASH_ON}
                WHERE i.{group_col} = ? ORDER BY i.combined DESC, i.id ASC""",
            (gr[group_col],),
        ).fetchall()
        items = rows_to_items(conn, members)
        for it in items:
            it["matches"] = it["id"] in match_ids
        page.append((gr, items))
    return total, page
