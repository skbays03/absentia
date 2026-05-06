"""Shared rich Console instances for full-line styled output.

Two consoles, one per stream, so that piped stdout (`lacuna check
> out.txt`) stays plain while colored progress + status messages
on stderr keep working.

``highlight=False`` is critical: rich's default highlighter
auto-styles things that look like numbers, paths, IDs, and UUIDs.
We want full control of every color decision (e.g., we color the
file path with our chosen ``cyan`` — rich's auto-highlight would
sneak in its own bright-cyan for the line number that conflicts).

``soft_wrap=True`` disables rich's automatic line-wrapping based
on terminal width, so a long file path or message doesn't get
broken across lines mid-render.

NO_COLOR detection, color-depth probing (truecolor / 256 / 8),
and TTY detection are all handled by rich's ``Console``
constructor automatically.
"""
from __future__ import annotations

import sys

from rich.console import Console


# Output console: gap reports, est table, init confirmation, etc.
# Goes to stdout. Auto-detects TTY; piped output is plain.
stdout_console = Console(highlight=False, soft_wrap=True)

# Status / progress / error console: scan preamble, calibration
# prompts, error messages, etc. Goes to stderr so it survives
# `lacuna check | grep ...`.
stderr_console = Console(
    file=sys.stderr,
    stderr=True,
    highlight=False,
    soft_wrap=True,
)


__all__ = ["stdout_console", "stderr_console"]
