"""TTY progress indicators.

Two flavors:

- :class:`ProgressBar` — count-based bar with a percent + ETA. Best for
  loops where you know the total (file scanning, mining).
- :class:`StepIndicator` — labeled step counter ("[3/8] doing X… 47.2 s"),
  for sequences of opaque sub-tasks (calibration: 8 sub-scans, each one
  a black box that might take 5-100 s).

Both write to stderr and use ``\\r`` to overwrite the current line.
Both auto-skip when stderr isn't a TTY (CI logs, piped output stay
clean). Both throttle updates to ~10 Hz so a tight loop doesn't
swamp the terminal with redraws.
"""
from __future__ import annotations

import contextlib
import re
import shutil
import sys
import threading
import time
from typing import Any, Iterator

from . import _color as C


# Strips ANSI CSI sequences (color, cursor) when measuring on-screen width.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _visible_len(s: str) -> int:
    """Length of a string ignoring ANSI escape codes — what the terminal
    actually renders."""
    return len(_ANSI_RE.sub("", s))


_BAR_WIDTH = 30
# 50 ms throttle (20 Hz) — snappy enough to feel real-time on fast
# work, slow enough not to flood a terminal with redraws when
# processing 100k+ files.
_THROTTLE_SECONDS = 0.05
_ITEM_INDENT = "  "

# ANSI escape for clear-from-cursor-to-end-of-line. Cursor recovery
# uses CSI n F (cursor preceding line, n times) inline in _emit_lines.
_CLEAR_TO_END = "\033[K"


def _term_cols() -> int:
    """Live terminal column count. Falls back to 80 if unknown
    (non-TTY, no env var). Cheap; safe to call per draw."""
    return shutil.get_terminal_size((80, 24)).columns


