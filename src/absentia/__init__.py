"""absentia — find the holes your code already drew."""

import sys as _sys
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Read the version from the installed package's metadata at import
# time. This avoids the historical drift bug where the version in
# `pyproject.toml` was bumped (via scripts/release.sh) but a
# hardcoded `__version__ = "X.Y.Z"` here was forgotten — leaving
# `absentia --version` reporting the old number after a release.
# When running from source without an install (e.g. an editable
# install during early dev or someone running `python -m absentia`
# from a clone without `pip install -e .`), fall back to "0.0.0+unknown".
try:
    __version__ = _pkg_version("absentia")
except _PackageNotFoundError:
    __version__ = "0.0.0+unknown"

# Default recursion limit (1000) is too tight for deeply-nested ASTs.
# Real-world example: rust-lang/rust crashed absentia's recursive call
# walker on its compiler source. 5000 is comfortably under the OS stack
# limit on every platform we target while handling any realistic source
# code. Per-extractor walkers should still prefer iterative DFS (see
# entities.walk_subtree) — this is defense-in-depth.
if _sys.getrecursionlimit() < 5000:
    _sys.setrecursionlimit(5000)
