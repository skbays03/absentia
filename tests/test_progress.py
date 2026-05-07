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
    _truncate_visible,
    _visible_len,
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


# ── _truncate_visible — wrap-prevention guarantee ─────────────────────


def test_truncate_visible_passes_short_strings_through():
    """Strings shorter than width come back unchanged — no extra cost."""
    s = "hello world"
    assert _truncate_visible(s, 80) is s


def test_truncate_visible_caps_at_target_width():
    """Long strings get cut so visible length ≤ width."""
    s = "x" * 200
    out = _truncate_visible(s, 50)
    # _visible_len strips ANSI; the trailing reset adds 0 visible cols.
    assert _visible_len(out) == 50


def test_truncate_visible_preserves_ansi_inside_window():
    """Color sequences inside the kept portion survive the cut."""
    s = "\x1b[32mhello\x1b[0m world" + ("z" * 200)
    out = _truncate_visible(s, 11)
    # Visible portion is "hello world" (11 chars); color codes preserved.
    assert "\x1b[32m" in out
    assert "\x1b[0m" in out
    assert _visible_len(out) == 11


def test_truncate_visible_appends_reset_on_cut():
    """A cut always closes any open color so it can't bleed into the
    next line — the bug that motivated this whole change."""
    s = "\x1b[31m" + ("x" * 200)
    out = _truncate_visible(s, 50)
    # Last bytes should be the reset sequence.
    assert out.endswith("\x1b[0m")


def test_progressbar_set_workers_renders_one_line_per_worker():
    """Multi-worker mode: each worker gets its own sub-line; the
    bar still appears once at the top."""
    fake_stderr = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake_stderr.write(data)
        def flush(self):
            pass
        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        bar = ProgressBar(total=100, label="scan")
        bar.set_workers([
            ("Worker-1", "python", "src/api/users.py"),
            ("Worker-2", "javascript", "src/web/main.js"),
            ("Worker-3", "bash", "scripts/build.sh"),
        ])
        bar.update(10)
        bar.finish()
    out = fake_stderr.getvalue()
    # Each worker label must appear in the rendered output.
    assert "Worker-1" in out
    assert "Worker-2" in out
    assert "Worker-3" in out
    # Section tags rendered with brackets.
    assert "[" in out and "python" in out
    # Item paths show.
    assert "src/api/users.py" in out


def test_progressbar_set_workers_then_clear_falls_back_to_current_item():
    """Passing an empty workers list reverts to the single sub-line
    behavior (used by callers that never enter multi-worker mode)."""
    fake_stderr = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake_stderr.write(data)
        def flush(self):
            pass
        def isatty(self):
            return True

    with patch.object(sys, "stderr", _FakeStream()):
        bar = ProgressBar(total=10)
        bar.set_workers([("W1", "py", "x.py")])
        bar.update(1)
        bar._last_drawn = 0.0  # bypass throttle for this test
        # Now switch back to single-item mode
        bar.set_workers([])
        bar.set_current_item("single_file.py")
        bar.refresh()
        bar.finish()
    out = fake_stderr.getvalue()
    assert "single_file.py" in out


def test_progressbar_multi_worker_lines_never_wrap():
    """Multi-worker mode honors the same wrap-prevention guarantee:
    every line emitted to stderr fits in the live terminal width."""
    fake_stderr = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake_stderr.write(data)
        def flush(self):
            pass
        def isatty(self):
            return True

    import shutil
    import os as _os
    fake_size = _os.terminal_size((100, 40))
    with patch.object(sys, "stderr", _FakeStream()), \
         patch.object(shutil, "get_terminal_size", return_value=fake_size):
        bar = ProgressBar(total=4, label="scan")
        bar.set_workers([
            ("ForkPoolWorker-1", "python",
             "some/very/long/path/to/file_aaaaaaaaaaaaa.py"),
            ("ForkPoolWorker-2", "typescript",
             "another/long/path/main_component.ts"),
            ("ForkPoolWorker-3", "rust", "src/parser/lex.rs"),
            ("ForkPoolWorker-4", "c", "kernel/sched/fair.c"),
        ])
        bar.update(1)
        bar._last_drawn = 0.0
        bar.refresh()
        bar.finish()

    output = fake_stderr.getvalue()
    lines = output.replace("\r", "\n").split("\n")
    for line in lines:
        assert _visible_len(line) <= 100, (
            f"multi-worker line of {_visible_len(line)} cols "
            f"exceeds terminal width 100: {line!r}"
        )


def test_progressbar_lines_never_wrap_in_narrow_terminal():
    """Regression: in a 100-col tmux pane (the bug we fixed), every
    line written to stderr must fit — no padding past the live width
    that would cause physical-line wrap and break ``\\033[F``."""
    fake_stderr = io.StringIO()

    class _FakeStream:
        def write(self, data):
            return fake_stderr.write(data)
        def flush(self):
            pass
        def isatty(self):
            return True

    import shutil
    import os
    fake_size = os.terminal_size((100, 40))
    with patch.object(sys, "stderr", _FakeStream()), \
         patch.object(shutil, "get_terminal_size", return_value=fake_size):
        bar = ProgressBar(total=3, label="bench")
        for i in range(1, 4):
            # Long sub-line filename — exactly the kind of input that
            # used to overflow the 120-col pad.
            bar.set_current_item(f"some/very/long/path/to/file_{i:02d}.py")
            bar.update(1)
            bar._last_drawn = 0.0  # bypass throttle
        bar.finish()

    # Split on the cursor-up sequence to look at each redraw chunk.
    output = fake_stderr.getvalue()
    # Each \n inside the bar output belongs to a single 2-line draw.
    # The visible content of every line (between \r/\n boundaries)
    # must fit in 100 cols.
    lines = output.replace("\r", "\n").split("\n")
    for line in lines:
        # Strip ANSI escapes for visible-length check.
        assert _visible_len(line) <= 100, (
            f"line of {_visible_len(line)} cols exceeds terminal width 100: "
            f"{line!r}"
        )


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
    # ANSI cursor-preceding-line sequence used to overwrite in place.
    # Accept either the bare \033[F or the explicit \033[1F form
    # (functionally identical; current code emits the explicit form).
    assert "\033[F" in out or "\033[1F" in out


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
