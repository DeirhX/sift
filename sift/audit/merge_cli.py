"""`sift merge` — combine per-root audit reports into one library report.

    sift merge ROOT_A/audit_report.json ROOT_B/audit_report.json \
        --embed-store LIB/.embeddings.sqlite --out LIB/audit_report.json

Reads the shared content-hash embedding cache that every root wrote during
analyze, renumbers groups/clusters globally, fixes the sharpness basis, and
re-clusters faces library-wide. The output feeds straight into `sift index`.
"""
import argparse
from pathlib import Path

from .merge import merge_reports


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="sift merge", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+",
                    help="Per-root audit_report.json files to combine")
    ap.add_argument("--out", required=True,
                    help="Path for the merged library audit_report.json")
    ap.add_argument("--embed-store", default=None,
                    help="Shared content-hash embedding cache "
                         "(default: <out_dir>/.embeddings.sqlite)")
    ap.add_argument("--eps", type=float, default=0.50,
                    help="DBSCAN cosine eps for global face clustering (default 0.50)")
    ap.add_argument("--recalibrate-sharpness", action="store_true",
                    help="Recompute the sharpness basis from this run instead of "
                         "reusing the prior basis in --out (scores may shift)")
    args = ap.parse_args()

    out_path = Path(args.out)
    store_path = (Path(args.embed_store) if args.embed_store
                  else out_path.parent / ".embeddings.sqlite")

    missing = [r for r in args.reports if not Path(r).exists()]
    if missing:
        ap.error("report(s) not found: " + ", ".join(missing))

    merge_reports(args.reports, store_path, out_path,
                  eps=args.eps,
                  recalibrate_sharpness=args.recalibrate_sharpness)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
