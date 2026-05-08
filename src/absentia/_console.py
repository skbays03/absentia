"""Shared rich Console proxies for full-line styled output.

Two proxies, one per stream — created lazily so they always read
the current ``sys.stdout`` / ``sys.stderr`` rather than whatever
they were at import time. This matters because:

  - pytest's ``capsys`` reroutes those streams *after* import; a
    cached Console would write past the redirect.
  - Tools that wrap our CLI (Dev-Dashboard's panel host, embedding
    contexts) may also redirect.

``highlight=False`` is critical: rich's default highlighter
auto-styles things that look like numbers, paths, IDs, and UUIDs.
We want full control of every color decision.

``soft_wrap=True`` disables rich's automatic line-wrapping based on
terminal width, so a long file path or message doesn't get broken
across lines mid-render.

NO_COLOR detection, color-depth probing (truecolor / 256 / 8), and
TTY detection are all handled by rich's ``Console`` constructor —
re-evaluated on every proxy call so the right answer is used even
when the environment changes.
"""
from __future__ import annotations

import sys
from typing import Any

from rich.console import Console


class _ConsoleProxy:
    """Lazily-instantiated Console wrapper that always uses the current
    ``sys.stdout`` / ``sys.stderr`` rather than caching them.

    Implements just the subset of ``Console`` methods we use across
    the codebase (``print``, ``capture``). Adding more later is a
    one-line passthrough.
    """

    __slots__ = ("_use_stderr",)

    def __init__(self, *, stderr: bool = False) -> None:
        self._use_stderr = stderr

    def _make(self) -> Console:
        kwargs: dict[str, Any] = {"highlight": False, "soft_wrap": True}
        if self._use_stderr:
            kwargs["file"] = sys.stderr
            kwargs["stderr"] = True
        return Console(**kwargs)

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._make().print(*args, **kwargs)

    def capture(self) -> Any:
        return self._make().capture()


stdout_console = _ConsoleProxy()
stderr_console = _ConsoleProxy(stderr=True)


__all__ = ["stdout_console", "stderr_console"]
