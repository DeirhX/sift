# Architecture

Developer-facing map of PhotoOrganizer: the modules, how they layer, and the
design decisions that aren't obvious from any single file. For install/usage see
[`README.md`](README.md).

## The pipeline

Three decoupled stages communicate through files, never through shared process
state. Each can be run, cached, and tested independently.

```
 photo_audit.py        build_db.py              server.py + React UI
 (analyze)             (index)                  (review)
      │                     │                          │
  scans a folder    reads audit_report.json     serves photos.db over HTTP,
  scores/groups/    → photos.db (SQLite)         mutates decisions/faces,
  captions/faces    → .thumbs/ (WebP cache)      moves rejects (reversibly)
      │                     │                          │
      └──► audit_report.json ┘                         └──► <library>/_rejected/
```

The split matters: the GPU-heavy analysis (`photo_audit.py`) is a batch CLI job;
the web server (`webapp/`) only ever touches the SQLite DB and the filesystem and
loads **no ML models**. Re-analysis from the UI shells out to the CLI as a
subprocess (see `analysis.py`) rather than importing it.

## Backend modules

| File | Lines | Responsibility |
|------|------:|----------------|
| `photo_audit.py` | ~40 | Thin **re-export shim** + CLI entry point over the `audit/` package. Preserves `python photo_audit.py` (the server invokes it by path) and `import photo_audit.<fn>` (tests). |
| `audit/` | ~1450 | The analysis pipeline, split into focused modules (see below). Writes `audit_report.json`. |
| `webapp/build_db.py` | ~420 | Ingests a report into `photos.db`: content-hashing, thumbnail generation, **incremental** re-hash/re-thumb, decision/cluster-name preservation, face-override replay, orphan-thumb pruning. |
| `webapp/photodb.py` | ~350 | The **schema + domain authority**. Owns DDL, migrations (`ensure_schema`), and the face/cluster/portrait domain rules (`largest_face_aggregate`, `bbox_key`, cluster name anchors, manual-cluster id allocation). Shared by `build_db` and `server`, so ingest and the API can't disagree. |
| `webapp/server.py` | ~1040 | FastAPI app: routing, runtime config (`DB_PATH`/`THUMB_DIR`/photo roots), the `db()` connection factory, mutation endpoints (decisions, clusters, faces), byte serving (thumb/full/reveal), `_rejected/` apply+undo, and the `/api/analyze/*` lifecycle. |
| `webapp/queries.py` | ~230 | **Read layer.** Pure `conn`-parameterized SQL→DTO helpers shared by the read endpoints: faceted `image_where`, `rows_to_items`, paginated `grouped_page` (groups/scenes), `histogram`, `SORT_COLUMNS`, the `DEC_ON` join. No app/global coupling — trivially unit-testable. |
| `webapp/analysis.py` | ~210 | **Reanalysis subsystem.** `AnalysisJob` (background thread running CLI steps, parsing tqdm `\r` progress into a live line + committed lines) and `build_analyze_steps` (UI payload → validated/clamped argv). Config-agnostic: paths and a connection factory are injected, so there's no import cycle with `server.py`. |

### Layering inside `audit/`

The pipeline is split by stage, lowest layer first. `cli` orchestrates; the
stage modules don't know about each other except the explicit deps shown.

```
clip_common.py  ── shared CLIP / batching primitives (no intra-package deps)
   ▲      ▲      ▲
   │      │      └──── grouping.py   phash dups, scene segmentation, capture time, CLIP embeds
   │      └─────────── faces.py      MTCNN + VGGFace2 + DBSCAN  (also ──► scoring)
   └────────────────── scoring.py    Laplacian sharpness + CLIP-IQA + PARA
                       tagging.py    BLIP captions + Qwen3-VL keyword tags (standalone)
                          ▲
cli.py  ── arg parsing + main() orchestration; imports scoring/tagging/grouping/faces
```

