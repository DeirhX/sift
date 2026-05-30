# PhotoOrganizer

A local, single-user toolkit for **auditing and culling a photo library**. It
scores every image for sharpness and aesthetic quality, groups near-duplicates,
optionally captions/tags photos and detects faces, then gives you a fast web UI
to review, mark keep/delete, and **reversibly move** rejects into a `_rejected/`
folder. Nothing is ever hard-deleted.

Everything runs on your machine. There is no cloud, no auth, no telemetry.

## How it works

The workflow is a three-stage pipeline:

| Stage | Script | Produces |
|-------|--------|----------|
| 1. Analyze | `photo_audit.py` | `audit_report.json` |
| 2. Index | `webapp/build_db.py` | `photos.db` + `.thumbs/` (WebP cache) |
| 3. Review | `webapp/server.py` + React UI | browse, decide, apply |

```
photo_audit.py ──► audit_report.json ──► build_db.py ──► photos.db + .thumbs/
                                                              │
                                                  server.py (FastAPI) ──► React UI
                                                              │
                                              originals  ◄────┴────►  _rejected/
```

## Install

Requires Python 3.10+ and (for the review UI) Node 18+.

```bash
pip install -r requirements.txt          # see notes in requirements.txt re: torch/CUDA
cd webapp/frontend && npm install
```

The ML scoring path downloads multi-GB model weights on first run and is far
happier with a CUDA GPU. If you only want sharpness + duplicate detection, you
can skip the heavy backends with `--no-clip` (see below).

## Usage

### 1. Analyze a folder

```bash
python photo_audit.py "E:\Photos" --recurse --backend para --caption --faces
```

Useful flags (full list in the script's `--help`):

- `--recurse` — include subfolders
- `--backend {para,clip-iqa,both}` — aesthetic model (default `para`)
- `--no-clip` — sharpness + duplicates only (fast, no models)
- `--caption` — add BLIP captions + CLIP keyword tags
- `--faces` — detect and cluster faces (needs `facenet-pytorch`)
- `--face-ref "Alice=alice.jpg"` — auto-name the cluster matching a reference

Output: `audit_report.json` in the folder (or `--out <path>`).

### 2. Build the database + thumbnails

```bash
python webapp/build_db.py "E:\Photos\audit_report.json"
```

This is **incremental**: re-running it after a fresh audit only re-hashes and
re-thumbnails files whose bytes changed (detected by mtime + size). Thumbnails
are named by content hash, so re-sorting the report no longer invalidates the
cache.

### 3. Review in the browser

```bash
# Production: build the frontend once, then serve everything from FastAPI
cd webapp/frontend && npm run build
python webapp/server.py --db "E:\Photos\photos.db"
# → http://localhost:8000
```

```bash
# Development: API on :8000 + Vite dev server on :5173 (proxies /api, /thumb, /img)
python webapp/server.py --db "E:\Photos\photos.db"
cd webapp/frontend && npm run dev
```

In the UI you can filter by score/sharpness/aesthetic, people, tags and
captions; review duplicate groups; auto-cull (keep the best of each group);
and finally **Apply** to move every `del`-marked file into `<library>/_rejected/`.
Apply is logged and **undoable**.

## Decisions survive moves

Keep/delete decisions are keyed by a **content hash**, not by file path. If you
reorganize folders and re-run the audit, your decisions follow the actual file
content rather than being orphaned.

## Scores (all 0–1, higher = better)

- `sharpness` — normalized Laplacian variance (blur detection)
- `clip_iqa` — CLIP-IQA bipolar prompt score
- `para_aesthetic`, `para_*` — PARA model heads (aesthetic/quality/composition/light/color/dof/content)
- `combined` — `0.4 * sharpness + 0.6 * primary_aesthetic`

## Other scripts

- `generate_viewer.py` — build a standalone, self-contained `audit_viewer.html`
  (older review UI; cluster names live in `localStorage`)
- `reface.py` — re-run only face detection on an existing report
- `classify_inspiration.py` — one-off CLIP folder classifier (paths hardcoded)

## Notes & limitations

- **Re-analysis is not incremental.** `build_db.py` is incremental, but
  `photo_audit.py` re-scores every file each run; that GPU work is the real cost
  on large libraries.
- Duplicate grouping is `O(n²)` over all image pairs — fine for thousands, slow
  for very large libraries.
- No tests yet.
