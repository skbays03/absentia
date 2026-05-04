"""Command-line entry point.

MVP: only the `check` subcommand exists. Bare `lacuna` prints help. The
TUI (default-when-no-subcommand) and `init`, `explain`, `suppress`,
`rules`, `groups`, `stats`, `watch` subcommands land in later passes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .entities import Entity, FeatureSet
from .features import extract_python_functions
from .mining import mine
from .output import format_gaps, format_gaps_json
from .parsing import find_python_files, parse_file
from .selectors import directory_groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lacuna",
        description="Find the holes your code already drew.",
    )
    parser.add_argument("--version", action="version", version=f"lacuna {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    check = sub.add_parser("check", help="Scan a project and print gaps.")
    check.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    check.add_argument("--min-confidence", type=float, default=0.8,
                       help="Minimum confidence for a rule (default: 0.8)")
    check.add_argument("--min-group-size", type=int, default=3,
                       help="Skip groups with fewer members (default: 3)")
    check.add_argument("--json", action="store_true", dest="as_json",
                       help="Emit machine-readable JSON instead of human text")
    check.add_argument("--quiet", action="store_true",
                       help="Suppress the stats footer (text mode only)")

    args = parser.parse_args(argv)

    if args.cmd is None:
        parser.print_help()
        return 0

    if args.cmd == "check":
        return cmd_check(
            root=Path(args.path).resolve(),
            min_confidence=args.min_confidence,
            min_group_size=args.min_group_size,
            quiet=args.quiet,
            as_json=args.as_json,
        )

    return 0


def cmd_check(
    *,
    root: Path,
    min_confidence: float,
    min_group_size: int,
    quiet: bool,
    as_json: bool = False,
) -> int:
    if not root.is_dir():
        if as_json:
            import json
            print(json.dumps({"error": f"not a directory: {root}"}))
        else:
            print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    entities: dict[str, Entity] = {}
    feature_index: dict[str, FeatureSet] = {}

    for path in find_python_files(root):
        tree_root = parse_file(path)
        if tree_root is None:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        for entity, features in extract_python_functions(tree_root, rel):
            entities[entity.id] = entity
            feature_index[entity.id] = features

    groups = directory_groups(
        ((e, feature_index[e.id]) for e in entities.values()),
        min_members=min_group_size,
    )

    # Mine each feature kind independently. Compound (cross-kind)
    # predicates land later via FP-growth.
    rules: list = []
    gaps: list = []
    for kind in ("decorator", "calls"):
        rs, gs = mine(groups, feature_index,
                      min_confidence=min_confidence, feature_kind=kind)
        rules.extend(rs)
        gaps.extend(gs)
    elapsed = time.perf_counter() - started

    rules_by_id = {r.id: r for r in rules}
    scan_stats = {
        "root": str(root),
        "duration_ms": round(elapsed * 1000, 2),
        "entities_scanned": len(entities),
        "groups": len(groups),
        "rules": len(rules),
        "min_confidence": min_confidence,
        "min_group_size": min_group_size,
    }

    if as_json:
        print(format_gaps_json(gaps, rules_by_id, entities, scan_stats=scan_stats))
    else:
        print(format_gaps(gaps, rules_by_id, entities, min_confidence=min_confidence))
        if not quiet:
            print(f"  {len(entities)} entities scanned, "
                  f"{len(groups)} groups, {len(rules)} rules in {elapsed:.2f}s")
            print()

    return 1 if gaps else 0
