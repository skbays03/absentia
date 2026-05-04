"""End-to-end test on a synthetic corpus written into a tmp dir."""
from __future__ import annotations

from pathlib import Path

from lacuna.cli import cmd_check, cmd_init
from lacuna.config import Config


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
    code = cmd_check(root=tmp_path, config=Config(), quiet=False)
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
    code = cmd_check(root=tmp_path, config=Config(), quiet=False)
    assert code == 0
    assert "No gaps" in capsys.readouterr().out


def test_check_on_nonexistent_path_returns_two(tmp_path, capsys):
    code = cmd_check(
        root=tmp_path / "does-not-exist",
        config=Config(),
        quiet=True,
    )
    assert code == 2


def test_json_output_is_parseable_with_expected_shape(tmp_path, capsys):
    import json as json_module

    _write_corpus(tmp_path)
    code = cmd_check(root=tmp_path, config=Config(), quiet=False, as_json=True)
    assert code == 1
    payload = json_module.loads(capsys.readouterr().out)
    assert payload["summary"] == {"gaps": 1, "rules": 1}
    assert payload["scan"]["entities_scanned"] >= 5
    [gap] = payload["gaps"]
    assert gap["entity"]["qualified_name"].endswith("delete_user")
    assert gap["rule"]["feature_value"] == "@audit"
    assert gap["rule"]["confidence"] == 0.8


def test_init_creates_config_and_state_dir(tmp_path, capsys):
    code = cmd_init(root=tmp_path, force=False)
    assert code == 0
    assert (tmp_path / "lacuna.toml").is_file()
    assert (tmp_path / ".lacuna").is_dir()
    assert (tmp_path / ".lacuna" / ".gitignore").read_text() == "*\n"
    assert (tmp_path / ".lacuna" / "version").read_text() == "1\n"
    assert "Initialized lacuna" in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    (tmp_path / "lacuna.toml").write_text("# pre-existing\n")
    code = cmd_init(root=tmp_path, force=False)
    assert code == 1
    assert (tmp_path / "lacuna.toml").read_text() == "# pre-existing\n"


def test_init_then_check_works_end_to_end(tmp_path, capsys):
    _write_corpus(tmp_path)
    cmd_init(root=tmp_path, force=False)
    capsys.readouterr()  # clear init output

    config = Config.from_file(tmp_path / "lacuna.toml")
    code = cmd_check(root=tmp_path, config=config, quiet=False)
    assert code == 1
    assert "delete_user" in capsys.readouterr().out


def test_init_appends_lacuna_to_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\n.venv/\n")
    cmd_init(root=tmp_path, force=False)
    contents = (tmp_path / ".gitignore").read_text()
    assert ".lacuna/" in contents.splitlines()
    # Doesn't duplicate prior entries
    assert contents.count("*.pyc") == 1


def test_init_does_not_duplicate_lacuna_in_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text(".lacuna/\n")
    cmd_init(root=tmp_path, force=False)
    contents = (tmp_path / ".gitignore").read_text()
    assert contents.count(".lacuna/") == 1
