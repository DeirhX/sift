"""`python -m sift ...` entry point — defers to the CLI dispatcher."""
from sift.cli import main

raise SystemExit(main())
