"""Root pytest configuration.

The default suite is model-free and fast. The ML *efficacy* suite (`tests/ml/`)
loads real model weights (multi-GB) and wants a GPU, so it is deselected unless
explicitly enabled:

    pytest -m ml --run-ml        # only the ML efficacy tests
    RUN_ML=1 pytest              # everything, including ML efficacy

This hook lives at the repo root (not under tests/) so `pytest_addoption` is
registered before command-line parsing.
"""
import os


def pytest_addoption(parser):
    parser.addoption(
        "--run-ml", action="store_true", default=False,
        help="run ML efficacy tests that load real model weights (slow)")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ml: ML efficacy test that loads real model weights "
        "(slow/opt-in; enable with --run-ml or RUN_ML=1)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-ml") or os.environ.get("RUN_ML"):
        return

    kept = []
    deselected = []
    for item in items:
        # Check the real marker, not item.keywords (which also matches the
        # tests/ml/ directory name and would affect the always-run classical tests).
        if item.get_closest_marker("ml") is not None:
            deselected.append(item)
        else:
            kept.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
