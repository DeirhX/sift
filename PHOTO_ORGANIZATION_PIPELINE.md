# Photo Organization Pipeline

This document audits the part of PhotoOrganizer that decides which photos are
duplicates and the part that physically moves files. The short version: this is
a culling pipeline, not a folder reorganization pipeline. It scores and groups
photos, stores review decisions, then reversibly moves rejected originals into
`_rejected/`. It does not hard-delete photos.

## Pipeline Overview

The workflow is split into three commands behind the `sift` dispatcher:

| Stage | Entrypoint | Main files | Filesystem effect |
| --- | --- | --- | --- |
| Analyze | `sift analyze <folder>` | `sift/audit/cli.py`, `sift/audit/grouping.py` | Writes `audit_report.json`; optional `--move-junk` moves low-score files |
| Index | `sift index <audit_report.json>` | `sift/web/build_db.py`, `sift/web/photodb.py` | Writes `photos.db` and `.thumbs/*.webp`; may prune orphan thumbnails |
| Review/apply | `sift serve --db photos.db` | `sift/web/server.py`, `frontend/src/*` | Marks keep/delete decisions; Apply moves `del` files to `<library>/_rejected/` |

End-to-end:

```text
sift analyze
  -> scan image files
  -> score quality and sharpness
  -> compute perceptual hashes
  -> optionally compute CLIP embeddings
  -> assign duplicate groups and scene groups
  -> write audit_report.json

sift index
  -> read audit_report.json
  -> content-hash each image
  -> generate thumbnails under .thumbs/
  -> insert rows into photos.db
  -> preserve decisions by content hash

sift serve
  -> browser UI reviews grid, duplicate groups, and scenes
  -> decisions table records content_hash -> keep|del
  -> Apply moves del-marked originals into _rejected/
  -> Undo moves logged files back
```

## Duplicate Detection

Duplicate grouping is computed during `sift analyze` in
`sift/audit/grouping.py`.

`compute_phashes()` loads each image, applies `ImageOps.exif_transpose()`, and
computes an `imagehash.phash`. The upright transform matters: a camera-rotated
portrait should not hash as a totally different image just because EXIF was
ignored, which would be the usual clown show.

`assign_dup_groups()` then builds a similarity graph. Two images are connected
when either condition is true:

- Perceptual hash distance is less than or equal to `--dup-threshold` default
  `6`. This is time-independent and catches exact-ish re-saves, rotations after
  EXIF normalization, and some small edits.
- CLIP cosine similarity is greater than or equal to `--dup-sim` default `0.92`
  and capture times are within `--dup-window` default `10` minutes. This catches
  same-shot bursts or slight motion that phash can miss, while the time window
  keeps visually similar photos from different moments out of the same group.

The graph is reduced into connected components. For components larger than two,
when CLIP embeddings are available, `_cohesion_split()` re-clusters the component
with average linkage and stops merging below `--dup-cohesion` default `0.90`.
That prevents single-linkage chains from turning a whole shoot into one giant
"duplicate" blob. Phash-matched pairs are pinned at similarity `1.0` and are not
split apart by the cohesion pass.

Only groups with at least two members get a `dup_group`. Single images are left
with `dup_group = null`.

`dup_centrality()` computes each member's mean CLIP cosine to the other members
of its duplicate group. The UI uses this `dup_central` score to lead with the
visual medoid when embeddings exist.

## How Unwanted Duplicates Are Filtered

There are three different meanings of "filtered out" in the codebase:

1. Hidden from a view.
2. Marked as `del` in the database.
3. Physically moved to `_rejected/`.

Those are deliberately separate. A duplicate group by itself does not move any
file.

### Display Filtering

`GET /api/images?dup_mode=hide-dups` in `sift/web/server.py` keeps only one row
per duplicate group in the grid. It picks the highest `combined` score, with
`id ASC` as the tie-breaker:

```sql
i.dup_group IS NULL OR i.id = (
  SELECT id
  FROM images i2
  WHERE i2.dup_group = i.dup_group
  ORDER BY i2.combined DESC, i2.id ASC
  LIMIT 1
)
```

