# Working on Sift

Orientation for anyone (human or AI) changing this repo. `README.md` is for
users, `ARCHITECTURE.md` is the module map and design rationale; this file is
the short list of what to run and what not to break.

## Verify every change

```bash
python scripts/check.py          # pytest, API-type drift, typecheck, vitest  (~1 min)
python scripts/check.py --e2e    # + Playwright against a real server         (~1 more)
python scripts/check.py --fast   # pytest + typecheck, for the inner loop
```

This is exactly what the required CI job (`test`) runs. `main` is protected:
changes land through a pull request with that job green.

Run one thing at a time:

| What | Command |
|---|---|
| One Python test | `python -m pytest tests/test_apply_api.py -k empty_trash` |
| One frontend test | `cd frontend && npx vitest run src/keys.test.ts` |
| One browser test | `cd frontend && npx playwright test -g "lightbox"` |
| The app, against the tiny fixture library | `cd frontend && npx vite build && python e2e/serve_fixture.py` → http://127.0.0.1:8765 |

Setup: `pip install -e ".[dev]"` and `cd frontend && npm ci`. No ML stack is
needed for any of the above; tests marked `ml` are skipped unless `--run-ml`.

## Where things go

| To change… | Edit | Tested by |
|---|---|---|
| What files get moved / deleted (Trash, restore, empty) | `sift/web/library_ops.py` | `tests/test_apply_api.py` |
| DB tables, migrations, face / person rules | `sift/web/photodb.py` | `tests/test_photodb.py` |
| How lists are filtered, sorted, paged | `sift/web/queries.py` | `tests/test_images_api.py` |
| An HTTP endpoint | `sift/web/routes/<area>.py` (older ones still in `server.py`) | `tests/test_*_api.py` |
| A response's JSON shape | `sift/web/schemas.py`, then `npm run codegen` | CI fails if `schema.d.ts` is stale |
| Face / person edits | `sift/web/face_ops.py` | `tests/test_people_api.py`, `test_face_edits_replay.py` |
| Long-running jobs and their progress stream | `sift/web/tasks.py` | `tests/test_tasks_api.py` |
| Scoring, grouping, face detection (batch, needs ML) | `sift/audit/` | `tests/test_photo_audit.py`, `tests/ml/` |
| A frontend API call | `frontend/src/api.ts` (types come from `src/api/types`) | |
| Keyboard shortcuts | `frontend/src/keys.ts`; list them in `components/ShortcutHelp.tsx` | `keys.test.ts` |
| Keep / Trash marks, optimistic updates, undo | `frontend/src/hooks/useDecisions.ts` | `useDecisions.test.tsx` |
| What is in the URL / Back-button behaviour | `frontend/src/urlState.ts`, `hooks/useOverlayNav.ts` | `urlState.test.ts`, e2e "browser history" |
| Which queries refetch after an action | `frontend/src/queryKeys.ts` | |

New endpoints go in a router module: `create_router(db_factory)` in
`sift/web/routes/`, included from `server.py`. Routers get their config as
callables because tests repoint `server.DB_PATH` / `THUMB_DIR` at runtime.

## Rules that are easy to break

**Data safety**
- Nothing deletes a user's photo except `library_ops.empty_trash`, and it only
  unlinks files directly inside a `_trash` folder. Every other "delete" is a
  move into `<library>/_trash/` recorded in `trash_moves`, so it can be restored.
- Never move a file onto an existing path. Use `library_ops.unique_dest` (web)
  or `cli._unique_dest` (analyze); a plain `shutil.move` overwrites.
- Decide "is this path inside that folder" with `library_ops._inside`, not
  `str.startswith` (`_trash_old` is not `_trash`).
- Image ids are renumbered by every `sift index`. Anything that must survive a
  rebuild is keyed by **content hash** (decisions, face overrides, name anchors,
  scene pins) or by path (the Trash ledger), never by image id.
- `decisions`, `copy_decisions`, `clusters`, `face_overrides`,
  `cluster_name_anchors`, `scene_merges`, `scene_splits`, `trash_moves` are user
  work that cannot be regenerated. `images`, `faces`, `image_tags`, thumbnails
  can. A migration or rebuild must carry the first group over.
- `sift/web` must not import torch or anything from the `[ml]` extra; analysis
  runs as a subprocess (`sift/web/analysis.py`).

**Server**
- Bound to loopback, the server rejects requests whose `Host` is not a loopback
  name, and rejects cross-origin writes (`guard_browser_writes`). Keep new
  routes behind that middleware; do not add a second app or mount.
- Anything that opens or lists the filesystem (`/api/reveal`, `/api/fs/complete`)
  must stay bounded by the photo roots / loopback checks.

**Frontend**
- Response types are generated. Do not hand-edit `src/api/schema.d.ts`; change
  `schemas.py` and run `npm run codegen`.
- Key handlers call `hasModifier` / `decisionForKey` from `keys.ts` so browser
  chords (Ctrl+D, Ctrl+-) never mark photos.
- Ask with `confirmDialog` / `promptDialog` (`dialog.ts`) and report failures
  with `toast` (`toast.ts`). No `window.confirm`, `window.prompt`, `alert`, and
  no swallowed `catch`.
- A mark is applied optimistically, then reverted and reported if the save
  fails. Follow `useDecisions.setDecision` when adding another optimistic write.
- List query keys start with `images` / `groups` / `scenes` / `meta` / `people`
  (`queryKeys.ts`); optimistic patches and invalidation find them by that root.
- Person display names come from `personLabel` in `format.ts`.
- The Playwright suite shares one server and one DB (`workers: 1`): a test that
  marks a photo must clear the mark before it ends.

## Local traps

- Windows machines with `NODE_ENV=production` set globally: vitest is pinned to
  `test` in `vite.config.ts`, and `scripts/check.py` unsets it.
- Playwright serves the fixture on port 8765 and reuses whatever is already
  listening there. If another program owns that port, set `E2E_PORT` (the check
  script picks a free one for you).
- `frontend/e2e/real/` needs the ML stack and is not part of CI.

## Known large files

`sift/web/server.py` (~1,150 lines: list endpoints, file serving, photo roots,
folder settings, task streaming) and `frontend/src/components/GroupReview.tsx`
(~830 lines) are the two files where a change is hardest to reason about. Prefer
moving a cohesive piece out (a router, a hook, a pure helper with a test) over
adding to them.
