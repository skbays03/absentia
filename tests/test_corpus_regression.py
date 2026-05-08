"""Corpus regression tests.

Reads ``tests/fixtures/corpora.toml`` and parametrizes one test per
registered corpus. Each test scans the corpus and asserts the gap +
rule + entity counts match the recorded baseline. Drift fails the
test — the fix is to commit a deliberate update to corpora.toml in
the same commit that changed the mining behavior.

Optional corpora (most external repos: redis, nestjs, etc.) are
skipped when their path doesn't exist on disk. ``absentia-self`` is
non-optional so CI always exercises at least one corpus end-to-end.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_corpora() -> list[dict]:
    """Parse corpora.toml. Resolve relative paths against the repo root
    so '.' means the  absentia repo itself rather than tests/."""
    data = tomllib.loads((FIXTURES_DIR / "corpora.toml").read_text())
    rows = data.get("corpus", [])
    for row in rows:
        p = Path(row["path"])
        if not p.is_absolute():
            row["path"] = str((REPO_ROOT / p).resolve())
    return rows


def _corpus_id(row: dict) -> str:
    """pytest's id for the parametrized test."""
    return row["name"]


@pytest.mark.parametrize("corpus", _load_corpora(), ids=_corpus_id)
def test_corpus_counts_unchanged(corpus: dict) -> None:
    """``absentia check`` against this corpus produces the recorded
    gap + rule + entity counts.

    If this fails, you either:
      (a) introduced an unintended mining-behavior regression — fix
          the code; or
      (b) intentionally changed mining behavior — update the count
          in corpora.toml in the same commit.
    """
    path = Path(corpus["path"])
    if corpus.get("optional", False) and not path.exists():
        pytest.skip(f"optional corpus {corpus['name']} not on disk at {path}")
    if not path.exists():
        pytest.fail(
            f"non-optional corpus {corpus['name']} missing at {path}"
        )

    # Import here so test collection doesn't load the whole engine
    # for tests that only check other modules.
    from absentia.cli import scan_corpus
    from absentia.config import Config
    from absentia.extractors import discover_extractors
    from absentia.storage import StateLock

    state_dir = path / ".absentia"
    state_dir.mkdir(exist_ok=True)

    with StateLock(state_dir / "lockfile"):
        config = Config()
        extractors = discover_extractors(config.scan.languages)
        result = scan_corpus(
            root=path,
            state_dir=state_dir,
            config=config,
            extractors=extractors,
            jobs=1,           # serial keeps the test deterministic
            interactive=False,
        )

    actual_gaps = len(result["gaps"])
    actual_rules = len({g.rule_id for g in result["gaps"]})
    actual_entities = len(result["entities"])

    expected_gaps = corpus["expected_gaps"]
    expected_rules = corpus["expected_rules"]
    expected_entities = corpus["expected_entities"]

    # Print the actual numbers in the assertion message so a failing
    # test tells you exactly what to write into corpora.toml.
    msg = (
        f"\n  expected: gaps={expected_gaps} rules={expected_rules} "
        f"entities={expected_entities}\n"
        f"  actual:   gaps={actual_gaps} rules={actual_rules} "
        f"entities={actual_entities}"
    )
    assert (actual_gaps, actual_rules, actual_entities) == (
        expected_gaps, expected_rules, expected_entities,
    ), msg
