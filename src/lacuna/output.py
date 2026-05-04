"""Format gaps for human reading.

MVP: plain text only. JSON output for `--json` and the eventual TUI
renderer plug into the same data, just different presentation.
"""
from __future__ import annotations

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
            f"  {loc:<40s} {kind_label:<32s} {missing:<32s} {rule.confidence:.2f}"
        )

    lines.append("")
    lines.append("─" * 60)
    lines.append(f"  {len(gaps)} gaps  ·  {len(rules)} rules")
    lines.append("")

    return "\n".join(lines)
