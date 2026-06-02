"""sift — a two-stage photo culling toolkit.

The package is split along the stage seams of the pipeline:

    sift.audit   GPU-heavy analysis: sharpness, aesthetic scoring, captions/tags,
                 face detection + clustering, perceptual-hash / CLIP duplicate
                 grouping and scene segmentation. Produces an audit_report.json.
    sift.web     the SQLite ingest (build_db), schema/domain authority (photodb),
                 read layer (queries), reanalysis subsystem (analysis) and the
                 FastAPI review server. Only ever touches the DB + filesystem.

A single console entry point dispatches the three stages:

    sift analyze <folder> [...]   -> audit_report.json
    sift index   <report> [...]   -> photos.db + .thumbs/
    sift serve   --db <path> [..] -> the review web app
"""

__version__ = "0.1.0"
