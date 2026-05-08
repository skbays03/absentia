#!/usr/bin/env python3
"""scripts/update_ts.py — discover and update installed tree-sitter packages.

Absentia's polyglot story depends on staying current with tree-sitter grammar
releases (parser bug fixes, new node types). This script discovers every
installed package whose name starts with ``tree-sitter`` or ``tree_sitter``
and offers to upgrade them.

Three modes:

  - **Interactive** (default, when run from a TTY): prints a numbered list
    of discovered packages, then prompts for an action.
  - **Non-interactive apply** (``--apply`` and ``--apply --all``): runs the
    upgrade without prompting, suitable for CI / cron.
  - **Non-interactive info** (``--dry-run`` or non-TTY stdin): prints the
    status and exits.

Interactive menu:

  1) apply        Upgrade outdated packages only
  2) apply all    Upgrade every discovered package (force re-install)
  3) apply N      Upgrade specific package(s) by their listed number,
                  e.g. ``3 2,4`` to upgrade packages 2 and 4
  q) quit         Exit without changes
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
    return [p for p in json.loads(raw) if p["name"].startswith(_PREFIXES)]


def outdated_tree_sitter_packages() -> list[dict[str, Any]]:
    """Subset of installed tree-sitter packages with newer versions on PyPI."""
    raw = _pip("list", "--outdated", "--format=json")
    return [p for p in json.loads(raw) if p["name"].startswith(_PREFIXES)]


def upgrade(names: list[str]) -> int:
    """Upgrade the named packages in-place. Returns pip's exit code."""
    if not names:
        return 0
    print(f"Upgrading {len(names)} package(s)...\n")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *names]
    return subprocess.run(cmd).returncode


def print_status(
    discovered: list[dict[str, Any]],
    outdated: list[dict[str, Any]],
) -> None:
    """Print the numbered list of packages and an outdated summary."""
    if not discovered:
        print("No tree-sitter packages installed in this environment.")
        return

    sorted_pkgs = sorted(discovered, key=lambda x: x["name"])

    print("Tree-sitter packages")
    print("─" * 60)
    for i, p in enumerate(sorted_pkgs, 1):
        latest = next(
            (o["latest_version"] for o in outdated if o["name"] == p["name"]),
            None,
        )
        if latest:
            print(f"  {i}. {p['name']:<32s} {p['version']:>10s} → {latest}")
        else:
            print(f"  {i}. {p['name']:<32s} {p['version']:>10s}   (up to date)")
    print()

    if outdated:
        print(f"{len(outdated)} package(s) with updates available.\n")
    else:
        print("All packages up to date.\n")


def interactive(
    discovered: list[dict[str, Any]],
    outdated: list[dict[str, Any]],
) -> int:
    """Prompt for an action; return the resulting exit code."""
    sorted_pkgs = sorted(discovered, key=lambda x: x["name"])

    print("Actions")
    print("─" * 60)
    print("  1) apply        Upgrade outdated only "
          f"({len(outdated)} package(s))")
    print("  2) apply all    Upgrade every discovered package "
          f"({len(discovered)} package(s))")
    print("  3) apply N      Upgrade specific package(s) by number")
    print("                  Pass numbers inline ('3 2,4') or on the next prompt")
    print("  q) quit         Exit without changes")
    print()

    try:
        choice = input("Choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0

    if choice in ("", "q", "quit", "exit"):
        print("No changes.")
        return 0

    if choice == "1":
        if not outdated:
            print("Nothing to upgrade.")
            return 0
        return upgrade([p["name"] for p in outdated])

    if choice == "2":
        return upgrade([p["name"] for p in discovered])

    if choice.startswith("3"):
        rest = choice[1:].strip()
        if not rest:
            try:
                rest = input(
                    "Numbers (comma-separated), e.g. '2,4': "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
        if not rest:
            print("No selection given.")
            return 1
        try:
            indices = [int(s.strip()) for s in rest.split(",")]
        except ValueError:
            print(f"Invalid input: {rest!r}")
            return 1
        names: list[str] = []
        for idx in indices:
            if not (1 <= idx <= len(sorted_pkgs)):
                print(f"Invalid package number: {idx} "
                      f"(must be 1..{len(sorted_pkgs)})")
                return 1
            names.append(sorted_pkgs[idx - 1]["name"])
        if not names:
            print("Nothing selected.")
            return 0
        return upgrade(names)

    print(f"Unknown choice: {choice!r}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="update_ts",
        description="Discover and update installed tree-sitter packages.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Upgrade outdated packages without prompting (non-interactive).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="With --apply, upgrade every discovered package, "
             "not just outdated. Ignored without --apply.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print status and exit without prompting or upgrading.",
    )
    args = parser.parse_args(argv)

    discovered = installed_tree_sitter_packages()
    outdated = outdated_tree_sitter_packages()
    print_status(discovered, outdated)

    if not discovered:
        return 0

    # Non-interactive apply
    if args.apply:
        targets = (
            [p["name"] for p in discovered]
            if args.all else [p["name"] for p in outdated]
        )
        if not targets:
            print("Nothing to upgrade.")
            return 0
        return upgrade(targets)

    # Non-interactive info
    if args.dry_run or not sys.stdin.isatty():
        if not sys.stdin.isatty() and not args.dry_run:
            print("(non-TTY input; skipping interactive prompt)")
        return 0

    # Interactive
    return interactive(discovered, outdated)


if __name__ == "__main__":
    sys.exit(main())