`photo_audit.py` re-exports the public surface of all six modules, so the split
is invisible to callers; `tests/test_package_smoke.py` asserts the shim stays in
sync (same objects, no drift). `audit/__init__.py` puts the repo root on
`sys.path` so `scoring.load_para_scorer` can `import aesthetic_scorer` regardless
of how the package is reached.

### Layering inside `webapp/`

```
server.py  ── routing, config, mutations, file I/O, app wiring
   │  imports
   ├──► queries.py    (read layer; takes a conn, returns DTOs)
   ├──► analysis.py   (reanalysis job + argv builder; config injected)
   └──► photodb.py    (schema + domain rules)   ◄── also used by build_db.py
```

Dependencies point one way: `server` → (`queries`, `analysis`, `photodb`). The
two leaf layers (`queries`, `photodb`) know nothing about FastAPI or server
globals, which is what keeps them testable and reusable.

## Data model (SQLite, defined in `photodb.py`)

- **`images`** — one row per file: path, `content_hash`, score columns
  (`sharpness`, `clip_iqa`, `para_*`, `combined`), `dup_group`/`dup_central`,
  `scene_group`, `capture_time`, dimensions, caption, aggregated face fields
  (`n_faces`, `face_sharp`, `face_expr`, `portrait`).
- **`faces`** — per-face boxes, `prob`, `cluster_id`, per-face `sharp`/`expr`.
- **`clusters`** — `cluster_id → name` (people). Manual people get ids ≥ 100000.
- **`decisions`** — `content_hash → keep|del`. **Hash-keyed, not path-keyed**, so
  decisions survive file moves/reorg.
- **`image_tags`** — keyword tags; mirrored into an FTS5 table (`images_fts`) for
  caption/tag search when available.
- **`face_overrides`** — persisted UI face edits (assign/delete) keyed by
  `(content_hash, bbox)`, replayed by `build_db` on every ingest.
- **`cluster_name_anchors`** — pins a person's name to its face keys so renames
  survive re-clustering on a fresh audit.
- **`photo_roots`** — directories the `/api/reveal` guardrail is allowed to open.
- **`applied_moves`** — log backing the reversible Apply/Undo of rejects.

## Cross-cutting design decisions

- **Content-hash identity.** Decisions and face overrides are keyed by file
  content, not path, so reorganizing the library doesn't orphan your review work.
- **Override replay.** UI face edits write to `face_overrides`; `build_db`
  re-applies them after a fresh audit. The API recompute path
  (`server._reaggregate_faces`) and the ingest path both go through
  `photodb.largest_face_aggregate`, so they can never produce different
  `portrait`/`face_sharp` values for the same faces.
- **Incremental everywhere.** Both `photo_audit` (report-as-cache, keyed on
  mtime+size) and `build_db` (re-hash/re-thumb only changed bytes) avoid redoing
  expensive work. Thumbnails are named by content hash.
- **Constrained subprocess, not eval.** `/api/analyze` never runs arbitrary
  commands. `analysis.build_analyze_steps` emits argv from a fixed flag set; only
  the target folder is free text and it's validated to be an existing directory;
  numeric knobs are parsed and clamped.
- **Single analysis job.** One `AnalysisJob` at a time (`server.CURRENT_JOB`),
  streamed to the browser over SSE with full replay so a late/reconnecting client
  still gets the whole log.
- **No models in the server.** Keeps the web process light and lets the heavy
  path run/scale separately.

## Frontend (`webapp/frontend/`, React + Vite)

- `App.jsx` — top-level orchestrator: URL-synced filter/view state, data
  fetching (React Query), keyboard navigation.
- `urlState.js` — serializes filters/view to the URL (shareable, back-button
  friendly). `api.js` — typed fetch wrappers. `format.js` — shared formatters
  incl. `qualityColor`.
