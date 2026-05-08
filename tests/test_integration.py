"""End-to-end test on a synthetic corpus written into a tmp dir."""
from __future__ import annotations

from pathlib import Path

from absentia.cli import cmd_check, cmd_init
from absentia.config import Config


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


def test_synthetic_corpus_yields_one_gap(tmp_path, capsys):
    """The 4-of-5-with-@audit fixture must produce exactly one gap on
    delete_user, missing @audit. Rule count is intentionally not
    asserted — additional feature_kinds (has_docstring,
    has_return_type, has_param_types, ...) may legitimately produce
    extra 100%-confidence rules with zero gaps. The behavior we care
    about is gap-detection, not internal rule accounting."""
    _write_corpus(tmp_path)
    code = cmd_check(root=tmp_path, config=Config(), quiet=False)
    assert code == 1, "non-zero exit when gaps are present"
    out = capsys.readouterr().out
    assert "delete_user" in out
    assert "@audit" in out
    assert "1 gaps" in out


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


def test_suppress_then_check_silences_the_gap(tmp_path, capsys):
    """End-to-end: scan a synthetic corpus, suppress the gap, re-scan and
    confirm it's gone."""
    import json as json_module
    from absentia.cli import cmd_suppress

    _write_corpus(tmp_path)
    cmd_check(root=tmp_path, config=Config(), quiet=True, as_json=True)
    payload = json_module.loads(capsys.readouterr().out)
    assert payload["summary"]["gaps"] == 1
    short = payload["gaps"][0]["short_id"]

    code = cmd_suppress(
        root=tmp_path, gap_id=short, reason="test suppression",
        remove=False, as_list=False,
    )
    assert code == 0
    capsys.readouterr()  # clear suppress output

    cmd_check(root=tmp_path, config=Config(), quiet=True, as_json=True)
    after = json_module.loads(capsys.readouterr().out)
    assert after["summary"]["gaps"] == 0
    assert after["scan"]["suppressed"] == 1


def test_suppress_list_shows_existing_suppressions(tmp_path, capsys):
    from absentia.cli import cmd_suppress
    _write_corpus(tmp_path)
    # Run check once to populate state.db
    cmd_check(root=tmp_path, config=Config(), quiet=True)
    capsys.readouterr()

    cmd_suppress(
        root=tmp_path, gap_id="g-aaaaaaa", reason="abc",
        remove=False, as_list=False,
    )
    capsys.readouterr()
    cmd_suppress(
        root=tmp_path, gap_id=None, reason=None, remove=False, as_list=True,
    )
    out = capsys.readouterr().out
    assert "g-aaaaaaa" in out
    assert "abc" in out


def test_json_output_is_parseable_with_expected_shape(tmp_path, capsys):
    import json as json_module

    _write_corpus(tmp_path)
    code = cmd_check(root=tmp_path, config=Config(), quiet=False, as_json=True)
    assert code == 1
    payload = json_module.loads(capsys.readouterr().out)
    # Gap count is the load-bearing assertion; rule count is brittle
    # (any new feature_kind that fires a 100%-confidence rule on this
    # fixture would bump it without changing user-facing behavior).
    assert payload["summary"]["gaps"] == 1
    assert payload["summary"]["rules"] >= 1
    assert payload["scan"]["entities_scanned"] >= 5
    [gap] = payload["gaps"]
    assert gap["entity"]["qualified_name"].endswith("delete_user")
    assert gap["rule"]["feature_value"] == "@audit"
    assert gap["rule"]["confidence"] == 0.8


def test_init_creates_config_and_state_dir(tmp_path, capsys):
    from absentia.storage import SCHEMA_VERSION
    code = cmd_init(root=tmp_path, force=False)
    assert code == 0
    assert (tmp_path / "absentia.toml").is_file()
    assert (tmp_path / ".absentia").is_dir()
    assert (tmp_path / ".absentia" / ".gitignore").read_text() == "*\n"
    assert (tmp_path / ".absentia" / "version").read_text() == f"{SCHEMA_VERSION}\n"
    assert "Initialized absentia" in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    (tmp_path / "absentia.toml").write_text("# pre-existing\n")
    code = cmd_init(root=tmp_path, force=False)
    assert code == 1
    assert (tmp_path / "absentia.toml").read_text() == "# pre-existing\n"


def test_init_then_check_works_end_to_end(tmp_path, capsys):
    _write_corpus(tmp_path)
    cmd_init(root=tmp_path, force=False)
    capsys.readouterr()  # clear init output

    config = Config.from_file(tmp_path / "absentia.toml")
    code = cmd_check(root=tmp_path, config=config, quiet=False)
    assert code == 1
    assert "delete_user" in capsys.readouterr().out


def test_init_appends_absentia_to_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\n.venv/\n")
    cmd_init(root=tmp_path, force=False)
    contents = (tmp_path / ".gitignore").read_text()
    assert ".absentia/" in contents.splitlines()
    # Doesn't duplicate prior entries
    assert contents.count("*.pyc") == 1


def test_init_does_not_duplicate_absentia_in_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text(".absentia/\n")
    cmd_init(root=tmp_path, force=False)
    contents = (tmp_path / ".gitignore").read_text()
    assert contents.count(".absentia/") == 1


# ── --max-gaps tolerance ─────────────────────────────────────────────


def test_max_gaps_above_count_exits_zero(tmp_path, capsys):
    """--max-gaps N where N >= len(gaps) should pass the build."""
    _write_corpus(tmp_path)  # produces 1 gap
    code = cmd_check(
        root=tmp_path, config=Config(), quiet=True, max_gaps=5,
    )
    assert code == 0


def test_max_gaps_zero_fails_on_any_gap(tmp_path, capsys):
    """--max-gaps 0 fails on any gap (matches default behavior)."""
    _write_corpus(tmp_path)  # produces 1 gap
    code = cmd_check(
        root=tmp_path, config=Config(), quiet=True, max_gaps=0,
    )
    assert code == 1


def test_max_gaps_unset_fails_on_any_gap(tmp_path, capsys):
    """No --max-gaps argument: any gap fails the build."""
    _write_corpus(tmp_path)  # produces 1 gap
    code = cmd_check(root=tmp_path, config=Config(), quiet=True)
    assert code == 1


def test_max_gaps_with_no_gaps_exits_zero(tmp_path, capsys):
    """No gaps + any --max-gaps value → exit 0."""
    api = tmp_path / "api"
    api.mkdir()
    (api / "x.py").write_text(
        "def audit(fn):\n    return fn\n\n"
        "@audit\ndef a():\n    pass\n\n"
        "@audit\ndef b():\n    pass\n\n"
        "@audit\ndef c():\n    pass\n"
    )
    for n in (0, 5, 100):
        code = cmd_check(
            root=tmp_path, config=Config(), quiet=True, max_gaps=n,
        )
        assert code == 0, f"--max-gaps {n} on no-gap corpus must exit 0"
