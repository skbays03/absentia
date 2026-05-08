"""Tests for `absentia --purge` and `--purge-all`."""
from __future__ import annotations

from pathlib import Path

from absentia.cli import cmd_purge


def _make_absentia_state(root: Path) -> Path:
    """Create a minimal but plausible .absentia/ directory."""
    state = root / ".absentia"
    state.mkdir()
    (state / "version").write_text("2\n")
    (state / "state.db").write_bytes(b"sqlite stub")
    return state


def test_purge_removes_state_dir(tmp_path):
    state = _make_absentia_state(tmp_path)
    assert state.exists()
    rc = cmd_purge(tmp_path, confirm=False)
    assert rc == 0
    assert not state.exists()


def test_purge_keeps_absentia_toml(tmp_path):
    """--purge should remove state but leave the config file."""
    _make_absentia_state(tmp_path)
    config = tmp_path / "absentia.toml"
    config.write_text("[scan]\ninclude = ['.']\n")
    cmd_purge(tmp_path, confirm=False)
    assert config.exists()


def test_purge_no_state_dir(tmp_path, capsys):
    """No .absentia/ → exit 0, print informative message."""
    rc = cmd_purge(tmp_path, confirm=False)
    assert rc == 0
    captured = capsys.readouterr()
    assert "nothing to purge" in captured.out


def test_purge_refuses_unrelated_absentia_dir(tmp_path, capsys):
    """A .absentia/ that doesn't look like ours (no version/state.db) is refused."""
    rogue = tmp_path / ".absentia"
    rogue.mkdir()
    (rogue / "random.txt").write_text("not absentia")
    rc = cmd_purge(tmp_path, confirm=False)
    assert rc == 1
    assert rogue.exists()  # not deleted
    captured = capsys.readouterr()
    assert "doesn't look like an absentia state directory" in captured.err


def test_purge_not_a_directory(tmp_path, capsys):
    """Pointing at a file, not a directory, errors out cleanly."""
    f = tmp_path / "regular_file.txt"
    f.write_text("hello")
    rc = cmd_purge(f, confirm=False)
    assert rc == 2
    captured = capsys.readouterr()
    assert "not a directory" in captured.err


def test_purge_keeps_other_files_in_state_dir(tmp_path):
    """The whole .absentia/ tree gets removed, but parent dir untouched."""
    _make_absentia_state(tmp_path)
    sibling = tmp_path / "src"
    sibling.mkdir()
    (sibling / "foo.py").write_text("pass")
    cmd_purge(tmp_path, confirm=False)
    # Sibling and its contents survive
    assert sibling.exists()
    assert (sibling / "foo.py").exists()


def test_purge_refuses_non_interactive_with_confirm(tmp_path, capsys, monkeypatch):
    """confirm=True + non-TTY = refuse to delete (safety guard)."""
    import sys
    _make_absentia_state(tmp_path)
    # stdin not a TTY (the pytest default already has it not-a-tty,
    # but make it explicit so the test doesn't depend on env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = cmd_purge(tmp_path, confirm=True)
    assert rc == 1
    assert (tmp_path / ".absentia").exists()  # nothing deleted
    captured = capsys.readouterr()
    assert "non-interactive" in captured.err
    assert "--yes" in captured.err


def test_purge_disclaimer_lists_what_will_be_lost(tmp_path, capsys, monkeypatch):
    """The disclaimer enumerates state.db, suppressions, and notes
    that source code + absentia.toml are not touched."""
    import sys
    _make_absentia_state(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    cmd_purge(tmp_path, confirm=True)  # will refuse but still print the disclaimer
    captured = capsys.readouterr()
    out = captured.out
    assert "state.db" in out
    assert "suppressions" in out
    assert "source code" in out.lower()
    assert "absentia.toml" in out
