"""End-to-end TUI smoke tests via Textual's Pilot."""
from __future__ import annotations

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


@pytest.mark.asyncio
async def test_tui_mounts_and_runs_initial_scan(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#main_table")
        # Synthetic corpus produces one gap (delete_user missing @audit).
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_tui_rescan_keeps_gap_count_stable(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#main_table")
        first = table.row_count
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert table.row_count == first


@pytest.mark.asyncio
async def test_tui_subtitle_shows_scan_stats(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "gaps" in app.sub_title
        assert "rules" in app.sub_title
        assert "entities" in app.sub_title


@pytest.mark.asyncio
async def test_tui_view_switching_changes_subtitle_and_table(tmp_path):
    _write_corpus(tmp_path)
    app = AbsentiaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        assert app._watch_timer is None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is not None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is None
