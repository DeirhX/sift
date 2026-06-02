"""Pydantic response models for the API — the single source of truth for the
shapes the frontend consumes.

These exist so FastAPI emits real response schemas in ``/openapi.json``, which
the frontend codegens into TypeScript types (``npm run codegen``). That makes
the Python <-> TS DTO contract impossible to drift silently: rename a field here
and the frontend stops type-checking until it's updated too.

Design rules that keep these from *changing* the JSON the server already sends
(the existing test-suite asserts the exact shapes):

- Read DTOs emit ``null`` for absent values (the UI reads the keys), so nullable
  fields are ``X | None`` with no exclusion.
- Items inside groups/scenes carry an extra ``matches`` flag; that's a *separate*
  model (``GroupedImageItem``) so plain ``/api/images`` items never grow the key.
- Endpoints whose payload varies (idle vs running job) use
  ``response_model_exclude_unset=True`` on the route so unset fields are omitted
  rather than serialized as ``null``.
"""
from pydantic import BaseModel


class Face(BaseModel):
    id: int
    bbox: list[float]                 # [x1, y1, x2, y2]
    prob: float | None = None
    cluster_id: int | None = None
    sharp: float | None = None
    expr: float | None = None


class ImageItem(BaseModel):
    id: int
    filename: str
    path: str
    hash: str | None = None
    combined: float | None = None
    sharpness: float | None = None
    para_aesthetic: float | None = None
    para_quality: float | None = None
    para_composition: float | None = None
    para_light: float | None = None
    para_color: float | None = None
    para_dof: float | None = None
    para_content: float | None = None
    clip_iqa: float | None = None
    dup_group: int | None = None
    dup_central: float | None = None
    scene_group: int | None = None
    capture_time: float | None = None
    caption: str | None = None
    imgw: int | None = None
    imgh: int | None = None
    face_sharp: float | None = None
    face_expr: float | None = None
    portrait: float | None = None
    decision: str | None = None
    faces: list[Face] = []
    tags: list[str] = []


class GroupedImageItem(ImageItem):
    """An image as returned inside a group/scene: same as ImageItem plus the
    `matches` flag (true when the member passes the active filter)."""
    matches: bool


class ImagesResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ImageItem]


class Group(BaseModel):
    dup_group: int
    count: int
    match_count: int
    items: list[GroupedImageItem]


class GroupsResponse(BaseModel):
    total: int
    offset: int
    limit: int
    groups: list[Group]


class Scene(BaseModel):
    scene_group: int
    count: int
    match_count: int
    dup_sets: int
    time_start: float | None = None
    time_end: float | None = None
    items: list[GroupedImageItem]


class ScenesResponse(BaseModel):
    total: int
    offset: int
    limit: int
    scenes: list[Scene]


class Location(BaseModel):
    id: int
    path: str
    exists: bool


class LocationsResponse(BaseModel):
    hash: str | None = None
    count: int
    locations: list[Location]


# ── /api/meta facets ─────────────────────────────────────────────────────────

class ClusterFacet(BaseModel):
    cluster_id: int
    name: str | None = None
    count: int


class TagFacet(BaseModel):
    tag: str
    count: int


class FolderFacet(BaseModel):
    path: str
    count: int


class Ranges(BaseModel):
    cmin: float | None = None
    cmax: float | None = None
    smin: float | None = None
    smax: float | None = None
    amin: float | None = None
    amax: float | None = None


class Counts(BaseModel):
    total: int
    with_faces: int
    with_portrait: int
    dup_groups: int
    scene_groups: int


class MetaResponse(BaseModel):
    meta: dict[str, str]
    clusters: list[ClusterFacet]
    tags: list[TagFacet]
    folders: list[FolderFacet]
    ranges: Ranges
    counts: Counts
    histograms: dict[str, list[int]]
    has_para: bool
    has_portrait: bool
    photo_roots: list[str]


# ── settings / fs ─────────────────────────────────────────────────────────────

class RootsResponse(BaseModel):
    photo_roots: list[str]


class FsCompleteResponse(BaseModel):
    entries: list[str]
    truncated: bool


# ── small acks / mutation results ─────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool


class MergeResponse(BaseModel):
    ok: bool
    moved: int


class AssignFaceResponse(BaseModel):
    ok: bool
    cluster_id: int


class AutocullResponse(BaseModel):
    groups: int
    kept: int
    deleted: int


class ApplyStatusResponse(BaseModel):
    pending: int
    applied: int
    rejected_dir: str


class ApplyResponse(BaseModel):
    moved: int
    skipped: int
    rejected_dir: str


class UndoResponse(BaseModel):
    restored: int
    skipped: int


class AnalyzeStatus(BaseModel):
    """Job status / start ack. Shapes vary (idle -> {state, commands};
    running -> full snapshot; start -> snapshot + ok), so the routes use
    response_model_exclude_unset=True and only the keys actually returned ship."""
    ok: bool | None = None
    state: str
    exit_code: int | None = None
    started: float | None = None
    ended: float | None = None
    commands: list[str] = []
