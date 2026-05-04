"""Command-line entry point.

Subcommands:
  init    create lacuna.toml + .lacuna/ in the current directory
  check   scan a project and print gaps (text or JSON)

The TUI (default-when-no-subcommand) and `explain`, `suppress`, `rules`,
`groups`, `stats`, `watch` land in later passes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import hashlib
import json

from . import __version__
from .config import Config, find_config
from .entities import Entity, FeatureSet
from .features import extract_python_functions
from .mining import mine
from .output import format_gaps, format_gaps_json
from .parsing import find_python_files, parse_source
from .selectors import decorator_groups, directory_groups
from .storage import Storage


_INIT_TEMPLATE = """\
# lacuna configuration. Run `lacuna check` from this directory.
# Every section is optional; defaults are sensible.

[scan]
include   = ["."]
exclude   = []
languages = ["python"]

[mining]
min_confidence = 0.8
min_group_size = 3

[selectors.directory]
enabled     = true
min_members = 3
kind_filter = ["function"]

[selectors.decorator]
enabled     = true
min_members = 3
exclude     = ["@property", "@staticmethod", "@classmethod"]
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lacuna",
        description="Find the holes your code already drew.",
    )
    parser.add_argument("--version", action="version", version=f"lacuna {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="Create lacuna.toml + .lacuna/ in the current dir.")
    init.add_argument("path", nargs="?", default=".", help="Where to init (default: cwd)")
    init.add_argument("--force", action="store_true",
                      help="Overwrite an existing lacuna.toml")

    check = sub.add_parser("check", help="Scan a project and print gaps.")
    check.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    check.add_argument("--config", type=Path, default=None,
                       help="Path to lacuna.toml (default: search root upward)")
    check.add_argument("--min-confidence", type=float, default=None,
                       help="Override mining.min_confidence")
    check.add_argument("--min-group-size", type=int, default=None,
                       help="Override mining.min_group_size")
    check.add_argument("--json", action="store_true", dest="as_json",
                       help="Emit machine-readable JSON instead of human text")
    check.add_argument("--quiet", action="store_true",
                       help="Suppress the stats footer (text mode only)")

    args = parser.parse_args(argv)

    if args.cmd is None:
        parser.print_help()
        return 0

    if args.cmd == "init":
        return cmd_init(root=Path(args.path).resolve(), force=args.force)

    if args.cmd == "check":
        root = Path(args.path).resolve()
        config = _load_config(root, args.config)
        # CLI flags override config values for the mining knobs.
        from dataclasses import replace
        mining = config.mining
        if args.min_confidence is not None:
            mining = replace(mining, min_confidence=args.min_confidence)
        if args.min_group_size is not None:
            mining = replace(mining, min_group_size=args.min_group_size)
        config = replace(config, mining=mining)

        return cmd_check(
            root=root,
            config=config,
            quiet=args.quiet,
            as_json=args.as_json,
        )

    return 0


def _load_config(root: Path, explicit: Path | None) -> Config:
    if explicit is not None:
        return Config.from_file(explicit)
    discovered = find_config(root)
    if discovered is not None:
        return Config.from_file(discovered)
    return Config()


def cmd_init(*, root: Path, force: bool) -> int:
    if not root.is_dir():
        print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    config_path = root / "lacuna.toml"
    state_dir = root / ".lacuna"

    if config_path.exists() and not force:
        print(f"lacuna: {config_path} already exists. Use --force to overwrite.",
              file=sys.stderr)
        return 1

    config_path.write_text(_INIT_TEMPLATE)
    state_dir.mkdir(exist_ok=True)
    (state_dir / ".gitignore").write_text("*\n")
    (state_dir / "version").write_text("1\n")

    gitignore = root / ".gitignore"
    if gitignore.exists():
        existing_lines = gitignore.read_text().splitlines()
        if ".lacuna/" not in existing_lines and ".lacuna" not in existing_lines:
            with gitignore.open("a") as fh:
                if existing_lines and existing_lines[-1] != "":
                    fh.write("\n")
                fh.write(".lacuna/\n")

    print(f"Initialized lacuna in {root}")
    print(f"  - Wrote lacuna.toml")
    print(f"  - Created .lacuna/ (gitignored)")
    print()
    print("Run `lacuna check` to start exploring.")
    return 0


def cmd_check(
    *,
    root: Path,
    config: Config,
    quiet: bool = False,
    as_json: bool = False,
) -> int:
    if not root.is_dir():
        if as_json:
            print(json.dumps({"error": f"not a directory: {root}"}))
        else:
            print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    from datetime import datetime, timezone
    started = time.perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    state_dir = root / ".lacuna"

    with Storage(state_dir) as storage:
        run_id = storage.begin_run()
        files_seen, files_unchanged = _scan_incremental(root, storage, run_id)
        entities, feature_index = storage.load_all()
        storage.commit()

        items = [(e, feature_index[e.id]) for e in entities.values()]
        groups: list = []
        if config.selectors.directory.enabled:
            groups.extend(directory_groups(
                items,
                min_members=config.selectors.directory.min_members,
                kind_filter=config.selectors.directory.kind_filter,
            ))
        if config.selectors.decorator.enabled:
            groups.extend(decorator_groups(
                items,
                min_members=config.selectors.decorator.min_members,
                exclude=config.selectors.decorator.exclude,
            ))

        rules: list = []
        gaps: list = []
        for kind in ("decorator", "calls"):
            rs, gs = mine(groups, feature_index,
                          min_confidence=config.mining.min_confidence,
                          feature_kind=kind)
            rules.extend(rs)
            gaps.extend(gs)
        elapsed = time.perf_counter() - started

        storage.end_run(
            run_id,
            duration_ms=round(elapsed * 1000, 2),
            entities_scanned=len(entities),
            rules_discovered=len(rules),
            gaps_found=len(gaps),
        )

    rules_by_id = {r.id: r for r in rules}
    scan_stats = {
        "root": str(root),
        "started_at": started_iso,
        "duration_ms": round(elapsed * 1000, 2),
        "entities_scanned": len(entities),
        "files_seen": files_seen,
        "files_unchanged": files_unchanged,
        "groups": len(groups),
        "rules": len(rules),
        "min_confidence": config.mining.min_confidence,
        "min_group_size": config.mining.min_group_size,
    }
    _write_last_run(state_dir, scan_stats, run_id, len(gaps))

    if as_json:
        print(format_gaps_json(gaps, rules_by_id, entities, scan_stats=scan_stats))
    else:
        print(format_gaps(gaps, rules_by_id, entities,
                          min_confidence=config.mining.min_confidence))
        if not quiet:
            cache_note = (
                f" ({files_unchanged} unchanged)" if files_unchanged else ""
            )
            print(f"  {len(entities)} entities scanned, "
                  f"{len(groups)} groups, {len(rules)} rules in "
                  f"{elapsed:.2f}s{cache_note}")
            print()

    return 1 if gaps else 0


def _scan_incremental(
    root: Path, storage: Storage, run_id: int
) -> tuple[int, int]:
    """Walk the corpus, reusing cached entities/features for unchanged files.

    Returns ``(files_seen, files_unchanged)`` for the run summary.
    """
    cached = storage.all_file_hashes()
    seen_paths: set[str] = set()
    files_unchanged = 0

    for path in find_python_files(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        seen_paths.add(rel)

        try:
            content = path.read_bytes()
        except OSError:
            continue

        current_hash = hashlib.sha256(content).hexdigest()

        if cached.get(rel) == current_hash:
            storage.upsert_file(rel, current_hash, run_id)
            files_unchanged += 1
            continue

        # Changed or new — re-parse and replace this file's rows.
        storage.delete_entities_for_file(rel)
        tree_root = parse_source(content)
        new_entities: dict[str, Entity] = {}
        new_features: dict[str, FeatureSet] = {}
        for entity, features in extract_python_functions(tree_root, rel):
            new_entities[entity.id] = entity
            new_features[entity.id] = features
        storage.save_entities_and_features(new_entities, new_features)
        storage.upsert_file(rel, current_hash, run_id)

    # Tombstone files that disappeared from disk since the last run.
    for stale in set(cached) - seen_paths:
        storage.delete_file(stale)

    return len(seen_paths), files_unchanged


def _write_last_run(
    state_dir: Path, scan_stats: dict, run_id: int, gaps_found: int
) -> None:
    summary = {
        "run_id": run_id,
        "started_at": scan_stats.get("started_at"),  # populated by caller if desired
        "duration_ms": scan_stats["duration_ms"],
        "entities_scanned": scan_stats["entities_scanned"],
        "files_seen": scan_stats["files_seen"],
        "files_unchanged": scan_stats["files_unchanged"],
        "rules_discovered": scan_stats["rules"],
        "gaps_found": gaps_found,
    }
    (state_dir / "last_run.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
