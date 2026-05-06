"""Tests for src/lacuna/progress.py."""
from __future__ import annotations

import io
import sys
from unittest.mock import patch

from lacuna.progress import (
    ProgressBar,
    Spinner,
    StepIndicator,
    spinning,
    ticking,
    _format_time,
)


def test_format_time_under_minute():
    assert _format_time(0.4) == "0s"
    assert _format_time(45.7) == "46s"


def test_format_time_minutes():
    assert _format_time(60) == "1m00s"
    assert _format_time(125) == "2m05s"


def test_format_time_hours():
    assert _format_time(3700) == "1h01m"


def test_progressbar_handles_non_tty(capsys):
    """Non-TTY: bar accumulates internally but writes nothing."""
    with patch.object(sys.stderr, "isatty", return_value=False):
        bar = ProgressBar(total=10, label="x")
        for _ in range(5):
            bar.update(1)
        bar.finish()
    captured = capsys.readouterr()
    assert captured.err == ""
    # Internal counter still tracks; just no terminal output.
    assert bar.current == 5


def test_progressbar_zero_total_doesnt_explode():
    """A scan with zero files shouldn't divide-by-zero."""
    bar = ProgressBar(total=0)
    bar.update(0)
    bar.finish()
    # No assertion needed — just shouldn't raise.


def test_progressbar_writes_to_stderr_when_tty():
    """TTY: at least one redraw happens before finish."""
    fake_stderr = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake_stderr.write(data)

        def flush(self):
            pass

        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        bar = ProgressBar(total=5, label="test")
        bar.update(5)  # complete in one shot
        bar.finish()
    out = fake_stderr.getvalue()
    assert "test" in out
    assert "5/5" in out
    assert "100%" in out


def test_step_indicator_non_tty(capsys):
    """Non-TTY: step transitions don't write anything."""
    with patch.object(sys.stderr, "isatty", return_value=False):
        ind = StepIndicator(total_steps=3)
        ind.step("first")
        ind.step("second")
        ind.finish()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_step_indicator_writes_on_tty():
    fake = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake.write(data)

        def flush(self):
            pass

        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        ind = StepIndicator(total_steps=3, prefix="[cal]")
        ind.step("alpha task")
        ind.step("beta task")
        ind.finish()
    out = fake.getvalue()
    assert "[cal]" in out
    assert "alpha task" in out
    assert "beta task" in out
    # Step indices appear
    assert "1/3" in out
    assert "2/3" in out


def test_ticking_context_manager_is_safe_no_tty():
    """ticking() must not crash when indicator does nothing."""
    with patch.object(sys.stderr, "isatty", return_value=False):
        ind = StepIndicator(total_steps=2)
        ind.step("one")
        with ticking(ind):
            pass  # no work
        ind.finish()


def test_spinner_non_tty(capsys):
    """Non-TTY: spinner does nothing visible."""
    with patch.object(sys.stderr, "isatty", return_value=False):
        sp = Spinner(label="x")
        for _ in range(20):
            sp.tick()
        sp.finish("done")
    captured = capsys.readouterr()
    assert captured.err == ""


def test_spinner_writes_on_tty():
    fake = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake.write(data)

        def flush(self):
            pass

        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        sp = Spinner(label="walking")
        # Force a draw by bypassing throttle
        sp._last_drawn = 0.0
        sp.tick()
        sp.finish(end_message="walked")
    out = fake.getvalue()
    assert "walking" in out
    assert "walked" in out


def test_spinning_context_manager_is_safe_no_tty():
    """spinning() runs cleanly even when spinner is no-op (non-TTY)."""
    with patch.object(sys.stderr, "isatty", return_value=False):
        sp = Spinner(label="x")
        with spinning(sp):
            pass
        sp.finish()


# ── set_current_item / 2-line rendering ──────────────────────────────


def test_progressbar_renders_current_item():
    """When set_current_item is set, the rendered output includes
    both the bar and the sub-line item."""
    fake = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake.write(data)

        def flush(self):
            pass

        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        bar = ProgressBar(total=5, label="Scanning")
        bar.update(1, item="src/api/users.py")
        bar.update(4)
        bar.finish()
    out = fake.getvalue()
    # Sub-line content present
    assert "src/api/users.py" in out
    # Bar still rendered
    assert "5/5" in out
    # ANSI cursor-up sequence used to overwrite in place
    assert "\033[F" in out


def test_spinner_renders_current_item():
    fake = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake.write(data)

        def flush(self):
            pass

        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        sp = Spinner(label="walk")
        sp.set_current_item("path/to/file.py")
        sp._last_drawn = 0.0
        sp.tick()
        sp.finish()
    out = fake.getvalue()
    assert "path/to/file.py" in out
    assert "walk" in out


def test_long_path_is_middle_truncated():
    """A path longer than the display budget gets ... in the middle
    so both ends stay visible."""
    from lacuna.progress import _truncate_for_display

    long_path = "a/" * 60 + "file.py"
    out = _truncate_for_display(long_path, max_width=40)
    assert len(out) == 40
    assert "..." in out
    assert out.startswith("a/a/")  # head preserved
    assert out.endswith("file.py")  # tail preserved


def test_short_path_passes_through():
    from lacuna.progress import _truncate_for_display

    assert _truncate_for_display("short.py", max_width=100) == "short.py"
