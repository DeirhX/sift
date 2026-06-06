"""Near-duplicate detection and scene segmentation.

Perceptual-hash duplicate grouping (with optional CLIP-cohesion splitting),
EXIF capture-time reading, CLIP scene embeddings, and time-plus-similarity
scene grouping that the duplicate groups nest inside.
"""
from datetime import datetime

import numpy as np
from PIL import ImageOps
from tqdm import tqdm

from .clip_common import iter_image_batches, load_openclip_b32
from .imaging import load_rgb


# ── Perceptual hashing / duplicate detection ──────────────────────────────────

def compute_phashes(paths: list, progress=None) -> tuple[dict, dict]:
    """Returns (hashes, sizes). Sizes are raw (pre-EXIF-transpose) (w, h),
    matching the coordinate space the face detector uses for its bboxes, so
    the frontend can scale face overlays and lay out aspect-correct tiles.

    The hash itself is taken on the EXIF-corrected (upright) image, so an
    orientation-variant re-save matches its sibling instead of reading as a
    90°-rotated stranger. `sizes` stays raw on purpose — it's a coordinate
    contract with the face overlays, a separate concern from visual similarity.

    `progress`, if given, is called as progress(done, total) after each image so
    a caller can surface live progress — this phase reads every file from disk
    (slow over a cloud-synced drive), so a frozen bar here is otherwise alarming."""
    import imagehash
    hashes: dict = {}
    sizes:  dict = {}
    n = len(paths)
    for i, p in enumerate(tqdm(paths, desc="Perceptual hashing"), 1):
        try:
            im = load_rgb(p)
            sizes[p] = im.size
            hashes[p] = imagehash.phash(ImageOps.exif_transpose(im))
        except Exception as e:
            print(f"  hash error {p.name}: {e}")
        if progress is not None:
            progress(i, n)
    return hashes, sizes


def group_duplicates(hashes: dict, threshold: int = 6) -> list[list]:
    paths = list(hashes.keys())
    visited = set()
    groups = []
    for i, p in enumerate(paths):
        if p in visited:
            continue
        group = [p]
        visited.add(p)
        for j in range(i + 1, len(paths)):
            q = paths[j]
            if q not in visited and (hashes[p] - hashes[q]) <= threshold:
                group.append(q)
                visited.add(q)
        if len(group) > 1:
            groups.append(group)
    return groups


def _cohesion_split(members: list, embeddings: dict, hashes: dict,
                    threshold: int, floor: float) -> list:
    """Split one single-linkage candidate component into cohesive sub-groups.

    Single linkage chains: A~B, B~C, ... collapse into one blob even when the
    endpoints are nothing alike, which is how a whole shoot ends up in one
    "near-duplicate" group. We re-cluster the component with average-linkage
    agglomeration and stop merging once the best inter-cluster average cosine
    drops below `floor`, so loosely-connected chains break apart while tight
    bursts stay whole. phash-matched pairs (exact re-saves) are pinned at
    similarity 1.0 — a literal duplicate must never be split off.

    Returns the list of resulting sub-groups with >= 2 members; singletons drop
    out (they weren't really near-duplicates of anything)."""
    n = len(members)

    def sim(i: int, j: int) -> float:
        a, b = members[i], members[j]
        if (hashes[a] - hashes[b]) <= threshold:
            return 1.0
        if a in embeddings and b in embeddings:
            return float(np.dot(embeddings[a], embeddings[b]))
        return 0.0

    S = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            S[i][j] = S[j][i] = sim(i, j)

    clusters = [[i] for i in range(n)]
    while len(clusters) > 1:
        best, bi, bj = -2.0, -1, -1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                tot = sum(S[x][y] for x in clusters[a] for y in clusters[b])
                avg = tot / (len(clusters[a]) * len(clusters[b]))
                if avg > best:
                    best, bi, bj = avg, a, b
        if best < floor:
            break
        clusters[bi].extend(clusters[bj])
        del clusters[bj]

    return [[members[i] for i in c] for c in clusters if len(c) >= 2]


