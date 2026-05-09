"""End-to-end TUI smoke tests via Textual's Pilot."""
from __future__ import annotations

import asyncio

import pytest

from absentia.config import Config
from absentia.tui import AbsentiaApp


def _write_corpus(root):
    (root / "decorators.py").write_text("def audit(fn):\n    return fn\n")
    api = root / "api"
    api.mkdir()
    (api / "users.py").write_text(
        "from decorators import audit\n\n"
        "@audit\ndef create_user():\n    pass\n\n"
        "@audit\ndef update_user():\n    pass\n\n"
        "@audit\ndef list_users():\n    pass\n\n"
        "@audit\ndef get_user():\n    pass\n\n"
        "def delete_user():\n    pass\n"
    )


async def _wait_for_scan(app, pilot, *, timeout: float = 5.0) -> None:
    """Wait for the scan worker to complete + flush UI callbacks.

    Phase 2 moved scan_corpus off the main thread (Textual worker)
    so a single ``pilot.pause()`` no longer guarantees the scan is
    done. The order matters:
      1. Pause once so on_mount runs (it schedules the worker via
         call_after_refresh — without this tick the worker isn't
         even registered yet).
      2. wait_for_complete blocks until every pending worker
         finishes.
      3. Two more pauses flush the ``call_from_thread`` posts that
         landed during the worker's final cleanup (loading-screen
         pop, table re-render).
    """
    await pilot.pause()
    await asyncio.wait_for(
        app.workers.wait_for_complete(), timeout=timeout,
    )
    await pilot.pause()
    await pilot.pause()


