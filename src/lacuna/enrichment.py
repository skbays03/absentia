"""Corpus-level feature enrichment.

Most features are computed per-file by an extractor with no knowledge
of the rest of the corpus. A few features need the *whole corpus* to
compute — most notably "does this entity have a sibling test?", which
requires knowing what test entities exist before answering.

This module runs after extraction (when the entity store + feature
index are fully populated) and adds derived features to the index in
memory. The features aren't persisted — they're recomputed on every
scan because they depend on the corpus, not on individual files.
"""
from __future__ import annotations

from collections.abc import Iterator

from .entities import Entity, FeatureSet


# ── Test-file detection ────────────────────────────────────────────────

# Heuristics for recognizing a "test" file vs. a "source" file.
# Used both to skip test files when emitting the sibling_test feature
# (tests don't need their own tests for this convention check) and
# to find candidate test files for source entities.

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec"})
_TEST_FILENAME_PREFIXES = ("test_",)
_TEST_FILENAME_SUFFIXES = ("_test.py", "_test.go", ".test.ts",
                           ".test.tsx", ".test.js", ".spec.ts",
                           ".spec.tsx", ".spec.js")

# Sentinel "no test methods in this file" — shared across the millions
# of dict.get(test_file, _EMPTY_SET) lookups inside enrich_sibling_tests
# so we don't allocate a fresh empty set per call.
_EMPTY_SET: frozenset[str] = frozenset()


def is_test_file(file_path: str) -> bool:
    """True if ``file_path`` looks like a test file by convention."""
    # Hot path on big corpora — called per-entity (>1M times on the
    # Linux kernel). str.startswith and str.endswith both accept tuples
    # natively in C; that's a single C call vs N Python iterations of
    # an `any(... for p in PREFIXES)` generator. Same semantics, ~10×
    # faster per call. _TEST_FILENAME_PREFIXES has only one entry today
    # — kept as a tuple-of-one so adding a second prefix later doesn't
    # need a code change here.
    parts = file_path.split("/")
    if any(p in _TEST_DIR_NAMES for p in parts):
        return True
    name = parts[-1] if parts else file_path
    if name.startswith(_TEST_FILENAME_PREFIXES):
        return True
    if name.endswith(_TEST_FILENAME_SUFFIXES):
        return True
    return False


def _stem_and_ext(filename: str) -> tuple[str, str]:
    """Split ``users.py`` into ``("users", ".py")``."""
    if "." in filename:
        idx = filename.rfind(".")
        return filename[:idx], filename[idx:]
    return filename, ""


def _candidate_test_files(file_path: str) -> Iterator[str]:
    """Yield plausible test-file paths for a source file.

    Conventions covered:
      - ``src/api/users.py``        → ``tests/api/test_users.py``
      - ``api/users.py``            → ``tests/api/test_users.py``
      - any source file             → ``tests/test_<name>``
      - sibling test in same dir    → ``<dir>/test_<name>``
      - Go-flavored                  → ``<dir>/<name>_test.<ext>``
    """
    parts = file_path.split("/")
    filename = parts[-1]
    stem, ext = _stem_and_ext(filename)

    # Build the candidate test filenames (mirror by prefix or suffix)
    test_filenames = [
        f"test_{stem}{ext}",
        f"{stem}_test{ext}",
    ]

    # Build candidate directory paths
    parent_parts = parts[:-1]
    candidate_dirs: list[str] = []

    # Strip leading ``src/`` (or ``lib/``) and replace with ``tests/``.
    for prefix in ("src", "lib", "source"):
        if parent_parts and parent_parts[0] == prefix:
            mirror = ["tests"] + parent_parts[1:]
            candidate_dirs.append("/".join(mirror))
            break

    # ``tests/<rest>`` regardless of source dir
    if parent_parts:
        candidate_dirs.append("/".join(["tests"] + parent_parts))

    # Flat ``tests/``
    candidate_dirs.append("tests")

    # In-tree: same directory as the source
    candidate_dirs.append("/".join(parent_parts))

    # Yield every (dir, filename) combination, deduped
    seen: set[str] = set()
    for d in candidate_dirs:
        for tf in test_filenames:
            full = f"{d}/{tf}" if d else tf
            if full in seen:
                continue
            seen.add(full)
            yield full