def _burst_merge(items: list, groups: list, embeddings: dict, times: dict,
                 burst_gap: float, burst_sim: float, burst_span: float) -> list:
    """Strictly-additive burst merge. Treats `groups` (the cohesion result) and
    every still-loose frame as indivisible atoms, segments the time-ordered
    frames into bursts (consecutive gap <= burst_gap, run span <= burst_span,
    consecutive cosine >= burst_sim), and unions the atoms whose members share a
    burst. Returns the new >=2-member groups.

    Because it only ever merges whole atoms, an existing group can only grow —
    never lose a member. This is the key difference from pinning inside the
    cohesion split, which re-clusters enlarged components and can non-monotonically
    eject a previously-settled frame.

    Two PRE-EXISTING multi-member groups are never fused with each other: a single
    tight pair bridging two time-spread groups would otherwise drag both whole
    atoms (and their full, possibly multi-minute spans) into one blob, which the
    per-run span cap can't bound. So the pass only ATTACHES loose frames — to a
    group or to each other — which is exactly the burst-rescue case (a misty frame
    the cohesion floor left stranded) at zero risk to what already grouped."""
    from collections import defaultdict
    atom: dict = {}
    for gi, g in enumerate(groups):
        for p in g:
            atom[p] = gi
    nxt = len(groups)
    for p in items:                      # loose frames become singleton atoms
        if p not in atom:
            atom[p] = nxt
            nxt += 1

    aparent: dict = {a: a for a in range(nxt)}
    # A component is "real" once it contains an original (>=2-member) group; two
    # real components must never merge (see docstring).
    real: list = [a < len(groups) for a in range(nxt)]

    def afind(x):
        while aparent[x] != x:
            aparent[x] = aparent[aparent[x]]
            x = aparent[x]
        return x

    def aunion(x, y):
        rx, ry = afind(x), afind(y)
        if rx == ry or (real[rx] and real[ry]):
            return
        aparent[ry] = rx
        real[rx] = real[rx] or real[ry]

    timed = sorted((p for p in items
                    if p in embeddings and times.get(p) is not None),
                   key=lambda p: times[p])
    run: list = []

    def flush(r: list) -> None:
        for q in r[1:]:
            aunion(atom[r[0]], atom[q])

    for p in timed:
        if not run:
            run = [p]
            continue
        prev = run[-1]
        if (times[p] - times[prev] <= burst_gap
                and times[p] - times[run[0]] <= burst_span
                and float(np.dot(embeddings[prev], embeddings[p])) >= burst_sim):
            run.append(p)
        else:
            flush(run)
            run = [p]
    flush(run)

    merged: dict = defaultdict(list)
    for p in items:
        merged[afind(atom[p])].append(p)
    return [m for m in merged.values() if len(m) > 1]


