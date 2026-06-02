# Architecture

Developer-facing map of the `sift` package: the modules, how they layer, and the
design decisions that aren't obvious from any single file. For install/usage see
[`README.md`](README.md).

## The pipeline

Three decoupled stages communicate through files, never through shared process
state. Each can be run, cached, and tested independently, and all three are
reachable through one console command (`sift <stage>`, see `sift/cli.py`).

```
 sift analyze          sift index               sift serve + React UI
 (sift.audit)          (sift.web.build_db)      (sift.web.server)
      │                     │                          │
  scans a folder    reads audit_report.json     serves photos.db over HTTP,
  scores/groups/    → photos.db (SQLite)         mutates decisions/faces,
  captions/faces    → .thumbs/ (WebP cache)      moves rejects (reversibly)
      │                     │                          │
      └──► audit_report.json ┘                         └──► <library>/_rejected/
```

The split matters: the GPU-heavy analysis (`sift.audit`) is a batch CLI job; the
web server (`sift.web`) only ever touches the SQLite DB and the filesystem and
loads **no ML models**. Re-analysis from the UI shells out to `python -m sift
analyze` / `index` as a subprocess (see `sift/web/analysis.py`) rather than
importing the analysis stack. This is also why the dependency extras split
cleanly: a web-only install (`pip install -e .`) skips the multi-GB `[ml]` stack.

## Backend modules

| File | Lines | Responsibility |
|------|------:|----------------|
| `sift/cli.py` | ~50 | The unified `sift` console entry point. Dispatches `analyze`/`index`/`serve` to the owning module's `main()`, importing each lazily so `sift serve` never drags in the ML stack. |
| `sift/audit/` | ~1450 | The analysis pipeline, split into focused modules (see below). Writes `audit_report.json`. The package `__init__` re-exports the public surface. |
| `sift/web/build_db.py` | ~420 | Ingests a report into `photos.db`: content-hashing, thumbnail generation, **incremental** re-hash/re-thumb, decision/cluster-name preservation, face-override replay, orphan-thumb pruning. (`sift index`) |
| `sift/web/photodb.py` | ~350 | The **schema + domain authority**. Owns DDL, migrations (`ensure_schema`), and the face/cluster/portrait domain rules (`largest_face_aggregate`, `bbox_key`, cluster name anchors, manual-cluster id allocation). Shared by `build_db` and `server`, so ingest and the API can't disagree. |
| `sift/web/server.py` | ~1040 | FastAPI app: routing, runtime config (`DB_PATH`/`THUMB_DIR`/photo roots/frontend dist), the `db()` connection factory, mutation endpoints (decisions, clusters, faces), byte serving (thumb/full/reveal), `_rejected/` apply+undo, and the `/api/analyze/*` lifecycle. (`sift serve`) |
| `sift/web/queries.py` | ~230 | **Read layer.** Pure `conn`-parameterized SQL→dict helpers shared by the read endpoints: faceted `image_where`, `rows_to_items`, paginated `grouped_page` (groups/scenes), `histogram`, `SORT_COLUMNS`, the `DEC_ON` join. No app/global coupling — trivially unit-testable. Returns plain dicts; FastAPI validates them against the route's `response_model` (see `schemas.py`). |
| `sift/web/schemas.py` | ~225 | **Response DTOs.** Pydantic models for every endpoint's JSON (`ImageItem`, `GroupedImageItem`, the paginated `*Response` wrappers, `MetaResponse`, `AnalyzeStatus`, …), attached as `response_model=` on the routes. This is what makes `/openapi.json` carry real response schemas, which the frontend codegens into `schema.d.ts` (the "can't drift" contract). Nullable fields are `X \| None` so `null`s are emitted where the UI expects them; `AnalyzeStatus` uses `response_model_exclude_unset=True` to keep its idle-vs-running shape. |
| `sift/web/analysis.py` | ~210 | **Reanalysis subsystem.** `AnalysisJob` (background thread running `python -m sift` steps, parsing tqdm `\r` progress into a live line + committed lines) and `build_analyze_steps` (UI payload → validated/clamped argv). Config-agnostic: paths and a connection factory are injected, so there's no import cycle with `server.py`. |

### Layering inside `sift/audit/`

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

