#!/usr/bin/env python3
"""scripts/update_ts.py — discover and update installed tree-sitter packages.

Lacuna's polyglot story depends on staying current with tree-sitter grammar
releases (parser bug fixes, new node types). This script runs in dev to:

  1. Discover every installed package whose name starts with ``tree-sitter``
     or ``tree_sitter`` (so it works when new languages are added without
     editing this script).
  2. Ask pip which of them have updates available.
  3. Show the diff and, with ``--apply``, upgrade them.

Default is a dry run — running the script with no flags is safe and
informational. Pass ``--apply`` to actually upgrade.

Usage:
    python scripts/update_ts.py            # dry run, show outdated only
    python scripts/update_ts.py --apply    # upgrade outdated packages
    python scripts/update_ts.py --all      # show all discovered, not just outdated
    python scripts/update_ts.py --apply --all  # upgrade everything (force)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


_PREFIXES = ("tree-sitter", "tree_sitter")


def _pip(*args: str) -> str:
    """Invoke this interpreter's pip with ``args``; return stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def installed_tree_sitter_packages() -> list[dict[str, Any]]:
    """Every installed package whose name starts with tree-sitter*."""
    raw = _pip("list", "--format=json")
    packages = json.loads(raw)
    return [p for p in packages if p["name"].startswith(_PREFIXES)]


def outdated_tree_sitter_packages() -> list[dict[str, Any]]:
    """Subset of installed tree-sitter packages with newer versions on PyPI."""
    raw = _pip("list", "--outdated", "--format=json")
    packages = json.loads(raw)
    return [p for p in packages if p["name"].startswith(_PREFIXES)]


def upgrade(names: list[str]) -> int:
    """Upgrade the named packages in-place. Returns pip's exit code."""
    if not names:
        return 0
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *names]
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="update_ts",
        description="Discover and update installed tree-sitter packages.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually upgrade. Default is dry run.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show / upgrade every discovered tree-sitter package, "
             "not just the ones with updates available.",
    )
    args = parser.parse_args(argv)

    discovered = installed_tree_sitter_packages()
    if not discovered:
        print("No tree-sitter packages installed in this environment.")
        return 0

    print(f"Discovered {len(discovered)} tree-sitter package(s):")
    for p in sorted(discovered, key=lambda x: x["name"]):
        print(f"  {p['name']:<35s} {p['version']}")
    print()

    outdated = outdated_tree_sitter_packages()
    if not outdated:
        print("All tree-sitter packages are up to date.")
        return 0

    print(f"{len(outdated)} package(s) have updates available:")
    for p in sorted(outdated, key=lambda x: x["name"]):
        print(f"  {p['name']:<35s} {p['version']:>10s} → {p['latest_version']}")
    print()

    targets = (
        [p["name"] for p in discovered]
        if args.all else [p["name"] for p in outdated]
    )

    if not args.apply:
        print(f"(Dry run — would upgrade {len(targets)} package(s).)")
        print("Re-run with --apply to actually upgrade.")
        return 0

    print(f"Upgrading {len(targets)} package(s)...")
    print()
    return upgrade(targets)


if __name__ == "__main__":
    sys.exit(main())
