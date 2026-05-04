"""End-to-end test on a synthetic corpus written into a tmp dir."""
from __future__ import annotations

from pathlib import Path

from lacuna.cli import cmd_check


def _write_corpus(root: Path) -> None:
    """Five fns in api/, four with @audit. Decorator def in a different
    directory so it doesn't pollute the api/ group."""
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


def test_synthetic_corpus_yields_one_rule_one_gap(tmp_path, capsys):
    _write_corpus(tmp_path)
    code = cmd_check(
        root=tmp_path, min_confidence=0.8, min_group_size=3, quiet=False
    )
    assert code == 1, "non-zero exit when gaps are present"
    out = capsys.readouterr().out
    assert "delete_user" in out
    assert "@audit" in out
    assert "1 gaps  ·  1 rules" in out


def test_clean_corpus_yields_zero_gaps(tmp_path, capsys):
    api = tmp_path / "api"
    api.mkdir()
    # Three identical, all decorated — no gap.
    (api / "x.py").write_text(
        "def audit(fn):\n    return fn\n\n"
        "@audit\ndef a():\n    pass\n\n"
        "@audit\ndef b():\n    pass\n\n"
        "@audit\ndef c():\n    pass\n"
    )
    code = cmd_check(
        root=tmp_path, min_confidence=0.8, min_group_size=3, quiet=False
    )
    assert code == 0
    assert "No gaps" in capsys.readouterr().out


def test_check_on_nonexistent_path_returns_two(tmp_path, capsys):
    code = cmd_check(
        root=tmp_path / "does-not-exist",
        min_confidence=0.8,
        min_group_size=3,
        quiet=True,
    )
    assert code == 2
