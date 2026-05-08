"""Refactor-time output diff for ``absentia check``.

Two ways to use it. Both compare two scan outputs and report drift
in the rules + gaps, exit 0 on identical, 1 on any divergence.

The intended workflow is the "before/after a refactor" loop:

    # Before the change:
    absentia check . --json > /tmp/before.json
    # ... edit code ...
    absentia check . --json > /tmp/after.json
    python scripts/diff_scan.py /tmp/before.json /tmp/after.json

If you'd rather skip writing files, the script can run the second
scan itself against a path:

    python scripts/diff_scan.py /tmp/before.json --against .

Designed for *invasive but supposedly behavior-preserving* changes —
the symmetry name-index refactor, the Query API extractor migration,
mypyc compilation. Detects exactly the class of "I thought this
preserved output" mistakes those changes are prone to.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    with path.open("rb") as f:
        return json.loads(f.read())


def _run_absentia_json(target: Path) -> dict:
    """Invoke ``absentia check --json`` against ``target`` and return the
    parsed JSON. Best-effort: if the absentia CLI returns non-zero (which
    it will if any gap is found, since --max-gaps defaults to 0), we
    still parse the JSON it printed to stdout."""
    proc = subprocess.run(
        ["absentia", "check", str(target), "--json"],
        capture_output=True,
        text=False,
    )
    if not proc.stdout:
        sys.stderr.write(
            f"diff_scan: absentia check produced no JSON output. stderr:\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}\n"
        )
        sys.exit(2)
    return json.loads(proc.stdout)


def _gap_index(scan: dict) -> dict[str, dict]:
    """Index the gap list by stable short_id (or full id), so we can
    compare set-membership and detect changes per gap."""
    out: dict[str, dict] = {}
    for gap in scan.get("gaps", []):
        key = gap.get("short_id") or gap.get("id") or json.dumps(gap, sort_keys=True)
        out[key] = gap
    return out


def _rule_index(scan: dict) -> dict[str, dict]:
    """Pull the unique rules referenced by the gap list. Absentia's JSON
    output nests the rule under each gap rather than emitting a
    top-level rules array, so we deduplicate by rule.id here."""
    out: dict[str, dict] = {}
    for gap in scan.get("gaps", []):
        rule = gap.get("rule") or {}
        key = rule.get("id") or json.dumps(rule, sort_keys=True)
        out[key] = rule
    return out


def _format_section(title: str, ids: list[str], items: dict[str, dict]) -> str:
    if not ids:
        return ""
    lines = [f"{title} ({len(ids)}):"]
    for k in sorted(ids)[:50]:
        item = items.get(k, {})
        # Items can be either a gap dict (with nested entity/rule) or
        # a rule dict (flat). Try both shapes.
        ent = item.get("entity") or {}
        rule = item.get("rule") or item   # gap.rule or item-is-already-rule
        loc = ent.get("file_path", "")
        line = ent.get("line", "")
        kind = ent.get("kind", "")
        what = rule.get("feature_value", "")
        lines.append(f"  {k:14s} {loc}:{line} {kind} {what}".rstrip())
    if len(ids) > 50:
        lines.append(f"  ... and {len(ids) - 50} more")
    return "\n".join(lines)


def diff_scans(before: dict, after: dict) -> int:
    """Compare two scan dicts. Print a human-readable drift report.
    Return exit code: 0 if identical, 1 if drift."""
    before_gaps = _gap_index(before)
    after_gaps = _gap_index(after)
    before_rules = _rule_index(before)
    after_rules = _rule_index(after)

    added_gaps = sorted(set(after_gaps) - set(before_gaps))
    removed_gaps = sorted(set(before_gaps) - set(after_gaps))
    added_rules = sorted(set(after_rules) - set(before_rules))
    removed_rules = sorted(set(before_rules) - set(after_rules))

    n_before_gaps = len(before_gaps)
    n_after_gaps = len(after_gaps)
    n_before_rules = len(before_rules)
    n_after_rules = len(after_rules)

    print(
        f"gaps:  {n_before_gaps:>5d} -> {n_after_gaps:>5d}  "
        f"(added {len(added_gaps)}, removed {len(removed_gaps)})"
    )
    print(
        f"rules: {n_before_rules:>5d} -> {n_after_rules:>5d}  "
        f"(added {len(added_rules)}, removed {len(removed_rules)})"
    )

    if not (added_gaps or removed_gaps or added_rules or removed_rules):
        print("\n✓ identical — no rule or gap drift")
        return 0

    print()
    for section in (
        _format_section("Added gaps", added_gaps, after_gaps),
        _format_section("Removed gaps", removed_gaps, before_gaps),
        _format_section("Added rules", added_rules, after_rules),
        _format_section("Removed rules", removed_rules, before_rules),
    ):
        if section:
            print(section)
            print()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff two `absentia check --json` outputs."
    )
    parser.add_argument(
        "before",
        type=Path,
        help="Baseline scan JSON (from `absentia check ... --json`).",
    )
    parser.add_argument(
        "after",
        nargs="?",
        type=Path,
        help="Comparison scan JSON. Omit if using --against.",
    )
    parser.add_argument(
        "--against",
        type=Path,
        help=(
            "Run `absentia check --json` against this path and compare "
            "to <before>. Skip writing the second JSON file yourself."
        ),
    )
    args = parser.parse_args(argv)

    if args.after is None and args.against is None:
        parser.error("Either pass <after> or use --against PATH.")
    if args.after is not None and args.against is not None:
        parser.error("Pass <after> OR --against, not both.")

    before = _load(args.before)
    after = _load(args.after) if args.after else _run_absentia_json(args.against)

    return diff_scans(before, after)


if __name__ == "__main__":
    sys.exit(main())
