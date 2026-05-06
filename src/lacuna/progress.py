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
import sys
import threading
import time
from typing import Any, Iterator


_BAR_WIDTH = 30
_THROTTLE_SECONDS = 0.1
_LINE_WIDTH = 120
_ITEM_INDENT = "  "

# ANSI escape sequences for in-place 2-line redraws.
# After each draw we leave the cursor at the start of the bar
# (top) line, so the next \r-prefixed write overwrites cleanly.
_CLEAR_TO_END = "\033[K"      # clear from cursor to end of line
_CURSOR_PREV_LINE = "\033[F"   # move to start of previous line


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

    On the first draw, just write both lines (bar + sub) and leave
    the cursor at the start of the bar line. On subsequent draws,
    overwrite both lines in place. Result: when the work finishes
    and ``finish()`` is called, normal terminal flow resumes from
    the line below the sub-line — no orphaned partial draws.
    """
    top_padded = top.ljust(_LINE_WIDTH)[:_LINE_WIDTH]
    bottom_padded = bottom.ljust(_LINE_WIDTH)[:_LINE_WIDTH]
    if first:
        # Write bar + newline + sub. Leave cursor at end of sub.
        # Then move back to start of bar line for next overwrite.
        sys.stderr.write(
            f"\r{top_padded}\n{bottom_padded}{_CURSOR_PREV_LINE}"
        )
    else:
        sys.stderr.write(
            f"\r{top_padded}\n{bottom_padded}{_CURSOR_PREV_LINE}"
        )
    sys.stderr.flush()


def _emit_finish_blank() -> None:
    """Clear the 2-line draw region and move cursor past it.

    Called from ``finish()`` so normal terminal output continues
    on a fresh line below where the indicator was rendering.
    """
    # Cursor is at start of bar line. Clear bar, newline,
    # clear sub, newline (lands one line below where we drew).
    sys.stderr.write(f"\r{_CLEAR_TO_END}\n{_CLEAR_TO_END}\n")
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
        self._started = time.perf_counter()
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False
        self._first_draw_done = False

    def set_current_item(self, item: str) -> None:
        """Sub-line shown beneath the bar — typically the file lacuna
        is currently looking at. Lets the user see real-time progress
        without flooding stdout."""
        self._current_item = item

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

    def _draw(self, now: float) -> None:
        elapsed = now - self._started
        pct = (self.current / self.total) * 100
        filled = int(_BAR_WIDTH * self.current / self.total)
        bar = "█" * filled + "░" * (_BAR_WIDTH - filled)
        if self.current and self.current < self.total:
            eta = elapsed / self.current * (self.total - self.current)
            tail = (
                f" · {_format_time(elapsed)} elapsed, "
                f"~{_format_time(eta)} remaining"
            )
        elif self.current >= self.total:
            tail = f" · {_format_time(elapsed)}"
        else:
            tail = ""
        prefix = f"{self.label} " if self.label else ""
        top = (
            f"{prefix}[{bar}] {self.current:,d}/{self.total:,d} "
            f"({pct:>3.0f}%){tail}"
        )
        bottom = (
            f"{_ITEM_INDENT}{_truncate_for_display(self._current_item)}"
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
        # Force a final draw at 100%, clear the sub-line, then move
        # past the 2-line region so normal output flows below.
        if self.current < self.total:
            self.current = self.total
        self._current_item = ""  # clean up sub-line on final paint
        self._draw(time.perf_counter())
        # Move past the 2-line region (we're at start of bar after _draw)
        sys.stderr.write("\n\n")
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
        prefix = f"{self.prefix} " if self.prefix else ""
        top = (
            f"{prefix}[{self.current_step}/{self.total}] "
            f"{self.current_label}… {_format_time(elapsed)}"
        )
        bottom = (
            f"{_ITEM_INDENT}{_truncate_for_display(self._current_item)}"
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
        self._started = time.perf_counter()
        self._frame = 0
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False
        self._first_draw_done = False

    def set_current_item(self, item: str) -> None:
        """Sub-line shown beneath the spinner."""
        self._current_item = item

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
        prefix = f"{self.label} " if self.label else ""
        top = f"{sym} {prefix}({_format_time(elapsed)})"
        bottom = (
            f"{_ITEM_INDENT}{_truncate_for_display(self._current_item)}"
            if self._current_item else ""
        )
        _emit_two_line(top, bottom, first=not self._first_draw_done)
        self._first_draw_done = True

    def finish(self, end_message: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        if end_message:
            # Replace the bar with the end-message, clear the sub-line,
            # and exit the draw region.
            top = f"✓ {end_message}"
            sys.stderr.write(
                f"\r{top.ljust(_LINE_WIDTH)[:_LINE_WIDTH]}"
                f"\n{_CLEAR_TO_END}\n"
            )
        elif self._first_draw_done:
            # Clear the 2-line region we drew.
            _emit_finish_blank()
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