def candidate_test_entity_ids(source_entity: Entity) -> Iterator[str]:
    """Yield free-function-style test entity-id candidates for ``source_entity``.

    Class-method tests (``tests/test_users.py::TestUsers.test_create``)
    aren't enumerated here because the test class name is unknown
    in advance; they're matched via :func:`build_test_method_index`
    inside :func:`enrich_sibling_tests` instead.
    """
    short_name = source_entity.qualified_name.rsplit("::", 1)[-1]
    test_func_name = f"test_{short_name}"
    for test_file in _candidate_test_files(source_entity.file_path):
        yield f"{test_file}::{test_func_name}"


def build_test_method_index(
    entities: dict[str, Entity],
) -> dict[str, set[str]]:
    """Build ``{test_file_path: {short_test_method_names}}`` from the corpus.

    Walks every entity in a test file (function or method, kind-agnostic)
    and indexes the methods named ``test_*`` by their short name. The
    short name is the final dotted component of the qualified name, so
    both free-function tests (``::test_create``) and class-method
    tests (``::TestUsers.test_create``) collapse to the same key
    (``test_create``) — which is what we want, since the source-side
    asks "is *anything* named test_<my_name> in a candidate test file?"
    """
    index: dict[str, set[str]] = {}
    for ent in entities.values():
        if ent.kind not in ("function", "method"):
            continue
        if not is_test_file(ent.file_path):
            continue
        short = ent.qualified_name.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        if short.startswith("test_"):
            index.setdefault(ent.file_path, set()).add(short)
    return index


# ── Enrichment passes ──────────────────────────────────────────────────


def enrich_sibling_tests(
    entities: dict[str, Entity],
    feature_index: dict[str, FeatureSet],
) -> None:
    """Populate ``sibling_test`` on every eligible source-side function.

    Eligible entities are kind ∈ {function, method} that live in a
    non-test file and don't have an underscore-prefixed name. Each one
    gets its FeatureSet's ``sibling_test`` kind populated:

      - ``frozenset({"sibling test"})`` — a matching test exists
      - ``frozenset()`` — no test found (this is the gap shape)

    Mining over ``sibling_test`` then emits rules for groups where most
    members have one ("8/10 functions in src/api/ have a sibling test")
    and gaps for the divergent members.

    Mutates ``feature_index`` in place.
    """
    test_methods_by_file = build_test_method_index(entities)

    # Per-source-file memoization. Both `is_test_file` and
    # `_candidate_test_files` are deterministic on file_path, but the
    # outer loop calls them once per entity — and a single source file
    # typically holds many entities. On the Linux kernel, ~640k
    # entities are spread across ~65k unique source files (~10× hit
    # rate). Materializing the candidate set as a tuple lets the
    # any() inside the loop still short-circuit on re-iteration.
    is_test_cache: dict[str, bool] = {}
    candidates_cache: dict[str, tuple[str, ...]] = {}

    for entity_id, entity in entities.items():
        if entity.kind not in ("function", "method"):
            continue
        file_path = entity.file_path
        is_test = is_test_cache.get(file_path)
        if is_test is None:
            is_test = is_test_file(file_path)
            is_test_cache[file_path] = is_test
        if is_test:
            continue
        short_name = entity.qualified_name.rsplit("::", 1)[-1]
        if short_name.startswith("_"):
            continue  # private; usually not separately tested

        target_test_name = f"test_{short_name}"
        candidates = candidates_cache.get(file_path)
        if candidates is None:
            candidates = tuple(_candidate_test_files(file_path))
            candidates_cache[file_path] = candidates
        # Match against any test file's set of test_* short names.
        # Captures both free-function tests and class-method tests
        # because the index keys on short name only.
        has_test = any(
            target_test_name in test_methods_by_file.get(test_file, _EMPTY_SET)
            for test_file in candidates
        )

        fs = feature_index.get(entity_id)
        if fs is None:
            fs = FeatureSet()
            feature_index[entity_id] = fs
        # Use a human-readable feature value so the rendered message
        # ("missing sibling test") reads naturally without special-casing
        # the formatter.
        fs.by_kind["sibling_test"] = (
            frozenset({"sibling test"}) if has_test else frozenset()
        )


def enrich_all(
    entities: dict[str, Entity],
    feature_index: dict[str, FeatureSet],
) -> None:
    """Run every enrichment pass. Single entry point for ``scan_corpus``."""
    enrich_sibling_tests(entities, feature_index)