This does not mark or move anything. It is just a view filter.

### Decision Marking

Decisions live in the `decisions` table defined in `sift/web/photodb.py`:

```text
content_hash -> keep|del
```

Decision writes go through `POST /api/decisions` in `sift/web/server.py`. The UI
passes a content hash, not a path, so decisions survive renames and ordinary
folder moves.

Bulk duplicate culling has several paths:

- `POST /api/groups/autocull` marks every duplicate group. It keeps the highest
  `combined` score and marks the rest `del`.
- `GroupReview.tsx` "Keep best - delete rest" keeps `view[0]`. In group mode
  that list is ordered by `repFirst()`, which uses `dup_central` first and
  `combined` as a tie-breaker.
- Scene review can keep the currently selected member and mark only its
  near-duplicate siblings `del`.
- Manual `k` / `d` decisions mark a single content hash.

This means "best" is not fully consistent today. Server auto-cull means highest
quality score. Group review means most central frame first, then quality. That
can produce different keepers for the same duplicate group.

### Physical Move

`apply_decisions()` in `sift/web/server.py` is the physical exclusion step. It
selects every image joined to a `del` decision and moves files that still live
outside the rejected folder:

```text
source path -> <library>/_rejected/<unique filename>
```

The destination folder comes from the DB `meta.folder` value. Filename collisions
are handled by `_unique_dest()`, which appends `_1`, `_2`, and so on.

After a successful move:

- `images.path` is updated to the new `_rejected/` location.
- The move is logged in `applied_moves`.
- The decision remains attached to the content hash.

`undo_apply()` walks `applied_moves` in reverse order and moves files back to
their original paths when possible.

Apply skip cases:

- The source file is already under `_rejected/`.
- The source path no longer exists.
- `shutil.move()` raises `OSError`.

Skipped files are counted but not fixed automatically.

## Optional Junk Move

`sift analyze --move-junk <path>` is separate from duplicate culling. It moves
records whose `combined` score is below `--junk-threshold` default `0.25`.

This uses a flat destination of `junk_dir / src.name` and does not use
`_unique_dest()`. Name collisions can fail or behave unexpectedly depending on
the platform. Treat this path as a blunt quality filter, not the normal duplicate
workflow.

## Important Edge Cases

- Exact byte-identical copies share the same `content_hash`, and decisions are
  keyed by that hash. That is useful for preserving decisions across moves, but
  it means the system cannot represent "keep this exact copy, delete that exact
  copy" for two files with identical bytes. A later decision for the same hash
  overwrites the earlier one. In bulk culling, that can mark every identical copy
  the same way.
- The UI's representative can differ from server auto-cull. UI group review uses
  `dup_central` first; server auto-cull and grid hide-dups use `combined`.
- `--no-scenes` also prevents CLIP embeddings from being computed in the current
  analyzer flow, so duplicate grouping falls back to phash-only even though the
  flag sounds scene-specific.
- Re-running `sift analyze --recurse` on the same library after Apply may scan
  `_rejected/` again unless the caller excludes or moves it outside the scanned
  tree. Rejected photos can re-enter the report.
- Duplicate grouping is `O(n^2)` over candidate image pairs, so very large
  libraries will get slow.
- Apply is reversible, but undo is best-effort. If the rejected file is missing,
  or the original path already exists, that move is skipped.

## Source Map

| Concern | Source |
| --- | --- |
| CLI dispatch | `sift/cli.py` |
| Analyze orchestration | `sift/audit/cli.py` |
| Duplicate detection | `sift/audit/grouping.py` |
| DB ingest and thumbnails | `sift/web/build_db.py` |
| Schema and decision table | `sift/web/photodb.py` |
| Read filters and grouped pages | `sift/web/queries.py` |
| Decision API, auto-cull, apply, undo | `sift/web/server.py` |
| UI representative selection | `frontend/src/format.ts` |
| Duplicate group review | `frontend/src/components/GroupReview.tsx` |
| Apply/undo panel | `frontend/src/components/ApplyPanel.tsx` |

