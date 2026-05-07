"""ANSI color constants for in-place stderr writes (progress UI).

Rich's ``Console.print()`` is line-oriented — it adds newlines and
buffers writes, which interferes with our ``\\r``-based progress
overwrites and ``\\033[F`` cursor-up sequences. For the progress
UI we keep raw stderr writes and just sprinkle these constants
into the format strings.

Behavior:
  - When stderr is a TTY *and* ``NO_COLOR`` is unset *and* ``TERM``
    isn't ``dumb``, all constants resolve to ANSI escape codes.
  - Otherwise they resolve to empty strings, so f-strings using them
    produce identical output to the no-color path. CI logs and piped
    output stay clean automatically.

Full-line color (gap rendering, calibration prompts, etc.) lives in
:mod:`_console` via ``rich`` instead — that has better support for
terminal-width-aware wrapping, mixed styles, and theme detection.
"""
from __future__ import annotations

import os
import sys


def _color_supported() -> bool:
    """Detect whether stderr can render ANSI colors."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


_USE_COLOR = _color_supported()


# Foreground colors — we use these in the progress UI, never in
# output.py (rich handles full-line styling there).
RESET          = "\033[0m"  if _USE_COLOR else ""
BOLD           = "\033[1m"  if _USE_COLOR else ""
DIM            = "\033[2m"  if _USE_COLOR else ""
RED            = "\033[31m" if _USE_COLOR else ""
GREEN          = "\033[32m" if _USE_COLOR else ""
YELLOW         = "\033[33m" if _USE_COLOR else ""
BLUE           = "\033[34m" if _USE_COLOR else ""
MAGENTA        = "\033[35m" if _USE_COLOR else ""
CYAN           = "\033[36m" if _USE_COLOR else ""
BRIGHT_RED     = "\033[91m" if _USE_COLOR else ""
BRIGHT_GREEN   = "\033[92m" if _USE_COLOR else ""
BRIGHT_YELLOW  = "\033[93m" if _USE_COLOR else ""
BRIGHT_BLUE    = "\033[94m" if _USE_COLOR else ""
BRIGHT_MAGENTA = "\033[95m" if _USE_COLOR else ""
BRIGHT_CYAN    = "\033[96m" if _USE_COLOR else ""
BRIGHT_WHITE   = "\033[97m" if _USE_COLOR else ""


# Per-language palette for progress-UI section tags. Color choice
# is mostly mnemonic ("Python blue", "Rust orange-via-bright_red",
# "Go cyan", "Ruby red", etc.); collisions are acceptable since the
# point is just visual distinction at a glance, not a 1:1 mapping.
# Falls back to CYAN for any language not in the table.
_LANG_COLORS: dict[str, str] = {
    "python":     BRIGHT_BLUE,
    "javascript": BRIGHT_YELLOW,
    "typescript": BLUE,
    "tsx":        BLUE,
    "rust":       BRIGHT_RED,
    "go":         BRIGHT_CYAN,
    "java":       RED,
    "ruby":       RED,
    "csharp":     BRIGHT_MAGENTA,
    "c":          BRIGHT_WHITE,
    "cpp":        BRIGHT_WHITE,
    "php":        MAGENTA,
    "kotlin":     MAGENTA,
    "scala":      BRIGHT_MAGENTA,
    "lua":        BLUE,
    "bash":       GREEN,
    "swift":      BRIGHT_YELLOW,
}


def lang_color(language: str) -> str:
    """Return the ANSI foreground escape for a language tag, or CYAN
    as the neutral fallback. Empty string when colors are off."""
    return _LANG_COLORS.get(language, CYAN)


__all__ = [
    "RESET", "BOLD", "DIM",
    "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN",
    "BRIGHT_RED", "BRIGHT_GREEN", "BRIGHT_YELLOW",
    "BRIGHT_BLUE", "BRIGHT_MAGENTA", "BRIGHT_CYAN", "BRIGHT_WHITE",
    "lang_color",
]
