"""Format gaps for output — human-readable text or JSON.

The TUI (when it lands) will consume the same underlying data via the
library API; ``format_gaps_json`` is also what editor plugins and the
Dev-Dashboard panel will parse.
"""
from __future__ import annotations

import json
from typing import Any

from .entities import Entity
from .mining import Gap, Rule


def format_gaps(
    gaps: list[Gap],
    rules: dict[str, Rule],
    entities: dict[str, Entity],
    *,
    min_confidence: float = 0.8,
) -> str:
    if not gaps:
        return "No gaps. (lacuna found nothing wrong.)\n"

    lines: list[str] = []
    lines.append(f"GAPS{'':<46}confidence ≥ {min_confidence:.2f}   {len(gaps)}")
    lines.append("")

    for gap in gaps:
        rule = rules[gap.rule_id]
        entity = entities[gap.entity_id]
        loc = f"{entity.file_path}:{entity.line}"
        short = entity.qualified_name.split("::", 1)[-1]
        kind_label = f"{entity.kind} `{short}`"
        missing = f"missing {rule.feature_value}"
        lines.append(
            f"  {loc:<40s} {kind_label:<32s} {missing:<32s} "
            f"{rule.confidence:.2f}  {gap.short_id}"
        )

    lines.append("")
    lines.append("─" * 60)
    lines.append(f"  {len(gaps)} gaps  ·  {len(rules)} rules")
    lines.append("")

    return "\n".join(lines)


def format_gaps_json(
    gaps: list[Gap],
    rules: dict[str, Rule],
    entities: dict[str, Entity],
    *,
    scan_stats: dict[str, Any],
) -> str:
    """Stable JSON shape for editor plugins, CI consumers, and the
    Dev-Dashboard panel. Each gap is self-contained: the rule and entity
    are inlined so consumers don't need to join across collections.
    """
    payload = {
        "scan": scan_stats,
        "summary": {
            "gaps": len(gaps),
            "rules": len(rules),
        },
        "gaps": [
            {
                "id": gap.id,
                "short_id": gap.short_id,
                "rule": {
                    "id": rule.id,
                    "group_id": rule.group_id,
                    "feature_kind": rule.feature_kind,
                    "feature_value": rule.feature_value,
                    "support_n": rule.support_n,
                    "support_total": rule.support_total,
                    "confidence": round(rule.confidence, 4),
                },
                "entity": {
                    "id": entity.id,
                    "kind": entity.kind,
                    "qualified_name": entity.qualified_name,
                    "file_path": entity.file_path,
                    "line": entity.line,
                },
            }
            for gap in gaps
            for rule in [rules[gap.rule_id]]
            for entity in [entities[gap.entity_id]]
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
