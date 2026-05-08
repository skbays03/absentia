"""Unit tests for the post-check export feature."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from absentia.entities import Entity
from absentia.export import (
    _FORMATS,
    build_export_path,
    prompt_and_export,
    render_csv,
    render_html,
    render_json,
    render_markdown,
    render_sarif,
    render_text,
)
from absentia.mining import Gap, Rule


# ── Test fixtures ─────────────────────────────────────────────────


@pytest.fixture
def sample_data():
    """A scan result with two gaps over one rule, for renderer tests."""
    rule = Rule(
        group_id="directory:src/api/",
        feature_kind="decorator",
        feature_value="@audit",
        support_n=4,
        support_total=5,
    )
    gap1 = Gap(rule_id=rule.id, entity_id="src/api/users.py::delete_user")
    gap2 = Gap(rule_id=rule.id, entity_id="src/api/orders.py::refund")
    entities = {
        "src/api/users.py::delete_user": Entity(
            kind="function",
            qualified_name="src/api/users.py::delete_user",
            file_path="src/api/users.py",
            line=42,
        ),
        "src/api/orders.py::refund": Entity(
            kind="function",
            qualified_name="src/api/orders.py::refund",
            file_path="src/api/orders.py",
            line=15,
        ),
    }
    rules_by_id = {rule.id: rule}
    gaps = [gap1, gap2]
    scan_stats = {
        "root": "/tmp/demo",
        "started_at": "2026-05-08T23:00:00+00:00",
        "duration_ms": 1234.5,
        "files_seen": 5,
        "files_unchanged": 0,
        "entities_scanned": 6,
        "groups": 2,
        "rules": 3,
        "suppressed": 0,
        "min_confidence": 0.8,
        "min_group_size": 3,
    }
    return {
        "root": Path("/tmp/demo"),
        "gaps": gaps,
        "rules_by_id": rules_by_id,
        "entities": entities,
        "scan_stats": scan_stats,
    }


@pytest.fixture
def empty_data():
    """A scan with zero gaps — renderers must handle this without
    crashing or emitting empty tables."""
    scan_stats = {
        "root": "/tmp/clean",
        "started_at": "2026-05-08T23:00:00+00:00",
        "duration_ms": 100.0,
        "files_seen": 1,
        "entities_scanned": 1,
        "groups": 0,
        "rules": 0,
        "suppressed": 0,
        "min_confidence": 0.8,
        "min_group_size": 3,
    }
    return {
        "root": Path("/tmp/clean"),
        "gaps": [],
        "rules_by_id": {},
        "entities": {},
        "scan_stats": scan_stats,
    }


# ── build_export_path ─────────────────────────────────────────────


def test_build_export_path_uses_corpus_subdir(tmp_path):
    """Path is <base>/docs/absentia/<corpus_name>/gaps-<ts>.<ext>."""
    ts = datetime(2026, 5, 8, 23, 45, 30, tzinfo=timezone.utc)
    out = build_export_path(tmp_path, "linux", "md", timestamp=ts)
    assert out.parent == (tmp_path / "docs" / "absentia" / "linux").resolve()
    assert out.name == "gaps-2026-05-08T23-45-30.md"


def test_build_export_path_filename_safe_iso():
    """Colons in ISO timestamps would break Windows paths — verify the
    formatter uses hyphens."""
    ts = datetime(2026, 5, 8, 23, 45, 30, tzinfo=timezone.utc)
    out = build_export_path(Path("/tmp"), "demo", "json", timestamp=ts)
    assert ":" not in out.name


def test_build_export_path_extension_pass_through():
    """The extension is appended literally — ``sarif.json`` round-trips
    as ``gaps-<ts>.sarif.json``."""
    ts = datetime(2026, 5, 8, 23, 45, 30, tzinfo=timezone.utc)
    out = build_export_path(Path("/tmp"), "demo", "sarif.json", timestamp=ts)
    assert out.name.endswith(".sarif.json")


# ── Renderer happy-path coverage ──────────────────────────────────


def test_render_markdown_includes_gaps_and_rules(sample_data):
    out = render_markdown(**sample_data)
    assert "absentia check — `demo`" in out
    assert "delete_user" in out
    assert "refund" in out
    assert "@audit" in out
    assert "## Gaps" in out
    assert "## Rules referenced by gaps" in out
    # 2 gap rows + the rules-referenced row.
    assert out.count("@audit") >= 2


def test_render_markdown_no_gaps_path(empty_data):
    out = render_markdown(**empty_data)
    assert "No gaps" in out
    assert "## Gaps" not in out


def test_render_html_is_valid_html(sample_data):
    out = render_html(**sample_data)
    assert out.startswith("<!DOCTYPE html>")
    assert "</body></html>" in out
    assert "delete_user" in out
    assert "@audit" in out
    assert "@media print" in out  # print-friendly CSS present


def test_render_html_escapes_user_data():
    """Entity names with HTML metacharacters must be escaped."""
    rule = Rule(
        group_id="directory:src/api/",
        feature_kind="decorator",
        feature_value="<script>alert(1)</script>",
        support_n=4,
        support_total=5,
    )
    entities = {
        "x": Entity(kind="function", qualified_name="x", file_path="x.py", line=1),
    }
    gap = Gap(rule_id=rule.id, entity_id="x")
    out = render_html(
        root=Path("/tmp/x"),
        gaps=[gap],
        rules_by_id={rule.id: rule},
        entities=entities,
        scan_stats={
            "started_at": "x", "duration_ms": 0,
            "files_seen": 1, "entities_scanned": 1, "groups": 1,
            "rules": 1, "suppressed": 0,
        },
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_text_is_plain_ascii(sample_data):
    out = render_text(**sample_data)
    assert "absentia check — demo" in out
    assert "delete_user" in out
    assert "refund" in out
    assert "@audit" in out
    # No HTML / Markdown markup.
    assert "<" not in out
    assert "##" not in out


def test_render_json_round_trips(sample_data):
    out = render_json(**sample_data)
    parsed = json.loads(out)
    assert parsed["meta"]["root"] == "/tmp/demo"
    assert parsed["meta"]["gaps_total"] == 2
    assert len(parsed["gaps"]) == 2
    assert parsed["gaps"][0]["entity"]["file_path"] in (
        "src/api/users.py", "src/api/orders.py",
    )
    assert parsed["gaps"][0]["rule"]["feature_value"] == "@audit"


def test_render_csv_one_header_plus_one_row_per_gap(sample_data):
    import csv
    import io

    out = render_csv(**sample_data)
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][0] == "gap_id"
    assert "feature_value" in rows[0]
    # 1 header + 2 data rows.
    assert len(rows) == 3
    # data rows reference the right entities.
    file_paths = {r[2] for r in rows[1:]}
    assert file_paths == {"src/api/users.py", "src/api/orders.py"}


def test_render_sarif_schema_shape(sample_data):
    out = render_sarif(**sample_data)
    parsed = json.loads(out)
    assert parsed["version"] == "2.1.0"
    assert "$schema" in parsed
    assert len(parsed["runs"]) == 1
    run = parsed["runs"][0]
    assert run["tool"]["driver"]["name"] == "absentia"
    # 1 referenced rule, 2 gap results.
    assert len(run["tool"]["driver"]["rules"]) == 1
    assert len(run["results"]) == 2
    # Each result has a physicalLocation pointing at the entity file.
    assert run["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] in {"src/api/users.py", "src/api/orders.py"}
    # Levels reflect confidence (0.8 → "note", since not >= 0.9).
    assert run["results"][0]["level"] == "note"


def test_format_table_covers_six(sample_data):
    """Sanity check on the format menu: exactly 6 entries, each with
    a unique file extension and a renderer function in module globals."""
    from absentia import export as exp

    assert len(_FORMATS) == 6
    extensions = [f[2] for f in _FORMATS]
    assert len(set(extensions)) == 6
    for _, _, _, fn_name in _FORMATS:
        assert callable(getattr(exp, fn_name))


# ── prompt_and_export interactive flow ────────────────────────────


def test_prompt_export_no_answer_returns_none(sample_data, capsys):
    """User says N → no file written, no error, returns None."""
    with patch("builtins.input", return_value="n"):
        result = prompt_and_export(**sample_data)
    assert result is None


def test_prompt_export_full_flow_writes_file(sample_data, tmp_path, capsys):
    """Full happy path: y → format=1 (md) → location=1 (custom) → path."""
    inputs = iter([
        "y",                       # export?
        "1",                       # format = markdown
        "1",                       # location = custom
        str(tmp_path / "out"),     # custom path
    ])
    with patch("builtins.input", lambda _prompt="": next(inputs)):
        result = prompt_and_export(**sample_data)

    assert result is not None
    assert result.exists()
    assert result.suffix == ".md"
    # File is under <custom>/docs/absentia/demo/
    assert result.parent.name == "demo"
    assert result.parent.parent.name == "absentia"
    body = result.read_text()
    assert "delete_user" in body
    assert "@audit" in body


def test_prompt_export_default_path_first_use_saves_to_settings(
    sample_data, tmp_path, capsys,
):
    """Picking 'default' with no default set → prompt → save the
    user's choice to settings.json."""
    settings_file = tmp_path / "settings.json"
    inputs = iter([
        "y",                      # export?
        "4",                      # format = JSON
        "2",                      # location = default
        str(tmp_path / "exports"),  # set new default
    ])
    with patch("absentia.settings.settings_path", return_value=settings_file):
        with patch("builtins.input", lambda _prompt="": next(inputs)):
            result = prompt_and_export(**sample_data)

    from absentia.settings import load_settings
    saved = load_settings(settings_file)
    assert result is not None
    assert saved.default_export_path == str((tmp_path / "exports").resolve())
    assert result.suffix == ".json"