- Views: `PhotoGrid` (masonry, aspect-preserving) with `PhotoCard`;
  `GroupView`/`GroupPile` (duplicate sets); `SceneView`/`ScenePile`/`ScenePanel`
  (scene hierarchy); `GroupReview` (filmstrip review modal); `Lightbox`
  (full-res). Panels: `Sidebar`, `FolderTree`, `SettingsPanel`, `AnalyzePanel`,
  `ApplyPanel`. Controls: `RangeSlider`, `DecideButtons`, `DecisionBadge`.
- Tests are colocated `*.test.{js,jsx}` (Vitest).

## Tests

Three tiers, by cost and what they exercise:

- **Unit + API integration** (`tests/`, `pytest -q`) — the default suite, fully
  model-free and fast (~8s). Covers ingest aggregation, every read endpoint, the
  reveal guardrail + settings/roots + fs autocomplete + idle analyze stream,
  face/cluster mutation + override persistence, the analyze argv builder/guards,
  and the pure `photo_audit` helpers (`_clean_tags`, `iter_image_batches`,
  `bipolar_score`, scene/dup grouping, the CLI parser). The web tests drive the
  FastAPI app through a `TestClient` against a synthetic report — no models.
  `test_package_smoke.py` imports every `audit/` submodule and asserts the
  `photo_audit` shim re-exports the same objects (guards the split's surface).
- **End-to-end pipeline** (`tests/test_e2e_pipeline.py`) — the only test that
  runs the real `photo_audit.py` CLI as a subprocess (`--no-clip`, so classical
  sharpness + phash duplicates, no weights), then ingests its report with
  `build_db` and asserts through the API. This is the cross-stage seam check;
  still CI-safe.
- **ML efficacy** (`tests/ml/`, opt-in) — see below. Skipped by default.
- **Frontend** (`webapp/frontend`, `npm test`): URL state, API wrappers,
  formatters, and key components.

### ML efficacy harness (`tests/ml/`)

`test_efficacy.py` has two tiers:

- **Classical (always run):** the non-neural tasks have synthesizable ground
  truth, so efficacy is asserted every run — Laplacian sharpness ranks a crisp
  image above a blurred copy; phash groups a recompressed duplicate and rejects
  an unrelated image.
- **Neural (`@pytest.mark.ml`, opt-in):** PARA / CLIP-IQA / Qwen3-VL / BLIP /
  faces load real weights. Skipped unless `--run-ml` or `RUN_ML=1`. On synthetic
  inputs these assert the *contract* (score ranges, tag-cleaning, determinism /
  drift), which catches load breakage and silent regressions. For real
  *accuracy*, drop labeled photos in `tests/ml/fixtures/` with a `labels.json`
  (schema in that folder's README) and `test_golden_set_accuracy` checks
  expected tags/captions/face counts against them.

```bash
pytest                       # default: model-free suite (~8s)
pytest -n auto               # ~20% faster via pytest-xdist (model-free only)
pytest tests/ml -m ml --run-ml   # neural efficacy; needs weights + GPU. NO -n (one GPU).
```

Profiling note: the model-free suite is import-bound (one `transformers` import
≈ 5s of the ~8s), not CPU-bound, so `-n auto` only saves ~20% and each worker
re-pays the import. It's kept opt-in rather than default; don't parallelise the
`--run-ml` tier — the efficacy tests share a single GPU and would contend/OOM.

## Known shape / future work

- The analysis pipeline now lives in the `audit/` package (see *Layering inside
  `audit/`*), split along stage seams with `photo_audit.py` reduced to a thin
  re-export shim. The neural function bodies are validated by the opt-in
  `--run-ml` efficacy tier; the import-smoke test guards the package surface.
  `styles.css` (~1400 lines) is now the largest single file, frontend-side.
- Duplicate grouping is `O(n²)` over image pairs — fine for thousands, slow for
  very large libraries.
- `server.py` runtime config is still module-level mutable state
  (`DB_PATH`/`THUMB_DIR`/photo roots), set once at startup and patched directly
  by tests. Fine for a single-user, single-process tool; a config object would
  only pay off if multi-tenancy ever mattered.