`sift/audit/__init__.py` re-exports the public surface of all six modules, so the
split is invisible to callers (`sift.audit.<fn>`); `tests/test_package_smoke.py`
asserts the package re-exports stay in sync (same objects, no drift). Only the
light deps (numpy/opencv/PIL/tqdm) are imported at package load; torch /
transformers / imagehash stay lazy. `scoring.load_para_scorer` imports the model
head as `sift.audit.aesthetic_scorer` — no `sys.path` manipulation anywhere.

### Layering inside `sift/web/`

```
server.py  ── routing, config, mutations, file I/O, app wiring
   │  imports
   ├──► queries.py    (read layer; takes a conn, returns dicts)
   ├──► schemas.py    (Pydantic response_model DTOs ──► /openapi.json)
   ├──► analysis.py   (reanalysis job + argv builder; config injected)
   └──► photodb.py    (schema + domain rules)   ◄── also used by build_db.py
```

Dependencies point one way: `server` → (`queries`, `schemas`, `analysis`,
`photodb`). The leaf layers (`queries`, `photodb`) know nothing about FastAPI or
server globals, which is what keeps them testable and reusable; `schemas` is
plain Pydantic and equally decoupled.

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
- **Upright before analysis.** `sift.audit` runs every image through
  `ImageOps.exif_transpose` before a model embeds it or a perceptual hash is
  taken (`clip_common.iter_image_batches`, `grouping.compute_phashes`), so a
  portrait frame (EXIF orient 6/8) is never fed to CLIP/phash as raw,
  90°-rotated pixels. This is what lets a portrait and a landscape reframe of
  the *same* moment land in one near-dup group — measured CLIP cosine ~0.74 raw
  vs ~0.97 upright, the difference between "unrelated" and "above `dup_sim`".
  The face detector deliberately stays in **raw** sensor coordinates: its bboxes
  and the stored `imgw/imgh` are a coordinate contract with the frontend face
  overlays, a separate concern from visual similarity.
- **Override replay.** UI face edits write to `face_overrides`; `build_db`
  re-applies them after a fresh audit. The API recompute path
  (`server._reaggregate_faces`) and the ingest path both go through
  `photodb.largest_face_aggregate`, so they can never produce different
  `portrait`/`face_sharp` values for the same faces.
- **Incremental everywhere.** Both `sift analyze` (report-as-cache, keyed on
  mtime+size) and `sift index` (re-hash/re-thumb only changed bytes) avoid redoing
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

## Frontend (`frontend/`, React + Vite + TypeScript)

The frontend is **strict-mode TypeScript** (`tsconfig.json`, `strict: true`);
`npm run build` gates on `tsc --noEmit` before `vite build`, and `npm run
typecheck` runs it standalone.

- `App.tsx` — top-level orchestrator: URL-synced filter/view state, data
  fetching (React Query), keyboard navigation.
- `urlState.ts` — serializes filters/view/overlay-nav to the URL (shareable,
  back-button friendly). `api.ts` — typed `fetch` wrappers returning the
  generated DTO types. `format.ts` — shared formatters incl. `qualityColor`.
  `types.ts` — shared UI/callback types (decisions, person names, filter updates).
- **Overlay history model.** Every overlay (lightbox / group / scene review) and
  each in-overlay step (focus a photo, toggle zoom) is a real history entry
  tagged with a `navDepth` (`App.tsx`), so Back peels one step at a time while
  Close unwinds the whole overlay in one `history.go(-(depth+1))`. That unwind
  assumes a plain-list entry sits beneath the overlay — true when opened from
  the list, but **not** when the app loads straight into an overlay (deep link /
  reload / new tab). So on mount `App.jsx` plants a list entry beneath a
  deep-linked overlay; without it, Close had nothing to unwind onto and silently
  did nothing (regression-guarded in `e2e/flows.spec.js`).
- The filmstrip review (`GroupReview`) is **selection-driven**: arrow keys move
  the active member via a global key handler (the strip auto-scrolls to follow),
  the active tile is the sole highlight (UA focus rings on the tiles are
  suppressed), and the strip is a fixed height so expanding a collapsed near-dup
  set (via its `+` handle) never reflows the hero preview.
