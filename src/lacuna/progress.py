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
        self._started = time.perf_counter()
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False

    def update(self, n: int = 1) -> None:
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
        line = (
            f"\r{prefix}[{bar}] {self.current:,d}/{self.total:,d} "
            f"({pct:>3.0f}%){tail}"
        )
        # Pad with spaces to overwrite any longer previous line; trim
        # to a reasonable max so we don't visually wrap on small terms.
        sys.stderr.write(line.ljust(120)[:120])
        sys.stderr.flush()

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        # Force a final draw at 100% then newline.
        if self.current < self.total:
            self.current = self.total
        self._draw(time.perf_counter())
        sys.stderr.write("\n")
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
        self._started = time.perf_counter()
        self._step_started = self._started
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False

    def step(self, label: str) -> None:
        """Begin a new sub-task with this label."""
        self.current_step += 1
        self.current_label = label
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
        line = (
            f"\r{prefix}[{self.current_step}/{self.total}] "
            f"{self.current_label}… {_format_time(elapsed)}"
        )
        sys.stderr.write(line.ljust(120)[:120])
        sys.stderr.flush()

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        sys.stderr.write("\n")
        sys.stderr.flush()

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
        self._started = time.perf_counter()
        self._frame = 0
        self._last_drawn = 0.0
        self._tty = _is_tty()
        self._finished = False

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
        line = f"\r{sym} {prefix}({_format_time(elapsed)})"
        sys.stderr.write(line.ljust(80)[:80])
        sys.stderr.flush()

    def finish(self, end_message: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._tty:
            return
        if end_message:
            sys.stderr.write(f"\r✓ {end_message}".ljust(80)[:80] + "\n")
        else:
            # Just clear the line and move on.
            sys.stderr.write("\r" + " " * 80 + "\r")
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