def test_prompt_export_default_path_subsequent_use_reads_settings(
    sample_data, tmp_path, capsys,
):
    """Picking 'default' with a default already set → uses it without
    re-prompting."""
    from absentia.settings import Settings, save_settings

    settings_file = tmp_path / "settings.json"
    save_settings(
        Settings(default_export_path=str(tmp_path / "saved-default")),
        path=settings_file,
    )
    inputs = iter([
        "y",       # export?
        "5",       # format = CSV
        "2",       # location = default (no further prompt)
    ])
    with patch("absentia.settings.settings_path", return_value=settings_file):
        with patch("builtins.input", lambda _prompt="": next(inputs)):
            result = prompt_and_export(**sample_data)

    assert result is not None
    assert result.suffix == ".csv"
    assert "saved-default" in str(result)


def test_prompt_export_invalid_format_returns_failure(sample_data, capsys):
    """Garbage format pick → 'Export Failed!' on stderr, returns None."""
    inputs = iter(["y", "999"])
    with patch("builtins.input", lambda _prompt="": next(inputs)):
        result = prompt_and_export(**sample_data)
    err = capsys.readouterr().err
    assert result is None
    assert "Export Failed" in err


def test_prompt_export_eof_during_prompt_aborts(sample_data, capsys):
    """Hit Ctrl-D at the y/N prompt → cancel without error."""
    def _raise(*_a, **_kw):
        raise EOFError

    with patch("builtins.input", _raise):
        result = prompt_and_export(**sample_data)
    assert result is None
