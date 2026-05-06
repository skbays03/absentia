"""Tests for src/lacuna/settings.py."""
from __future__ import annotations

import json
from pathlib import Path

from lacuna.settings import (
    SETTINGS_FILENAME,
    Settings,
    load_settings,
    save_settings,
    settings_path,
)


def test_settings_path_is_under_home() -> None:
    p = settings_path()
    assert p.name == SETTINGS_FILENAME
    assert p.parent.name == ".lacuna"
    assert str(p).startswith(str(Path.home()))


def test_default_settings_has_no_jobs_default() -> None:
    s = Settings()
    assert s.jobs_default is None


def test_load_returns_defaults_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.jobs_default is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    save_settings(Settings(jobs_default=4), path=p)
    loaded = load_settings(p)
    assert loaded.jobs_default == 4


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "new" / "deep" / "settings.json"
    save_settings(Settings(jobs_default=2), path=p)
    assert p.exists()


def test_load_treats_invalid_json_as_default(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("{ this is not valid json")
    s = load_settings(p)
    assert s.jobs_default is None  # corrupt file → defaults


def test_load_treats_non_object_as_default(tmp_path: Path) -> None:
    # File parses as JSON but isn't a dict — should fall through.
    p = tmp_path / "settings.json"
    p.write_text("[1, 2, 3]")
    s = load_settings(p)
    assert s.jobs_default is None


def test_load_normalizes_invalid_jobs_default(tmp_path: Path) -> None:
    # jobs_default of wrong type or out of range → treat as unset.
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"jobs_default": "not-an-int"}))
    assert load_settings(p).jobs_default is None

    p.write_text(json.dumps({"jobs_default": 0}))
    assert load_settings(p).jobs_default is None  # < 1 is invalid

    p.write_text(json.dumps({"jobs_default": -3}))
    assert load_settings(p).jobs_default is None


def test_save_writes_atomically_via_tmp_rename(tmp_path: Path) -> None:
    # Indirect verification: after save, the .tmp sibling shouldn't
    # be left behind.
    p = tmp_path / "settings.json"
    save_settings(Settings(jobs_default=2), path=p)
    siblings = list(tmp_path.iterdir())
    names = [s.name for s in siblings]
    assert "settings.json" in names
    assert "settings.json.tmp" not in names
