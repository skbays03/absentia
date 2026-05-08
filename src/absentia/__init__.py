"""absentia — find the holes your code already drew."""

import sys as _sys

__version__ = "0.0.1"

# Default recursion limit (1000) is too tight for deeply-nested ASTs.
# Real-world example: rust-lang/rust crashed absentia's recursive call
# walker on its compiler source. 5000 is comfortably under the OS stack
# limit on every platform we target while handling any realistic source
# code. Per-extractor walkers should still prefer iterative DFS (see
# entities.walk_subtree) — this is defense-in-depth.
if _sys.getrecursionlimit() < 5000:
    _sys.setrecursionlimit(5000)
