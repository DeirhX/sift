# PhotoOrganizer

A local, single-user toolkit for **auditing and culling a photo library**. It
scores every image for sharpness and aesthetic quality, groups near-duplicates,
optionally captions/tags photos and detects faces, then gives you a fast web UI
to review, mark keep/delete, and **reversibly move** rejects into a `_rejected/`
folder. Nothing is ever hard-deleted.

Everything runs on your machine. There is no cloud, no auth, no telemetry.

For a developer-facing map of the modules and design decisions, see
[`ARCHITECTURE.md`](ARCHITECTURE.md). For the physical culling/apply flow and
duplicate filtering audit, see
[`PHOTO_ORGANIZATION_PIPELINE.md`](PHOTO_ORGANIZATION_PIPELINE.md).

## How it works

The workflow is a three-stage pipeline, driven by one `sift` command:

| Stage | Command | Produces |
|-------|---------|----------|
| 1. Analyze | `sift analyze` | `audit_report.json` |
| 2. Index | `sift index` | `photos.db` + `.thumbs/` (WebP cache) |
| 3. Review | `sift serve` + React UI | browse, decide, apply |

```
sift analyze ──► audit_report.json ──► sift index ──► photos.db + .thumbs/
                                                            │
                                                sift serve (FastAPI) ──► React UI
                                                            │
                                            originals  ◄────┴────►  _rejected/
```

## Install

Requires Python 3.10+ and (for the review UI) Node 18+. Dependencies live in
`pyproject.toml`; install the package (editable) with the extras you need:

```bash
pip install -e ".[ml]"     # core + the multi-GB ML stack (torch/CUDA: see below)
# pip install -e .         # core only: classical sharpness + duplicate/scene grouping + web
# pip install -e ".[dev]"  # + the test suite
cd frontend && npm install
```

This installs the `sift` console command. (`pip install -r requirements.txt`
still works — it just forwards to `-e .[ml,dev]`.)

The ML scoring path downloads multi-GB model weights on first run and is far
happier with a CUDA GPU. If you only want sharpness + duplicate detection, you
can skip the heavy backends with `--no-clip` (see below).

## Usage

### 1. Analyze a folder

```bash
sift analyze "E:\Photos" --recurse --backend para --caption --faces
```

Useful flags (full list in `sift analyze --help`):

- `--recurse` — include subfolders
- `--backend {para,clip-iqa,both}` — aesthetic model (default `para`)
- `--no-clip` — sharpness + duplicates only (fast, no models)
- `--caption` — add BLIP captions + CLIP keyword tags
- `--faces` — detect and cluster faces (needs `facenet-pytorch`). Also scores
  each face's **region sharpness** (blur), normalized across all faces.
- `--face-expr` — also score **portrait expression** quality per face via
  zero-shot CLIP (coarse pleasant-vs-grimace; requires `--faces`)
- `--face-ref "Alice=alice.jpg"` — auto-name the cluster matching a reference

Output: `audit_report.json` in the folder (or `--out <path>`).

This step is **incremental**: the previous report doubles as a cache, so a
re-run only re-scores files whose bytes changed (detected by mtime + size) and
whose cached record already has the outputs you asked for. The heavy
aesthetic/caption models are skipped for unchanged files. Pass `--no-cache` to
force a full re-score. (Faces are clustered globally, so any change re-detects
faces for the whole folder; changing flags like `--backend` or `--caption`
invalidates the cache.)

### 2. Build the database + thumbnails

```bash
sift index "E:\Photos\audit_report.json"
```

This is **incremental**: re-running it after a fresh audit only re-hashes and
re-thumbnails files whose bytes changed (detected by mtime + size). Thumbnails
are named by content hash, so re-sorting the report no longer invalidates the
cache.

### 3. Review in the browser

```bash
# Production: build the frontend once, then serve everything from FastAPI
cd frontend && npm run build
sift serve --db "E:\Photos\photos.db"
# → http://localhost:8000
```

```bash
# Development: API on :8000 + Vite dev server on :5173 (proxies /api, /thumb, /img)
sift serve --db "E:\Photos\photos.db" --reload
cd frontend && npm run dev
```

With `--reload`, backend Python/API changes restart the FastAPI process
automatically. Frontend React/CSS changes hot-reload through Vite. If you change
API response models, still run `cd frontend && npm run codegen`; type generation
is intentionally explicit so generated files do not churn behind your back.

`sift serve` finds the built frontend at `frontend/dist` (editable install). To
serve a build from elsewhere, pass `--frontend-dist <dir>` or set
`$SIFT_FRONTEND_DIST`.

The frontend is strict TypeScript. Its API response types are **generated** from
the backend's OpenAPI schema, so they can't drift from the server. After changing
any endpoint's `response_model`, regenerate them:

```bash
cd frontend && npm run codegen   # → src/api/schema.d.ts (commit this)
npm run typecheck                # tsc --noEmit; also runs as part of `npm run build`
```

In the UI you can filter by score/sharpness/aesthetic/portrait, people, tags and
captions; review duplicate groups; auto-cull (keep the best of each group);
and finally **Apply** to move every `del`-marked file into `<library>/_rejected/`.
Apply is logged and **undoable**.

**Face editing** (sidebar "manage" + clicking a face box on a tile): rename or
merge people, reassign a face to another/new person, or delete a false-positive
box. These edits are **persisted as overrides** and re-applied on the next
`build_db` ingest, so they survive a fresh audit (matched by image content hash
+ face bbox). Manually created people get ids ≥ 100000 to avoid colliding with
the detector's clusters.

## Decisions survive moves

Keep/delete decisions are keyed by a **content hash**, not by file path. If you
reorganize folders and re-run the audit, your decisions follow the actual file
content rather than being orphaned.

## Scores (all 0–1, higher = better)

- `sharpness` — normalized Laplacian variance (blur detection)
- `clip_iqa` — CLIP-IQA bipolar prompt score
- `para_aesthetic`, `para_*` — PARA model heads (aesthetic/quality/composition/light/color/dof/content)
- `combined` — `0.4 * sharpness + 0.6 * primary_aesthetic`
- per-face `sharp` / `expr` (with `--faces` / `--face-expr`); `build_db`
  aggregates the largest face per image into `face_sharp`, `face_expr` and a
  combined `portrait` (`0.6 * face_sharp + 0.4 * face_expr`)

## Other scripts

- `scripts/reface.py` — re-run only face detection on an existing report
  (`python scripts/reface.py audit_report.json`)
- `scripts/classify_inspiration.py` — one-off CLIP folder classifier (paths hardcoded)

## Notes & limitations

- Both stages are incremental, but **faces are global**: because identity
  clustering spans the whole folder, any change re-detects faces for every
  image (the scalar scores are still cached).
- Duplicate grouping is `O(n²)` over all image pairs — fine for thousands, slow
  for very large libraries.
- Orphaned thumbnails are pruned automatically on each `build_db` run; pass
  `--no-prune` to keep them.
- Portrait expression scoring is **coarse** (zero-shot CLIP on face crops);
  it's useful for ranking/flagging, not a forensic judgement. Closed-eye
  detection is intentionally not implemented (it needs eyelid landmarks, not
  CLIP).
- Tests live in `tests/` (run `pytest -q`); they cover the ingest aggregation
  and the face/cluster mutation + override-persistence endpoints.