@pytest.mark.asyncio
async def test_tui_mounts_and_runs_initial_scan(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        table = app.query_one("#main_table")
        # Synthetic corpus produces one gap (delete_user missing @audit).
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_tui_rescan_keeps_gap_count_stable(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        table = app.query_one("#main_table")
        first = table.row_count
        await pilot.press("ctrl+r")
        await _wait_for_scan(app, pilot)
        assert table.row_count == first


@pytest.mark.asyncio
async def test_tui_subtitle_shows_scan_stats(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        assert "gaps" in app.sub_title
        assert "rules" in app.sub_title
        assert "entities" in app.sub_title


@pytest.mark.asyncio
async def test_tui_view_switching_changes_subtitle_and_table(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        assert "[Gaps]" in app.sub_title

        await pilot.press("2")  # Rules
        await pilot.pause()
        assert "[Rules]" in app.sub_title

        await pilot.press("3")  # Groups
        await pilot.pause()
        assert "[Groups]" in app.sub_title

        await pilot.press("4")  # Stats
        await pilot.pause()
        assert "[Stats]" in app.sub_title

        await pilot.press("1")  # back to Gaps
        await pilot.pause()
        assert "[Gaps]" in app.sub_title


@pytest.mark.asyncio
async def test_tui_follow_then_back(tmp_path):
    """Selecting a gap and pressing `f` should land on its rule;
    `Esc` should walk back to the gaps view."""
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        # Should start in Gaps view with at least one gap.
        assert app._view == "gaps"
        assert len(app._gaps) >= 1

        await pilot.press("f")
        await pilot.pause()
        assert app._view == "rules"
        assert len(app._nav_stack) == 1

        await pilot.press("escape")
        await pilot.pause()
        assert app._view == "gaps"
        assert len(app._nav_stack) == 0


def test_editor_command_vi_family():
    """Traditional Unix editors take ``+<line> <file>``."""
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    for ed in ("vi", "vim", "nvim", "nano", "emacs", "pico"):
        assert editor_command(ed, p, 42) == [ed, "+42", str(p)]


def test_editor_command_vscode_family():
    """VS Code / Cursor / Windsurf use ``--goto <file>:<line>``."""
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    assert editor_command("code", p, 7) == ["code", "--goto", f"{p}:7"]
    assert editor_command("cursor", p, 7) == ["cursor", "--goto", f"{p}:7"]
    assert editor_command(
        "code --wait", p, 7,
    ) == ["code", "--wait", "--goto", f"{p}:7"]


def test_editor_command_sublime_helix_micro_atom():
    """Editors that take ``<file>:<line>`` directly."""
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    for ed in ("subl", "sublime_text", "hx", "helix", "micro", "atom"):
        assert editor_command(ed, p, 13) == [ed, f"{p}:13"]


def test_editor_command_textmate():
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    assert editor_command("mate", p, 99) == ["mate", "-l", "99", str(p)]


def test_editor_command_unknown_falls_back_to_vi_form():
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    assert editor_command("zed", p, 5) == ["zed", "+5", str(p)]


def test_editor_command_handles_full_path_to_binary():
    """``$EDITOR=/usr/local/bin/vim`` should still be detected as vim."""
    from absentia.tui.app import editor_command
    from pathlib import Path
    p = Path("/tmp/x.py")
    # Full path resolved via Path(...).name → "vim"
    assert editor_command("/usr/local/bin/vim", p, 1) == [
        "/usr/local/bin/vim", "+1", str(p),
    ]
    assert editor_command("/usr/local/bin/code", p, 1) == [
        "/usr/local/bin/code", "--goto", f"{p}:1",
    ]


@pytest.mark.asyncio
async def test_tui_open_editor_callback_invoked_when_provided(tmp_path):
    """When ``on_open_editor`` is set, the TUI uses it instead of
    spawning a subprocess. This is the integration seam Dev-Dashboard
    will hook when absentia is embedded as a panel."""
    _write_corpus(tmp_path)
    captured: list[tuple[str, int]] = []

    def fake_open(file_path, line):
        captured.append((str(file_path), line))

    app = AbsentiaApp(root=tmp_path, config=Config(), on_open_editor=fake_open)
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        # Trigger the open-editor action with a gap selected.
        await pilot.press("enter")
        await pilot.pause()

    assert len(captured) == 1
    file_path, line = captured[0]
    assert file_path.endswith("api/users.py")
    assert isinstance(line, int) and line > 0


@pytest.mark.asyncio
async def test_tui_watch_toggle_sets_timer(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        assert app._watch_timer is None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is not None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is None


@pytest.mark.asyncio
async def test_tui_explain_chains_into_suppress(tmp_path):
    """Pressing `s` inside the Explain modal closes it and opens
    the Suppress prompt for the same gap, without the user having
    to dismiss Explain first."""
    from absentia.storage import Storage
    from absentia.tui.app import ExplainScreen, SuppressScreen

    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)
        # Synthetic corpus produces exactly one gap; the table has it
        # selected by default.
        assert app.query_one("#main_table").row_count == 1

        # Open Explain.
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)

        # Press `s` inside Explain — should dismiss the explain modal
        # and push the suppress modal in its place.
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SuppressScreen)

        # Type a reason + Enter; suppression should land in the DB.
        await pilot.press(*"audit endpoint itself")
        await pilot.press("enter")
        await pilot.pause()

    # After the pilot exits the Storage write should be visible in
    # the per-project state DB.
    with Storage(tmp_path / ".absentia") as storage:
        suppressions = storage.load_suppressions()
    assert len(suppressions) == 1
    only = next(iter(suppressions.values()))
    assert only["reason"] == "audit endpoint itself"


@pytest.mark.asyncio
async def test_tui_export_default_path_writes_file(tmp_path):
    """Full TUI export flow with a saved default path:
       x → 1 (Markdown) → 2 (Default) → file written, no extra prompt.
    """
    from unittest.mock import patch

    from absentia.settings import Settings, save_settings
    from absentia.tui.app import (
        ExportFormatScreen, ExportLocationScreen,
    )

    _write_corpus(tmp_path)
    export_base = tmp_path / "exports"
    settings_file = tmp_path / "settings.json"
    save_settings(
        Settings(default_export_path=str(export_base)),
        path=settings_file,
    )

    app = AbsentiaApp(root=tmp_path, config=Config())
    with patch("absentia.settings.settings_path", return_value=settings_file):
        async with app.run_test() as pilot:
            await _wait_for_scan(app, pilot)

            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ExportFormatScreen)

            await pilot.press("1")  # Markdown
            await pilot.pause()
            assert isinstance(app.screen, ExportLocationScreen)

            await pilot.press("2")  # Default — no further prompt
            await pilot.pause()

    corpus_dir = export_base / "docs" / "absentia" / tmp_path.name
    md_files = list(corpus_dir.glob("gaps-*.md"))
    assert len(md_files) == 1
    body = md_files[0].read_text()
    assert "# absentia check" in body
    assert "@audit" in body


@pytest.mark.asyncio
async def test_tui_export_custom_path_writes_file(tmp_path):
    """Custom-path branch: x → 4 (JSON) → 1 (Custom) → typed path → file."""
    from unittest.mock import patch

    from absentia.tui.app import (
        ExportFormatScreen, ExportLocationScreen, ExportPathInputScreen,
    )

    _write_corpus(tmp_path)
    custom_base = tmp_path / "one-off"
    settings_file = tmp_path / "settings.json"  # never written

    app = AbsentiaApp(root=tmp_path, config=Config())
    with patch("absentia.settings.settings_path", return_value=settings_file):
        async with app.run_test() as pilot:
            await _wait_for_scan(app, pilot)

            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ExportFormatScreen)

            await pilot.press("4")  # JSON
            await pilot.pause()
            assert isinstance(app.screen, ExportLocationScreen)

            await pilot.press("1")  # Custom
            await pilot.pause()
            assert isinstance(app.screen, ExportPathInputScreen)

            await pilot.press(*str(custom_base))
            await pilot.press("enter")
            await pilot.pause()

    corpus_dir = custom_base / "docs" / "absentia" / tmp_path.name
    json_files = list(corpus_dir.glob("gaps-*.json"))
    assert len(json_files) == 1
    # No default written, since this was a custom one-off.
    assert not settings_file.exists()


