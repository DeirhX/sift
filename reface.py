#!/usr/bin/env python3
"""
reface.py — Re-run face detection on an existing audit_report.json without
             re-running scoring, captions, or duplicate detection.

Useful for quickly iterating on face detection parameters without waiting
for the full 10-minute pipeline.

Usage:
  python reface.py <audit_report.json> [options]

Options:
  --out <path>          Output path (default: overwrites input)
  --face-ref NAME=PATH  Named reference photo (repeatable)
  --face-min-size PX    Min face width in pixels for MTCNN (default: 80)
  --face-min-rel F      Min face width as fraction of image width (default: 0.04)
  --face-eps F          DBSCAN cosine-distance epsilon (default: 0.50)
  --min-prob F          MTCNN min detection probability (default: 0.90)
  --dry-run             Print stats without writing anything

Typical iteration workflow:
  # First pass — conservative (fewer false positives)
  python reface.py audit_report.json --face-min-rel 0.04

  # Loosen if subject faces are missed
  python reface.py audit_report.json --face-min-rel 0.03 --face-eps 0.55

  # After tuning, regenerate the viewer
  python generate_viewer.py audit_report.json
"""

import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict

# Make photo_audit importable regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from photo_audit import run_faces


def analyse(report: dict) -> None:
    """Print a quick breakdown of current face detection results."""
    images = report["images"]
    cluster_counts: dict[int, int] = defaultdict(int)
    size_buckets = [0] * 6  # 0-1%, 1-2%, 2-4%, 4-8%, 8-15%, 15%+
    thresholds = [0.01, 0.02, 0.04, 0.08, 0.15, 1.0]
    total_faces = 0

    for img in images:
        w = img.get("imgw", 1) or 1
        for f in img.get("faces", []):
            total_faces += 1
            cluster_counts[f["cluster_id"]] += 1
            x1, y1, x2, y2 = f["bbox"]
            rel = (x2 - x1) / w
            for bi, t in enumerate(thresholds):
                if rel < t:
                    size_buckets[bi] += 1
                    break

    print(f"\n  Total face instances : {total_faces}")
    print(f"  Images with faces    : {sum(1 for img in images if img.get('faces'))}")
    print(f"  Identity clusters    : {len(cluster_counts)}")
    print()
    labels = ["<1%", "1-2%", "2-4%", "4-8%", "8-15%", ">=15%"]
    print("  Face size distribution (face width / image width):")
    for label, count in zip(labels, size_buckets):
        bar = "#" * min(count, 50)
        print(f"    {label:6s}: {count:4d}  {bar}")
    print()
    if cluster_counts:
        print("  Top 10 clusters by size:")
        for cid, n in sorted(cluster_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    cluster {cid:3d}: {n:4d} faces")


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("report",           help="Path to audit_report.json")
    ap.add_argument("--out",            default=None)
    ap.add_argument("--face-ref",       action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--face-min-size",  type=int,   default=80,   metavar="PX")
    ap.add_argument("--face-min-rel",   type=float, default=0.04, metavar="F")
    ap.add_argument("--face-eps",       type=float, default=0.50, metavar="F")
    ap.add_argument("--min-prob",       type=float, default=0.90, metavar="F")
    ap.add_argument("--dry-run",        action="store_true",
                    help="Show current stats only, do not reprocess")
    args = ap.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: {report_path} not found"); sys.exit(1)

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    print(f"Loaded report: {report_path}")
    print(f"  {report['total_images']} images, backend={report.get('backend')}")

    if args.dry_run:
        print("\nCurrent face detection state:")
        analyse(report)
        return

    # ── Parse paths & refs ────────────────────────────────────────────────────
    paths = [Path(img["path"]) for img in report["images"]]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"Warning: {len(missing)} image paths not found on disk")

    refs: dict[str, Path] = {}
    for item in args.face_ref:
        if "=" in item:
            name, rpath = item.split("=", 1)
            refs[name.strip()] = Path(rpath.strip())
        else:
            print(f"Warning: --face-ref '{item}' ignored (expected NAME=PATH)")

    # ── Re-run face detection ─────────────────────────────────────────────────
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\nRunning face detection on {device}  "
          f"(min_size={args.face_min_size}px, "
          f"min_rel={args.face_min_rel}, "
          f"eps={args.face_eps})")

    face_data, img_sizes = run_faces(
        paths, device,
        face_refs=refs or None,
        min_prob=args.min_prob,
        min_face_size=args.face_min_size,
        min_face_rel=args.face_min_rel,
        eps=args.face_eps,
    )

    # ── Patch report records ───────────────────────────────────────────────────
    for img in report["images"]:
        p = Path(img["path"])
        img["faces"] = face_data.get(p, [])
        if p in img_sizes:
            img["imgw"], img["imgh"] = img_sizes[p]

    n_face_images = sum(1 for img in report["images"] if img.get("faces"))
    cluster_ids = {
        f["cluster_id"]
        for img in report["images"]
        for f in img.get("faces", [])
        if f["cluster_id"] >= 0
    }
    n_clusters = len(cluster_ids)

    report["face_model"]   = "mtcnn+vggface2"
    report["faces_images"] = n_face_images
    report["face_clusters"] = n_clusters

    # ── Write output (before analyse so a display crash never loses data) ────────
    out_path = Path(args.out) if args.out else report_path
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report saved: {out_path}")

    print("\nNew face detection state:")
    analyse(report)
    print(f"\nNow regenerate the viewer:")
    viewer_out = out_path.parent / "audit_viewer.html"
    print(f"  python generate_viewer.py \"{out_path}\"")
    print(f"  start \"{viewer_out}\"")


if __name__ == "__main__":
    main()