def assign_dup_groups(paths: list, hashes: dict, threshold: int,
                      embeddings: dict | None = None, dup_sim: float = 0.92,
                      times: dict | None = None, dup_window: float = 600.0,
                      dup_cohesion: float = 0.90,
                      burst_gap: float = 8.0, burst_sim: float = 0.80,
                      burst_span: float = 30.0
                      ) -> tuple[dict, list]:
    """Assign fine near-duplicate group ids. First a similarity graph joins two
    images when EITHER their perceptual hashes are within `threshold` Hamming
    distance (literal re-saves / crops — matched regardless of time), OR — when
    CLIP `embeddings` are supplied — their cosine similarity is >= `dup_sim` AND
    they were taken within `dup_window` seconds of each other. CLIP catches
    "same shot, slight motion" pairs that phash misses (phash can read ~32/64
    for those); the time window keeps look-alikes from different moments out of
    the same group. phash remains the time-independent exact-dup signal so
    legacy behaviour is preserved when `embeddings` is None.

    The graph's connected components are only *candidates*: single linkage
    chains unrelated frames together, so each multi-frame candidate is then
    re-clustered by `_cohesion_split` and any group whose members aren't
    mutually cohesive (average cosine below `dup_cohesion`) is broken up. This
    keeps the loose-but-genuine pair (linked at, say, cos 0.93) together while
    shattering the 50-frame chains the segmenter used to emit.

    Finally a *strictly additive* burst pass runs (`_burst_merge`): it treats the
    cohesion result as fixed atoms — each near-dup group and each still-loose
    frame — and only ever MERGES whole atoms that fall in the same tight temporal
    run (consecutive gap <= `burst_gap`s, run span <= `burst_span`s, consecutive
    cosine >= `burst_sim`). It never re-clusters or ejects, so an existing group
    can grow or two can merge, but nothing that was grouped is ever broken. This
    rescues bursts whose CLIP cosine is dragged under `dup_sim` by drifting
    mist/haze (time compensating for the weakened visual signal) without the
    non-monotonic churn that re-running cohesion on enlarged components causes.
    Disabled when `burst_gap` <= 0 or times/embeddings are absent.

    Returns ({path: group_id}, [groups]); only multi-member groups are kept and
    ids/members are ordered by earliest capture time for determinism."""
    items = [p for p in paths if p in hashes]
    parent = {p: p for p in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def t(p):
        return (times or {}).get(p, 0.0)

    has_emb = embeddings is not None
    n = len(items)
    for i in range(n):
        a = items[i]
        for j in range(i + 1, n):
            b = items[j]
            near = (hashes[a] - hashes[b]) <= threshold
            if (not near and has_emb and a in embeddings and b in embeddings
                    and (times is None or abs(t(a) - t(b)) <= dup_window)):
                near = float(np.dot(embeddings[a], embeddings[b])) >= dup_sim
            if near:
                union(a, b)

    from collections import defaultdict
    comps: dict = defaultdict(list)
    for p in items:
        comps[find(p)].append(p)

    # Candidate components -> cohesion-split blobs; tight bursts pass through.
    groups: list = []
    for comp in comps.values():
        if len(comp) <= 1:
            continue
        if has_emb and len(comp) > 2:
            groups.extend(
                _cohesion_split(comp, embeddings, hashes, threshold, dup_cohesion))
        else:
            groups.append(comp)
    groups = [m for m in groups if len(m) > 1]

    # Strictly-additive burst merge over the settled cohesion result.
    if (has_emb and times is not None and burst_gap and burst_gap > 0
            and burst_sim is not None):
        groups = _burst_merge(items, groups, embeddings, times,
                              burst_gap, burst_sim, burst_span)

    groups.sort(key=lambda m: (min(t(p) for p in m), str(min(m, key=str))))

    path_to_group: dict = {}
    for gid, group in enumerate(groups):
        group.sort(key=lambda p: (t(p), str(p)))
        for p in group:
            path_to_group[p] = gid
    return path_to_group, groups


def dup_centrality(groups: list, embeddings: dict | None) -> dict:
    """Per-image centrality within its near-duplicate group: the mean CLIP
    cosine to the group's other members. High = the representative frame the
    rest cluster around; low = an edge frame. The UI leads each group with its
    most-central photo so the hero is never a visual outlier. Returns {} (and
    callers fall back to quality) when embeddings aren't available."""
    if not embeddings:
        return {}
    out: dict = {}
    for g in groups:
        members = [p for p in g if p in embeddings]
        if len(members) < 2:
            continue
        for p in members:
            sims = [float(np.dot(embeddings[p], embeddings[q]))
                    for q in members if q is not p]
            out[p] = round(sum(sims) / len(sims), 4)
    return out


def coarsen_scenes_for_dups(paths: list, scene_of: dict,
                            dup_groups: list, times: dict | None = None
                            ) -> tuple[dict, int]:
    """Coarsen a scene assignment so every near-duplicate group nests inside a
    single scene. Near-dups are the finest grain, so scenes must contain them:
    images sharing an initial (multi-member) scene stay together, and any scenes
    a dup group spans are merged via union-find. A singleton-scene image is
    pulled into a scene only when a dup group ties it there. This fixes the case
    where the sequential segmenter splits a continuous shoot between two genuine
    near-duplicates (which, being scene-bounded, could otherwise never merge).

    Returns ({path: scene_id|None}, n_scenes); lone images keep None."""
    from collections import defaultdict
    parent = {p: p for p in paths}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_scene: dict = defaultdict(list)
    for p in paths:
        s = scene_of.get(p)
        if s is not None:
            by_scene[s].append(p)
    for members in by_scene.values():
        for q in members[1:]:
            union(members[0], q)

    for group in dup_groups:
        g = [p for p in group if p in parent]
        for q in g[1:]:
            union(g[0], q)

    def t(p):
        return (times or {}).get(p, 0.0)

    comps: dict = defaultdict(list)
    for p in paths:
        comps[find(p)].append(p)
    multi = [m for m in comps.values() if len(m) > 1]
    multi.sort(key=lambda m: (min(t(p) for p in m), str(min(m, key=str))))

    scene_assign: dict = {p: None for p in paths}
    for sid, members in enumerate(multi):
        for p in members:
            scene_assign[p] = sid
    return scene_assign, len(multi)


# ── Capture time (EXIF) + scene grouping ──────────────────────────────────────

def read_capture_time(path) -> float | None:
    """Best-effort capture timestamp (epoch seconds) from EXIF: DateTimeOriginal,
    then DateTimeDigitized, then the IFD0 DateTime. Returns None when absent or
    unparseable, so callers can fall back to filesystem mtime."""
    try:
        with load_rgb(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            dt = None
            try:
                sub = exif.get_ifd(0x8769)          # ExifIFD pointer
                dt = sub.get(0x9003) or sub.get(0x9004)  # DateTimeOriginal/Digitized
            except Exception:
                pass
            dt = dt or exif.get(0x0132)             # IFD0 DateTime
            if not dt:
                return None
            return datetime.strptime(str(dt).strip(), "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def compute_clip_embeddings(paths: list, device: str,
                            batch_size: int = 64, progress=None) -> dict:
    """L2-normalised CLIP ViT-B/32 image embeddings per path, for semantic scene
    similarity. Standardised on ViT-B/32 regardless of the aesthetic backend so
    scene grouping is consistent. Returns {path: 1-D float32 ndarray}.

    `progress(done, total)`, if given, fires after each batch for live progress."""
    import torch

    model, prep, _ = load_openclip_b32(device)

    embs: dict = {}
    done, n = 0, len(paths)
    for t, bpaths in iter_image_batches(paths, prep, device,
                                        batch_size, "Scene embeddings (CLIP)"):
        with torch.no_grad(), torch.amp.autocast(device):
            f = model.encode_image(t)
            f = f / f.norm(dim=-1, keepdim=True)
        for p, v in zip(bpaths, f.cpu().float().numpy()):
            embs[p] = v
        done += len(bpaths)
        if progress is not None:
            progress(done, n)

    del model
    return embs


def _visually_similar(a, b, embeddings: dict | None, hashes: dict | None,
                      sim: float, phash_dist: int) -> bool:
    """Whether two images look like the same scene. Prefers CLIP cosine when
    embeddings are present, else falls back to perceptual-hash distance."""
    if embeddings is not None and a in embeddings and b in embeddings:
        return float(np.dot(embeddings[a], embeddings[b])) >= sim
    if hashes is not None and a in hashes and b in hashes:
        return (hashes[a] - hashes[b]) <= phash_dist
    return False


def group_scenes(paths: list, times: dict,
                 embeddings: dict | None = None, hashes: dict | None = None,
                 big_gap: float = 3600.0, small_gap: float = 120.0,
                 sim: float = 0.85, phash_dist: int = 18) -> tuple[dict, int]:
    """Segment images into rough "scenes" in capture-time order. EXIF time is the
    primary signal; visual similarity (CLIP cosine, or phash fallback) refines it.

    Boundary between two time-adjacent images when:
      - the gap exceeds `big_gap` (always a new scene), or
      - the gap exceeds `small_gap` AND they are not visually similar.
    Tight bursts (gap <= small_gap) always stay together.

    Returns ({path: scene_id|None}, n_scenes). Like dup groups, only multi-member
    scenes get an id; lone images get None."""
    if not paths:
        return {}, 0
    ordered = sorted(paths, key=lambda p: (times.get(p, 0.0), str(p)))
    segments: list = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        dt = times.get(cur, 0.0) - times.get(prev, 0.0)
        similar = _visually_similar(prev, cur, embeddings, hashes, sim, phash_dist)
        if dt > big_gap or (dt > small_gap and not similar):
            segments.append([cur])
        else:
            segments[-1].append(cur)

    scene_of: dict = {}
    sid = 0
    for seg in segments:
        if len(seg) > 1:
            for p in seg:
                scene_of[p] = sid
            sid += 1
        else:
            scene_of[seg[0]] = None
    return scene_of, sid
