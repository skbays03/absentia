"""End-to-end TUI smoke tests via Textual's Pilot."""
from __future__ import annotations

import pytest

from lacuna.config import Config
from lacuna.tui import LacunaApp


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
    app = LacunaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#main_table")
        # Synthetic corpus produces one gap (delete_user missing @audit).
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_tui_rescan_keeps_gap_count_stable(tmp_path):
    _write_corpus(tmp_path)
    app = LacunaApp(root=tmp_path, config=Config())
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
    app = LacunaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "gaps" in app.sub_title
        assert "rules" in app.sub_title
        assert "entities" in app.sub_title


@pytest.mark.asyncio
async def test_tui_view_switching_changes_subtitle_and_table(tmp_path):
    _write_corpus(tmp_path)
    app = LacunaApp(root=tmp_path, config=Config())
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
    app = LacunaApp(root=tmp_path, config=Config())
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


@pytest.mark.asyncio
async def test_tui_watch_toggle_sets_timer(tmp_path):
    _write_corpus(tmp_path)
    app = LacunaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._watch_timer is None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is not None

        await pilot.press("w")
        await pilot.pause()
        assert app._watch_timer is None
