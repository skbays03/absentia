#!/usr/bin/env python3
"""Diagnose where lacuna spends time scanning a corpus.

Run on the same target directory across machines (Mac vs WSL vs
native Linux) to see if the bottleneck is filesystem I/O,
tree-sitter parsing, lacuna's extractor, or storage commits.

Reports per-stage timing for a representative sample of files,
plus environment info (Python version, CPU count, WSL version,
filesystem type for the target). Output is plain text so it
diffs cleanly across machines.

Usage:
    python scripts/diagnose_scan.py /path/to/corpus [--sample N]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path


def env_info(target: Path) -> list[str]:
    """Print an environment block — what platform, what Python, where
    the target actually lives, etc."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("ENVIRONMENT")
    lines.append("=" * 70)
    lines.append(f"  platform:         {platform.platform()}")
    lines.append(f"  python:           {sys.version.split()[0]}")
    lines.append(f"  executable:       {sys.executable}")
    lines.append(f"  cpu_count:        {os.cpu_count()}")
    lines.append(f"  process_cpu_count: {os.process_cpu_count()}")
    lines.append(f"  target:           {target}")
    lines.append(f"  target_resolved:  {target.resolve()}")

    # Detect WSL
    wsl = ""
    if "microsoft" in platform.uname().release.lower():
        wsl = " (WSL2)" if "wsl2" in platform.uname().release.lower() else " (WSL)"
    lines.append(f"  wsl:              {wsl or '(not WSL)'}")

    # Filesystem type / mount info
    try:
        df = subprocess.run(
            ["df", "-T", str(target.resolve())],
            capture_output=True, text=True, timeout=5,
        )
        if df.returncode == 0:
            lines.append("  filesystem (df -T):")
            for line in df.stdout.strip().splitlines():
                lines.append(f"    {line}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        mount = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=5,
        )
        if mount.returncode == 0:
            target_root = str(target.resolve())
            for line in mount.stdout.splitlines():
                if any(target_root.startswith(p) for p in line.split()[:3]):
                    lines.append(f"  mount entry:      {line.strip()}")
                    break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return lines


