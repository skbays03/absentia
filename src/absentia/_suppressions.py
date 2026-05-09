"""Shared suppression loading + matching for the engine and TUI.

Suppressions live in two places:

  1. ``<project>/.absentia/state.db`` — local, gitignored, ad-hoc.
     Added/removed via ``absentia suppress`` (CLI) or the TUI's
     ``s`` / ``r`` keys. Matched by gap.short_id (or full id).
     Owned by ``storage.Storage``.

  2. ``<project>/absentia.toml`` ``[[suppress]]`` blocks — project-
     wide, version-controlled, reviewable. Owned by this module.
     Matched by entity name + (optional) rule reference, with
     scope semantics defined below.

The TWO sources are AND'd into a single suppression filter that
``scan_corpus`` applies before the gap list is finalized.

``[[suppress]]`` block schema::

    [[suppress]]
    entity  = "src/api/users.py::delete_user"   # required
    rule    = "@audit"                          # optional — see below
    scope   = "gap"  # "gap" | "rule_global"    # optional, default "gap"
    reason  = "delete_user IS the audit endpoint"
    created = "2026-05-04"

Field semantics:

  * ``entity``: matches a gap's ``entity_id`` (the full qualified
    name like ``"src/api/users.py::delete_user"``). Exact match.
  * ``rule``: matches the rule's ``feature_value`` (e.g.
    ``"@audit"`` — what the user sees in "missing @audit") OR the
    rule's full id (``"<group>::<kind>=<value>"``). The
    feature-value form is the human-friendly path; full-id is
    available for surgical disambiguation.
  * ``scope``:
      - ``"gap"`` (default) — suppress a single (entity, rule) pair.
        Requires ``entity``. ``rule`` optional; without it,
        suppresses every gap on this entity (any rule).
      - ``"rule_global"`` — suppress every gap from this rule,
        anywhere. Requires ``rule``; ``entity`` ignored.

Other scopes ``("rule_for_entity"``, ``"selector"`)`` from the
example schema in the docs aren't implemented yet — they'd parse
but match no gaps. Future work.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_project_suppressions(root: Path) -> list[dict[str, Any]]:
    """Read ``[[suppress]]`` blocks from ``absentia.toml``.

    Returns a normalized list of dicts with the schema-shape fields
    (entity, rule, scope, reason, created). Failures (missing file,
    parse error, malformed entries) return an empty list — project
    suppressions are advisory enforcement, not load-bearing for the
    scan, so a bad TOML shouldn't break ``absentia check``.
    """
    toml_path = root / "absentia.toml"
    if not toml_path.exists():
        return []
    import tomllib  # py 3.11+, project requires 3.13+

    try:
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return []
    raw = data.get("suppress")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        out.append({
            "entity":  entry.get("entity"),
            "rule":    entry.get("rule"),
            "scope":   entry.get("scope", "gap"),
            "reason":  entry.get("reason", ""),
            "created": entry.get("created", ""),
        })
    return out


def gap_matches_project_entry(
    *,
    entity_id: str,
    rule_id: str,
    rule_feature_value: str,
    entry: dict[str, Any],
) -> bool:
    """Decide whether a gap is suppressed by a project-wide entry.

    See module docstring for scope semantics.

    ``entity_id`` is the gap's entity qualified_name (full path like
    ``"src/api/users.py::delete_user"``). ``rule_id`` is the rule's
    full id (``"<group>::<kind>=<value>"``). ``rule_feature_value``
    is what the user sees in gap output (e.g. ``"@audit"``).
    """
    scope = (entry.get("scope") or "gap").lower()
    rule_field = entry.get("rule")
    entity_field = entry.get("entity")

    if scope == "rule_global":
        if not rule_field:
            return False
        return _rule_matches(rule_field, rule_id, rule_feature_value)

    # Default: gap scope.
    if scope != "gap":
        # Unknown scope — be conservative, don't suppress.
        return False
    if not entity_field:
        return False
    if entity_field != entity_id:
        return False
    if rule_field and not _rule_matches(
        rule_field, rule_id, rule_feature_value,
    ):
        return False
    return True


def _rule_matches(
    rule_field: str, rule_id: str, rule_feature_value: str,
) -> bool:
    """A TOML ``rule = "..."`` matches a real rule iff the field
    equals either the rule's full id or its feature_value (the
    human-friendly form like ``"@audit"`` that shows in gap output).
    """
    return rule_field == rule_id or rule_field == rule_feature_value