@pytest.mark.asyncio
async def test_tui_export_invalid_path_reprompts(tmp_path):
    """Typing a path expanduser() rejects (e.g. '~~/foo' → RuntimeError
    'Could not determine home directory') must NOT crash the TUI —
    instead notify + re-push the input screen with the bad value
    pre-filled so the user can fix it."""
    from unittest.mock import patch

    from absentia.tui.app import (
        ExportFormatScreen, ExportLocationScreen, ExportPathInputScreen,
    )

    _write_corpus(tmp_path)
    settings_file = tmp_path / "settings.json"
    valid_base = tmp_path / "second-try"

    app = AbsentiaApp(root=tmp_path, config=Config())
    notifications: list[tuple] = []
    orig_notify = app.notify

    def captured_notify(*args, **kwargs):
        notifications.append((args, kwargs))
        return orig_notify(*args, **kwargs)
    app.notify = captured_notify  # type: ignore[method-assign]

    with patch("absentia.settings.settings_path", return_value=settings_file):
        async with app.run_test() as pilot:
            await _wait_for_scan(app, pilot)
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ExportFormatScreen)
            await pilot.press("1")  # Markdown
            await pilot.pause()
            assert isinstance(app.screen, ExportLocationScreen)
            await pilot.press("1")  # Custom
            await pilot.pause()
            assert isinstance(app.screen, ExportPathInputScreen)

            # First attempt: invalid path. The double-tilde form
            # raises RuntimeError from Path.expanduser when $HOME
            # resolution fails — same shape as the user's reported
            # crash.
            await pilot.press(*"~~/Desktop")
            await pilot.press("enter")
            await pilot.pause()

            # Should have notified + re-pushed the input screen
            # (NOT crashed, NOT silently exported nothing).
            assert isinstance(app.screen, ExportPathInputScreen)
            msgs = " ".join(str(args[0]) for args, _kw in notifications)
            assert "invalid path" in msgs.lower()

            # Second attempt: valid path. Type into the (already
            # focused) input. Need to clear the pre-filled value
            # first — Textual's Input doesn't auto-select on focus.
            input_widget = app.screen.query_one("#path_input")
            input_widget.value = str(valid_base)
            await pilot.press("enter")
            await pilot.pause()

    md_files = list(
        (valid_base / "docs" / "absentia" / tmp_path.name).glob("gaps-*.md")
    )
    assert len(md_files) == 1


@pytest.mark.asyncio
async def test_tui_export_default_first_use_saves_to_settings(tmp_path):
    """No default set yet → choosing 'default' prompts for one →
    saves to settings.json and uses it for this write."""
    from unittest.mock import patch

    from absentia.settings import load_settings
    from absentia.tui.app import (
        ExportFormatScreen, ExportLocationScreen, ExportPathInputScreen,
    )

    _write_corpus(tmp_path)
    new_default = tmp_path / "new-default"
    settings_file = tmp_path / "settings.json"  # starts unset

    app = AbsentiaApp(root=tmp_path, config=Config())
    with patch("absentia.settings.settings_path", return_value=settings_file):
        async with app.run_test() as pilot:
            await _wait_for_scan(app, pilot)

            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ExportFormatScreen)

            await pilot.press("5")  # CSV
            await pilot.pause()
            assert isinstance(app.screen, ExportLocationScreen)

            await pilot.press("2")  # Default (none set yet)
            await pilot.pause()
            assert isinstance(app.screen, ExportPathInputScreen)

            await pilot.press(*str(new_default))
            await pilot.press("enter")
            await pilot.pause()

        saved = load_settings(settings_file)
        assert saved.default_export_path == str(new_default.resolve())

    csv_files = list(
        (new_default / "docs" / "absentia" / tmp_path.name).glob("gaps-*.csv")
    )
    assert len(csv_files) == 1


@pytest.mark.asyncio
async def test_tui_explain_dismiss_without_s_does_not_suppress(tmp_path):
    """Cancelling Explain with Esc must not trigger the suppress flow."""
    from absentia.storage import Storage
    from absentia.tui.app import ExplainScreen

    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await _wait_for_scan(app, pilot)

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)

        await pilot.press("escape")
        await pilot.pause()
        # Back to the main app screen; no suppress modal pushed.
        assert not isinstance(app.screen, ExplainScreen)

    with Storage(tmp_path / ".absentia") as storage:
        suppressions = storage.load_suppressions()
    assert suppressions == {}
