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
        table = app.query_one("#gaps_table")
        # Synthetic corpus produces one gap (delete_user missing @audit).
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_tui_rescan_keeps_gap_count_stable(tmp_path):
    _write_corpus(tmp_path)
    app = LacunaApp(root=tmp_path, config=Config())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#gaps_table")
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
