"""Profile a `lacuna check` run with cProfile and dump the top hotspots.

Optimization-plan #12 (profile-guided pickup) made repeatable. After
the structural perf wins shipped (mining 30×, mypyc compilation, Query
API migration), this script answers "is the structural-reasoning model
correct, or is there a non-obvious hotspot we missed?"

Usage:
  python scripts/profile_scan.py /tmp/linux               # full kernel scan
  python scripts/profile_scan.py /tmp/redis --top 30      # custom top-N
  python scripts/profile_scan.py /tmp/linux --output /tmp/lacuna.prof
  python scripts/profile_scan.py /tmp/linux --no-cold     # warm scan only

Notes:
  - jobs=1 by default. cProfile only sees the main process; ProcessPool
    workers don't get traced. jobs=1 gives a clean profile of the work
    that happens in the main process (orchestration + mining +
    enrichment + storage + output). Pass --jobs N to mirror a real run,
    accepting that worker internals will show up only as "wait on pool".
  - mypyc-compiled hot paths (mining.py, symmetry.py) appear as opaque
    native calls — that's intentional. The question this script answers
    is "what else is slow?", not "how fast is the part we already
    optimized?".
  - --cold is the default so the parse stage is exercised, not just
    cache hits. Use --no-cold for a warm rescan profile (small +
    incremental-cache-hit-dominated).
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("corpus", type=Path, help="Path to corpus to scan")
    p.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker count. Default 1 for clean profile (see notes).",
    )
    p.add_argument(
        "--top",
        type=int,
        default=20,
        help="Top-N hotspots to print (default 20).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write raw .prof file here (default: corpus_name.prof in CWD).",
    )
    p.add_argument(
        "--no-cold",
        action="store_true",
        help="Don't blow away .lacuna/ before profile (warm rescan).",
    )
    return p.parse_args()


def render_top(stats: pstats.Stats, sort: str, top: int, label: str) -> str:
    """Render the top-N rows from a Stats object as a string."""
    buf = io.StringIO()
    stats.stream = buf  # type: ignore[attr-defined]
    stats.sort_stats(sort).print_stats(top)
    return f"\n{'=' * 78}\n{label}  (sorted by {sort})\n{'=' * 78}\n{buf.getvalue()}"


def main() -> int:
    args = parse_args()

    if not args.corpus.exists():
        print(f"ERROR: corpus path does not exist: {args.corpus}", file=sys.stderr)
        return 1

    if not args.no_cold:
        lacuna_dir = args.corpus / ".lacuna"
        if lacuna_dir.exists():
            print(f"  Cold scan: removing {lacuna_dir}/ ...")
            shutil.rmtree(lacuna_dir)

    output = args.output or Path.cwd() / f"{args.corpus.name}.prof"

    print(f"  Profiling lacuna check on {args.corpus}")
    print(f"  jobs={args.jobs}, top={args.top}")
    print(f"  Output: {output}")
    print()

    # Run lacuna in-process so cProfile sees the work. Importing inside
    # main() so the import itself is profiled (extractor discovery is
    # part of cold-start cost we want visibility on).
    started = time.perf_counter()
    profiler = cProfile.Profile()
    profiler.enable()

    # Use subprocess instead of importing because lacuna's CLI does
    # signal handlers + sys.exit; subprocess isolates the profile from
    # the rest of this script. cProfile.run() can't profile a child
    # process, so we instead use cProfile against an in-process call.
    from lacuna.cli import main as lacuna_main

    rc = 0
    try:
        # Mimic `lacuna check <corpus> --jobs N --quiet`. --quiet
        # suppresses progress UI noise that would inflate the profile
        # with rendering overhead unrelated to the scan engine.
        sys.argv = [
            "lacuna",
            "check",
            str(args.corpus),
            "--jobs",
            str(args.jobs),
            "--quiet",
        ]
        lacuna_main()
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0

    profiler.disable()
    elapsed = time.perf_counter() - started

    profiler.dump_stats(str(output))

    stats = pstats.Stats(output.as_posix())
    stats.strip_dirs()

    print(render_top(stats, "cumulative", args.top, "TOP BY CUMULATIVE TIME"))
    print(render_top(stats, "tottime", args.top, "TOP BY TOTAL TIME (excl. callees)"))
    print(render_top(stats, "ncalls", args.top, "TOP BY CALL COUNT"))

    print(f"\n  Wall-clock (incl. profile overhead): {elapsed:.2f}s")
    print(f"  Raw profile: {output}")
    print(f"  Inspect:     python -m pstats {output}")
    print(f"  lacuna exit: {rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
