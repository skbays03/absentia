"""Format gaps for output — human-readable text or JSON.

The TUI (when it lands) will consume the same underlying data via the
library API; ``format_gaps_json`` is also what editor plugins and the
Dev-Dashboard panel will parse.

Color rendering for the human text mode goes through rich's
``Console`` (see :mod:`_console`). Markup tokens like ``[cyan]…[/]``
are honored when stdout is a TTY and stripped automatically when it
isn't, so piped output stays plain. Markup is *only* applied here in
``format_gaps``; ``format_gaps_json`` stays pure data.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from typing import Any, Iterator

from rich.console import Console

from .entities import Entity
from .mining import Gap, Rule


@contextlib.contextmanager
def _capturing_console() -> Iterator[tuple[Console, io.StringIO]]:
    """Build a Console that writes into a StringIO, with color decisions
    matching whatever ``sys.stdout`` looks like *now*. Used by format
    functions that need to return a styled-or-plain string.

    ``force_terminal=is_tty`` lets us still force ANSI rendering into the
    StringIO buffer when the real stdout would have done so, so the
    returned string carries color codes that print correctly downstream.
    When stdout is piped (non-TTY), ``force_terminal=False`` strips
    markup automatically.

    Context-manager shape lets callers preserve a clean ``with`` block.
    The buffer remains valid after the block exits so ``buf.getvalue()``
    works post-yield.
    """
    is_tty = sys.stdout.isatty()
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=is_tty,
        highlight=False,
        soft_wrap=True,
    )
    yield console, buf


def _confidence_style(confidence: float) -> str:
    """Pick a rich color for a confidence value."""
    if confidence >= 0.95:
        return "bright_green"
    if confidence >= 0.80:
        return "green"
    return "yellow"


def format_gaps(
    gaps: list[Gap],
    rules: dict[str, Rule],
    entities: dict[str, Entity],
    *,
    min_confidence: float = 0.8,
) -> str:
    """Render the gap list as a styled string.

    Uses a temporary :class:`rich.console.Console` writing into an
    in-memory buffer so the styled output (or the auto-stripped plain
    output, when not a TTY) can be returned as a single string and
    handed to the caller — typically ``print()`` in cmd_check, or
    accumulated for further composition.
    """
    if not gaps:
        return "No gaps. (absentia found nothing wrong.)\n"

    # Build a local Console that writes into a buffer. Color decisions
    # mirror the real stdout's TTY status — TTY → ANSI codes; pipe →
    # plain text. Returning the buffer's value lets the caller print
    # via plain ``print()`` and still keep color or strip it correctly.
    with _capturing_console() as (console, buf):
        console.print(
            f"[bold]GAPS[/]{'':<46}"
            f"confidence ≥ {min_confidence:.2f}   {len(gaps)}"
        )
        console.print("")

        for gap in gaps:
            rule = rules[gap.rule_id]
            entity = entities[gap.entity_id]
            loc = f"{entity.file_path}:{entity.line}"
            short = entity.qualified_name.split("::", 1)[-1]
            kind_label = f"{entity.kind} `{short}`"
            missing = f"missing {rule.feature_value}"
            conf_style = _confidence_style(rule.confidence)
            # Pad the plain text values BEFORE applying markup so column
            # widths render correctly whether or not rich strips the
            # codes (piped output gets plain padded text; TTY gets the
            # same padded text plus color escapes around the parts we
            # marked).
            console.print(
                f"  [cyan]{loc:<40s}[/] "
                f"{entity.kind} `[yellow]{short}[/]`"
                f"{' ' * max(0, 32 - len(kind_label))} "
                f"[red]{missing:<32s}[/] "
                f"[{conf_style}]{rule.confidence:.2f}[/]  "
                f"[dim]{gap.short_id}[/]"
            )

        console.print("")
        console.print(f"[dim]{'─' * 60}[/]")
        console.print(
            f"  [bold]{len(gaps)}[/] gaps  ·  [bold]{len(rules)}[/] rules"
        )
        console.print("")

    return buf.getvalue()


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
