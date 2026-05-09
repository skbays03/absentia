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
    """Render the gap list as a styled, terminal-width-adaptive table.

    Uses ``rich.table.Table`` with per-column ``max_width`` + ``overflow=
    "fold"`` so long file paths / qualified names / missing-value
    strings wrap inside their cells instead of pushing other columns
    out of alignment. The table expands to fill the available terminal
    width; on narrow terminals the fold-columns wrap to multiple lines
    rather than truncating.

    Color decisions go through a :class:`rich.console.Console` writing
    into an in-memory buffer — TTY → ANSI codes; pipe → plain text —
    so the returned string carries (or doesn't carry) escape codes
    appropriately for downstream ``print()``.
    """
    if not gaps:
        return "No gaps. (absentia found nothing wrong.)\n"

    from rich import box
    from rich.table import Table

    n_rules = len({g.rule_id for g in gaps})

    table = Table(
        title=f"GAPS · confidence ≥ {min_confidence:.2f}",
        title_style="bold",
        title_justify="left",
        caption=f"{len(gaps)} gaps · {n_rules} rules",
        caption_style="dim",
        caption_justify="left",
        show_header=True,
        header_style="bold",
        box=box.SIMPLE_HEAD,
        expand=True,
        pad_edge=False,
        # show_lines=False keeps row dividers off so the table reads
        # as a clean list rather than a heavy grid.
    )
    # Severity dot — a single glyph whose color encodes confidence at
    # a glance. Matches _confidence_style's bright_green / green /
    # yellow tiering. Width=1 + no_wrap keeps the indicator column
    # tight even on narrow terminals.
    table.add_column("●", justify="center", width=1, no_wrap=True)
    # The three "fold" columns share whatever terminal width is left
    # after the fixed ones (●, Conf, ID) are reserved. max_width caps
    # how much any single one can hog so a 200-char path doesn't
    # squeeze the others to nothing on a wide terminal.
    table.add_column(
        "Location", overflow="fold", max_width=42, style="cyan",
    )
    table.add_column("Entity", overflow="fold", max_width=32)
    table.add_column(
        "Missing", overflow="fold", max_width=32, style="red",
    )
    table.add_column("Conf", justify="right", width=4, no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True, width=9)

    for gap in gaps:
        rule = rules[gap.rule_id]
        entity = entities[gap.entity_id]
        loc = f"{entity.file_path}:{entity.line}"
        short = entity.qualified_name.split("::", 1)[-1]
        conf_style = _confidence_style(rule.confidence)
        # Embed the per-row severity color in the dot + the conf value;
        # the "Location" / "Missing" columns get column-level styles
        # (cyan, red) since their tone is consistent across rows.
        dot = f"[{conf_style}]●[/]"
        entity_cell = f"{entity.kind} `[yellow]{short}[/]`"
        missing = f"missing {rule.feature_value}"
        conf_cell = f"[{conf_style}]{rule.confidence:.2f}[/]"
        table.add_row(
            dot,
            loc,
            entity_cell,
            missing,
            conf_cell,
            gap.short_id,
        )

    with _capturing_console() as (console, buf):
        console.print(table)
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