def _truncate_visible(s: str, width: int) -> str:
    """Truncate ``s`` to at most ``width`` visible columns, preserving
    ANSI escape sequences (which take zero visible space). If a cut
    happens, append ``\\x1b[0m`` so any in-flight color sequence
    doesn't bleed past the truncation point.

    Critical for tmux / narrow panes: padding to a fixed width that
    exceeds the pane causes lines to wrap, which breaks the
    ``\\033[F`` cursor-up that the in-place redraw relies on. By
    truncating to the live column count and using ``_CLEAR_TO_END``
    instead of padding, we never produce a line that wraps.
    """
    if _visible_len(s) <= width:
        return s
    out: list[str] = []
    visible = 0
    i = 0
    n = len(s)
    while i < n and visible < width:
        if s[i] == "\x1b" and i + 1 < n and s[i + 1] == "[":
            m = _ANSI_RE.match(s, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        out.append(s[i])
        visible += 1
        i += 1
    out.append("\x1b[0m")
    return "".join(out)


def _is_tty() -> bool:
    """True if stderr is a TTY. Cheap; called every update."""
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def _format_time(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{int(m)}m{int(sec):02d}s"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{int(h)}h{int(m):02d}m"


def _truncate_for_display(text: str, max_width: int = 100) -> str:
    """Middle-truncate a long path so both ends stay visible.

    ``some/very/long/path/to/file.py`` with width 30 becomes
    ``some/very/lo...path/to/file.py``.
    """
    if len(text) <= max_width:
        return text
    keep = max_width - 3
    head = keep // 2
    tail = keep - head
    return text[:head] + "..." + text[-tail:]


def _emit_two_line(top: str, bottom: str, *, first: bool) -> None:
    """Render a two-line update to stderr.

    Thin wrapper around :func:`_emit_lines` for the common
    bar-plus-one-sub-line case. Kept separate so callers that
    don't need multi-worker rendering stay simple.
    """
    _emit_lines(top, [bottom] if bottom else [], prev_total_lines=2)


def _emit_lines(
    top: str,
    subs: list[str],
    *,
    prev_total_lines: int,
) -> int:
    """Render ``top`` plus N sub-lines in place. Returns the line
    count just drawn (for the caller to pass back as
    ``prev_total_lines`` next time).

    Each line is truncated to the live terminal width — same
    wrap-prevention discipline as the two-line path. If a previous
    draw had more lines than this one (worker count shrank), the
    extra rows are cleared with ``\\033[K``; the orphan blank rows
    below the active region are visually neutral and get cleaned
    up by the eventual ``finish()`` regardless.

    Final cursor position: column 0 of ``top``'s row, so the next
    ``\\r``-prefixed write overwrites in place.
    """
    cols = _term_cols()
    top = _truncate_visible(top, cols)
    subs = [_truncate_visible(s, cols) for s in subs]

    parts: list[str] = [f"\r{top}{_CLEAR_TO_END}"]
    for s in subs:
        parts.append(f"\n{s}{_CLEAR_TO_END}")

    current_lines = 1 + len(subs)
    extras = max(0, prev_total_lines - current_lines)
    for _ in range(extras):
        parts.append(f"\n{_CLEAR_TO_END}")

    total_newlines = (current_lines - 1) + extras
    if total_newlines > 0:
        # CSI n F = move up n lines AND to column 0 (single escape).
        parts.append(f"\033[{total_newlines}F")

    sys.stderr.write("".join(parts))
    sys.stderr.flush()
    return current_lines


def _emit_finish_blank(n_lines: int = 2) -> None:
    """Clear an N-line draw region and move cursor past it.

    Called from ``finish()`` so normal terminal output continues on
    a fresh line below where the indicator was rendering. Defaults
    to 2 (the bar + single sub-line case) to keep existing callers
    unchanged.
    """
    parts = [f"\r{_CLEAR_TO_END}"]
    for _ in range(n_lines - 1):
        parts.append(f"\n{_CLEAR_TO_END}")
    parts.append("\n")  # advance past the cleared region
    sys.stderr.write("".join(parts))
    sys.stderr.flush()


class ProgressBar:
    """Count-based progress bar.

    Caller increments ``current`` via :meth:`update`. Bar redraws
    rate-limited; callers can update once per item without worrying
    about flooding the terminal.
    """

    def __init__(self, total: int, label: str = "") -> None:
        self.total = max(1, total)
        self.label = label
        self.current = 0
        self._current_item: str = ""
        # Multi-worker mode: list of (worker_id, section, item) tuples.
        # When non-empty, _draw renders one sub-line per worker
        # instead of the single _current_item line.
        self._workers: list[tuple[str, str, str]] = []
        self._started = time.perf_counter()
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False
        self._first_draw_done = False
        # Number of lines rendered on the most recent draw — used so
        # _emit_lines can clear orphan rows when worker count shrinks.
        self._prev_total_lines = 0

    def set_current_item(self, item: str) -> None:
        """Sub-line shown beneath the bar — typically the file lacuna
        is currently looking at. Lets the user see real-time progress
        without flooding stdout. Ignored when ``set_workers`` has
        installed a multi-worker view."""
        self._current_item = item

    def set_workers(self, active: list[tuple[str, str, str]]) -> None:
        """Install a multi-worker view: one sub-line per active worker.

        ``active`` is a list of ``(worker_id, section, item)`` tuples —
        e.g. ``("ForkPoolWorker-1", "python", "src/api/users.py")``.
        ``section`` is rendered in brackets (a language tag during
        parse, a strategy name during mining). Pass an empty list to
        revert to the single ``_current_item`` view.
        """
        self._workers = list(active)

    def update(self, n: int = 1, item: str | None = None) -> None:
        if item is not None:
            self._current_item = item
        if self._finished or not self._tty:
            self.current += n
            return
        self.current += n
        now = time.perf_counter()
        # Always redraw on completion; otherwise throttle.
        if (
            now - self._last_drawn < _THROTTLE_SECONDS
            and self.current < self.total
        ):
            return
        self._last_drawn = now
        self._draw(now)

    def refresh(self) -> None:
        """Force a redraw without advancing the count. Useful when the
        worker map has changed but the file count hasn't ticked yet."""
        if self._finished or not self._tty:
            return
        now = time.perf_counter()
        if now - self._last_drawn < _THROTTLE_SECONDS:
            return
        self._last_drawn = now
        self._draw(now)

    def _format_worker_line(self, worker: tuple[str, str, str]) -> str:
        worker_id, section, item = worker
        section_color = C.lang_color(section) if section else C.CYAN
        section_tag = f"[{section_color}{section}{C.RESET}]" if section else ""
        item_str = (
            f"{C.CYAN}{_truncate_for_display(item)}{C.RESET}" if item else ""
        )
        return f"{_ITEM_INDENT}{C.DIM}{worker_id}{C.RESET} {section_tag} {item_str}".rstrip()

    def _draw(self, now: float) -> None:
        elapsed = now - self._started
        pct = (self.current / self.total) * 100
        filled = int(_BAR_WIDTH * self.current / self.total)
        bar = (
            f"{C.GREEN}{'█' * filled}{C.RESET}"
            f"{C.DIM}{'░' * (_BAR_WIDTH - filled)}{C.RESET}"
        )
        if self.current and self.current < self.total:
            eta = elapsed / self.current * (self.total - self.current)
            tail = (
                f" · {_format_time(elapsed)} elapsed, "
                f"~{_format_time(eta)} remaining"
            )
        elif self.current >= self.total:
            tail = f" · {C.GREEN}{_format_time(elapsed)}{C.RESET}"
        else:
            tail = ""
        prefix = f"{C.BOLD}{self.label}{C.RESET} " if self.label else ""
        top = (
            f"{prefix}[{bar}] {self.current:,d}/{self.total:,d} "
            f"({pct:>3.0f}%){tail}"
        )
        if self._workers:
            subs = [self._format_worker_line(w) for w in self._workers]
        elif self._current_item:
            subs = [
                f"{_ITEM_INDENT}{C.CYAN}{_truncate_for_display(self._current_item)}{C.RESET}"
            ]
        else:
            subs = []
        self._prev_total_lines = _emit_lines(
            top, subs, prev_total_lines=self._prev_total_lines,
        )
        self._first_draw_done = True

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        # Force a final draw at 100%, clear the sub-line(s), then move
        # past the multi-line region so normal output flows below.
        if self.current < self.total:
            self.current = self.total
        self._current_item = ""
        self._workers = []
        self._draw(time.perf_counter())
        # Cursor is at top of region; advance past it so subsequent
        # text starts on a fresh line below.
        for _ in range(self._prev_total_lines):
            sys.stderr.write("\n")
        sys.stderr.write("\n")  # blank separator line
        sys.stderr.flush()

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.finish()


class StepIndicator:
    """Labeled step counter: ``[3/8] description… 47.2s``.

    For opaque sub-tasks where you can't track inner progress (a
    long subprocess, a single long-running scan). Caller calls
    :meth:`step` with the new label at the start of each sub-task;
    elapsed time updates while the step runs.
    """

    def __init__(self, total_steps: int, prefix: str = "") -> None:
        self.total = max(1, total_steps)
        self.prefix = prefix
        self.current_step = 0
        self.current_label = ""
        self._current_item: str = ""
        self._started = time.perf_counter()
        self._step_started = self._started
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False
        self._first_draw_done = False

    def set_current_item(self, item: str) -> None:
        """Sub-line shown beneath the step counter."""
        self._current_item = item

    def step(self, label: str) -> None:
        """Begin a new sub-task with this label."""
        self.current_step += 1
        self.current_label = label
        self._current_item = ""  # reset sub-line per step
        self._step_started = time.perf_counter()
        self._draw(self._step_started)

    def tick(self) -> None:
        """Refresh the elapsed-time display without changing steps.

        Useful from inside a long sub-task to show that time is
        passing. Throttled.
        """
        if self._finished or not self._tty:
            return
        now = time.perf_counter()
        if now - self._last_drawn < _THROTTLE_SECONDS:
            return
        self._draw(now)

    def _draw(self, now: float) -> None:
        if not self._tty:
            return
        self._last_drawn = now
        elapsed = now - self._step_started
        prefix = f"{C.DIM}{self.prefix}{C.RESET} " if self.prefix else ""
        step_marker = f"{C.CYAN}[{self.current_step}/{self.total}]{C.RESET}"
        top = (
            f"{prefix}{step_marker} "
            f"{self.current_label}… {_format_time(elapsed)}"
        )
        bottom = (
            f"{_ITEM_INDENT}{C.CYAN}{_truncate_for_display(self._current_item)}{C.RESET}"
            if self._current_item else ""
        )
        _emit_two_line(top, bottom, first=not self._first_draw_done)
        self._first_draw_done = True

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        if self._first_draw_done:
            _emit_finish_blank()

    def __enter__(self) -> "StepIndicator":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.finish()


class Spinner:
    """Indeterminate-progress spinner.

    Use when the work is opaque (no count/total available): rgloss
    over a home directory, a single big-tree walk, network-bound
    fetches. Ticks an animated frame + elapsed-time label so the user
    sees the tool is alive.
    """

    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, label: str = "") -> None:
        self.label = label
        self._current_item: str = ""
        # Multi-worker view: same shape as ProgressBar.set_workers.
        # Used by the mining stage to show one sub-line per running
        # strategy.
        self._workers: list[tuple[str, str, str]] = []
        self._started = time.perf_counter()
        self._frame = 0
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False
        self._first_draw_done = False
        self._prev_total_lines = 0

    def set_current_item(self, item: str) -> None:
        """Sub-line shown beneath the spinner. Ignored when
        ``set_workers`` has installed a multi-worker view."""
        self._current_item = item

    def set_workers(self, active: list[tuple[str, str, str]]) -> None:
        """Multi-worker mode: one sub-line per active worker. Same shape
        as :meth:`ProgressBar.set_workers`. Pass an empty list to revert
        to the single ``_current_item`` view."""
        self._workers = list(active)

    def _format_worker_line(self, worker: tuple[str, str, str]) -> str:
        worker_id, section, item = worker
        section_color = C.lang_color(section) if section else C.CYAN
        section_tag = f"[{section_color}{section}{C.RESET}]" if section else ""
        item_str = (
            f"{C.CYAN}{_truncate_for_display(item)}{C.RESET}" if item else ""
        )
        return f"{_ITEM_INDENT}{C.DIM}{worker_id}{C.RESET} {section_tag} {item_str}".rstrip()

    def tick(self) -> None:
        if self._finished or not self._tty:
            return
        now = time.perf_counter()
        if now - self._last_drawn < _THROTTLE_SECONDS:
            return
        self._last_drawn = now
        self._frame = (self._frame + 1) % len(self._FRAMES)
        elapsed = now - self._started
        sym = self._FRAMES[self._frame]
        prefix = f"{C.BOLD}{self.label}{C.RESET} " if self.label else ""
        top = f"{C.CYAN}{sym}{C.RESET} {prefix}({_format_time(elapsed)})"
        if self._workers:
            subs = [self._format_worker_line(w) for w in self._workers]
        elif self._current_item:
            subs = [
                f"{_ITEM_INDENT}{C.CYAN}{_truncate_for_display(self._current_item)}{C.RESET}"
            ]
        else:
            subs = []
        self._prev_total_lines = _emit_lines(
            top, subs, prev_total_lines=self._prev_total_lines,
        )
        self._first_draw_done = True

    def finish(self, end_message: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        if end_message:
            # Replace the spinner with the end-message and clear all
            # sub-line(s) the multi-worker view drew, then exit the
            # draw region.
            top = f"{C.BRIGHT_GREEN}✓{C.RESET} {end_message}"
            top = _truncate_visible(top, _term_cols())
            parts = [f"\r{top}{_CLEAR_TO_END}"]
            extras = max(1, self._prev_total_lines - 1)
            for _ in range(extras):
                parts.append(f"\n{_CLEAR_TO_END}")
            parts.append("\n")
            sys.stderr.write("".join(parts))
        elif self._first_draw_done:
            _emit_finish_blank(n_lines=max(2, self._prev_total_lines))
        sys.stderr.flush()

    def __enter__(self) -> "Spinner":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.finish()


@contextlib.contextmanager
def spinning(spinner: Spinner) -> Iterator[None]:
    """Run ``spinner.tick()`` ~10 Hz on a daemon thread for the duration
    of the block. Mirror of :func:`ticking` for ``StepIndicator``.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(_THROTTLE_SECONDS):
            spinner.tick()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=0.5)


@contextlib.contextmanager
def ticking(indicator: StepIndicator) -> Iterator[None]:
    """Context manager that runs ``indicator.tick()`` ~10 Hz on a daemon
    thread for the duration of the block.

    Use this around an opaque blocking sub-task (a long subprocess, a
    long synchronous scan) so the elapsed-time display updates while
    the work runs — without instrumenting the work itself.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(_THROTTLE_SECONDS):
            indicator.tick()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=0.5)