- Views: `PhotoGrid` (masonry, aspect-preserving) with `PhotoCard`;
  `GroupView`/`GroupPile` (duplicate sets); `SceneView`/`ScenePile`/`ScenePanel`
  (scene hierarchy); `GroupReview` (filmstrip review modal); `Lightbox`
  (full-res). Panels: `Sidebar`, `FolderTree`, `SettingsPanel`, `AnalyzePanel`,
  `ApplyPanel`. Controls: `RangeSlider`, `DecideButtons`, `DecisionBadge`.
- Tests are colocated `*.test.{ts,tsx}` (Vitest).

### API type contract (codegen — "can't drift")

Response shapes are generated from the backend, not hand-mirrored, so the two
sides can't silently diverge:

```
sift/web/schemas.py (Pydantic response_model)
        │  app.openapi()  (sift/web/openapi_schema.py)
        ▼
   /openapi.json ──► openapi-typescript ──► src/api/schema.d.ts
                                                  │  friendly aliases
                                                  ▼
                                            src/api/types.ts ──► api.ts, components
```

Run `npm run codegen` after any backend response change. It dumps the schema via
`python -m sift.web.openapi_schema` and regenerates `src/api/schema.d.ts`. The
intermediate `frontend/openapi.json` is gitignored; **the committed artifact is
`schema.d.ts`** so the frontend builds without a running backend. Caveat: codegen
is run manually (not yet wired into CI), so the guarantee holds only as long as
someone regenerates after touching a `response_model`.

- **Styling** lives in one `styles.css` fronted by a `:root` **design-token
  layer**: the palette plus semantic tokens — `--accent/keep/del` (+ `*-rgb`
  triples for translucent `rgba(var(--x-rgb), a)` fills), `--on-accent/keep/del`
  (foreground on a solid fill), `--gold/amber/info`, `--thumb-bg`,
  `--term-bg/fg`, `--font-sans/mono`, `--shadow-card/pop`. Components reference
  tokens, never raw literals, so re-theming or tweaking an accent is a one-line
  change. Deliberately **not** tokenized yet: radius/spacing/font-size scales
  (the current values are ad hoc and snapping them to a scale would shift
  pixels — a redesign, not a consolidation).

## Tests

Three tiers, by cost and what they exercise:

- **Unit + API integration** (`tests/`, `pytest -q`) — the default suite, fully
  model-free and fast (~8s). Covers ingest aggregation, every read endpoint, the
  reveal guardrail + settings/roots + fs autocomplete + idle analyze stream,
  face/cluster mutation + override persistence, the analyze argv builder/guards,
  and the pure `sift.audit` helpers (`_clean_tags`, `iter_image_batches`,
  `bipolar_score`, scene/dup grouping, the CLI parser). The web tests drive the
  FastAPI app through a `TestClient` against a synthetic report — no models.
  `test_package_smoke.py` imports every `sift.audit` submodule, asserts the
  package re-exports the same objects (guards the split's surface), and pins the
  `sift` CLI dispatcher's routing.
- **End-to-end pipeline** (`tests/test_e2e_pipeline.py`) — the only test that
  runs the real `sift analyze` CLI as a subprocess (`--no-clip`, so classical
  sharpness + phash duplicates, no weights), then ingests its report with
  `sift index` and asserts through the API. This is the cross-stage seam check;
  still CI-safe.
- **ML efficacy** (`tests/ml/`, opt-in) — see below. Skipped by default.
- **Frontend** (`frontend/`, `npm test`): URL state, API wrappers,
  formatters, and key components (Vitest, strict TS). `npm run build` (or
  `npm run typecheck`) additionally enforces `tsc --noEmit` across the tree.

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

- The whole backend is one import namespace: `sift.audit` (analysis, see
  *Layering inside `sift/audit/`*) and `sift.web` (ingest + server), driven by a
  single `sift` console command. The neural function bodies are validated by the
  opt-in `--run-ml` efficacy tier; the import-smoke test guards the package
  surface. `styles.css` (~1400 lines) is now the largest single file,
  frontend-side.
- Duplicate grouping is `O(n²)` over image pairs — fine for thousands, slow for
  very large libraries.
- `server.py` runtime config is still module-level mutable state
  (`DB_PATH`/`THUMB_DIR`/photo roots), set once at startup and patched directly
  by tests. Fine for a single-user, single-process tool; a config object would
  only pay off if multi-tenancy ever mattered.