def time_stage(label: str, fn, *args, **kwargs):
    """Run fn(*args, **kwargs), return (elapsed_seconds, result)."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return time.perf_counter() - start, result


def diagnose(target: Path, sample: int) -> int:
    lines = env_info(target)

    # Find a sample of files
    rust_files = list(target.rglob("*.rs"))
    c_files = list(target.rglob("*.c"))
    py_files = list(target.rglob("*.py"))

    lines.append("")
    lines.append("=" * 70)
    lines.append("CORPUS SHAPE")
    lines.append("=" * 70)
    lines.append(f"  *.rs files:       {len(rust_files):>7,d}")
    lines.append(f"  *.c  files:       {len(c_files):>7,d}")
    lines.append(f"  *.py files:       {len(py_files):>7,d}")

    # Take up to `sample` of each kind
    samples: list[tuple[str, list[Path]]] = []
    if rust_files:
        samples.append(("rust", rust_files[:sample]))
    if c_files:
        samples.append(("c", c_files[:sample]))
    if py_files:
        samples.append(("python", py_files[:sample]))

    if not samples:
        lines.append("")
        lines.append("No source files found — nothing to diagnose.")
        print("\n".join(lines))
        return 1

    # Try to import the corresponding tree-sitter grammars
    grammars = {}
    for lang, _ in samples:
        try:
            if lang == "rust":
                import tree_sitter_rust as ts
            elif lang == "c":
                import tree_sitter_c as ts
            elif lang == "python":
                import tree_sitter_python as ts
            else:
                continue
            from tree_sitter import Language, Parser
            grammars[lang] = Parser(Language(ts.language()))
        except Exception as e:
            lines.append(f"  ! couldn't load tree-sitter-{lang}: {e}")

    # Per-language per-stage timing
    for lang, files in samples:
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"PER-FILE TIMING — {lang} ({len(files)} files sampled)")
        lines.append("=" * 70)
        lines.append(
            f"  {'file':<50s}  {'size':>9s}  "
            f"{'stat':>6s}  {'read':>7s}  {'hash':>6s}  {'parse':>7s}  {'walk':>6s}"
        )

        stat_times = []
        read_times = []
        hash_times = []
        parse_times = []
        walk_times = []

        parser = grammars.get(lang)
        for path in files:
            # stat
            t_stat, stat_result = time_stage("stat", path.stat)
            stat_times.append(t_stat)

            # read
            t_read, content = time_stage("read", path.read_bytes)
            read_times.append(t_read)

            # hash
            t_hash, _ = time_stage(
                "hash", lambda c=content: hashlib.sha256(c).hexdigest(),
            )
            hash_times.append(t_hash)

            # parse
            t_parse, tree = (None, None)
            if parser is not None:
                t_parse, tree = time_stage("parse", parser.parse, content)
                parse_times.append(t_parse)

            # walk (count nodes via iterative DFS)
            t_walk = None
            if tree is not None:
                def _count_nodes(root):
                    n = 0
                    stack = [root]
                    while stack:
                        node = stack.pop()
                        n += 1
                        stack.extend(node.children)
                    return n
                t_walk, n_nodes = time_stage("walk", _count_nodes, tree.root_node)
                walk_times.append(t_walk)

            try:
                rel = str(path.relative_to(target))
            except ValueError:
                rel = str(path)
            if len(rel) > 50:
                rel = "..." + rel[-47:]

            size = stat_result.st_size
            t_parse_str = f"{t_parse * 1000:>5.0f}ms" if t_parse is not None else "  -- "
            t_walk_str = f"{t_walk * 1000:>4.0f}ms" if t_walk is not None else " -- "
            lines.append(
                f"  {rel:<50s}  {size:>9,d}  "
                f"{t_stat * 1000:>4.0f}ms  {t_read * 1000:>5.0f}ms  "
                f"{t_hash * 1000:>4.0f}ms  {t_parse_str}  {t_walk_str}"
            )

        # Summary stats
        lines.append("")
        lines.append(f"  {lang} stage totals (sum of {len(files)} files):")
        if stat_times:
            lines.append(
                f"    stat:   {sum(stat_times) * 1000:>7.0f}ms total  "
                f"(median {statistics.median(stat_times) * 1000:.1f}ms, "
                f"max {max(stat_times) * 1000:.0f}ms)"
            )
        if read_times:
            lines.append(
                f"    read:   {sum(read_times) * 1000:>7.0f}ms total  "
                f"(median {statistics.median(read_times) * 1000:.1f}ms, "
                f"max {max(read_times) * 1000:.0f}ms)"
            )
        if parse_times:
            lines.append(
                f"    parse:  {sum(parse_times) * 1000:>7.0f}ms total  "
                f"(median {statistics.median(parse_times) * 1000:.1f}ms, "
                f"max {max(parse_times) * 1000:.0f}ms)"
            )
        if walk_times:
            lines.append(
                f"    walk:   {sum(walk_times) * 1000:>7.0f}ms total  "
                f"(median {statistics.median(walk_times) * 1000:.1f}ms, "
                f"max {max(walk_times) * 1000:.0f}ms)"
            )

    # Final summary: where does time go?
    lines.append("")
    lines.append("=" * 70)
    lines.append("INTERPRETATION GUIDE")
    lines.append("=" * 70)
    lines.append(
        "  - High stat / read times relative to parse → filesystem bottleneck.\n"
        "    On WSL, this typically means your target is on /mnt/c/ (the\n"
        "    Windows filesystem accessed via 9P). Fix: clone into ~/ on the\n"
        "    WSL ext4 filesystem instead.\n"
        "  - High parse times → tree-sitter binding issue or unusual grammar.\n"
        "    Same numbers across platforms = environment-independent.\n"
        "  - High walk times → AST is huge (deep recursion or many nodes).\n"
        "    Compare per-language; some files are just big.\n"
    )

    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="diagnose_scan",
        description=(
            "Diagnose lacuna's per-stage scan timing. Run with the same "
            "target on Mac and WSL, diff the two outputs to see where the "
            "discrepancy lives."
        ),
    )
    p.add_argument("target", type=Path, help="Path to scan (e.g., /tmp/linux/rust/syn)")
    p.add_argument(
        "--sample", type=int, default=10,
        help="How many files of each language to time (default: 10)",
    )
    args = p.parse_args(argv)

    if not args.target.is_dir():
        print(f"diagnose_scan: not a directory: {args.target}", file=sys.stderr)
        return 2

    return diagnose(args.target, args.sample)


if __name__ == "__main__":
    sys.exit(main())
