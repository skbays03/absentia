"""Property-based fuzz tests for the per-language extractors.

For every registered extractor, generate random source bytes and
assert a small set of universal invariants:

  - ``parse()`` never raises (tree-sitter recovers from arbitrary
    bytes by emitting ERROR nodes; that should be transparent).
  - ``extract()`` never raises and returns an iterable of
    ``(Entity, FeatureSet)`` tuples.
  - Every emitted entity has a positive 1-indexed line number.
  - No null bytes leak into entity ``qualified_name``.
  - Entity ``id`` values are unique within one ``extract()`` call.
  - Reported features are always ``frozenset[str]``.

Hypothesis runs each test against many random inputs (default
budget per test). Failing examples are persisted in
``.hypothesis/examples`` so a flake reproduces deterministically.

These tests catch the class of bugs that *only* random input
finds: parser-specific ERROR-node handling, off-by-one line
counts on edge-of-file entities, qualified-name construction
when the source contains weird characters.
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from lacuna.entities import Entity, FeatureSet
from lacuna.extractors import discover_extractor_classes


_EXTRACTOR_CLASSES = sorted(
    discover_extractor_classes().items(),
    key=lambda kv: kv[0],
)

# Tighter time budget per case keeps the suite snappy. Hypothesis
# defaults are conservative; for "never crash" properties we
# don't need exhaustive shrinking.
_FUZZ_SETTINGS = settings(
    max_examples=25,
    deadline=2000,                                # 2s per example
    suppress_health_check=[HealthCheck.too_slow],
)


@pytest.fixture(scope="module")
def extractors() -> dict[str, type]:
    return dict(_EXTRACTOR_CLASSES)


# Inputs: a mix of printable ASCII, Unicode, and null bytes — the
# kinds of garbage tree-sitter is most likely to choke on. Bounded
# size keeps fuzz cycles fast.
_SOURCE_STRATEGY = st.one_of(
    st.binary(min_size=0, max_size=2_000),
    st.text(
        alphabet=st.characters(min_codepoint=0x01, max_codepoint=0xFFFF),
        min_size=0,
        max_size=2_000,
    ).map(lambda s: s.encode("utf-8", errors="replace")),
)


@pytest.mark.parametrize(
    "language,extractor_cls",
    _EXTRACTOR_CLASSES,
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
class TestExtractorInvariants:
    """One class per extractor, hypothesis-driven."""

    @_FUZZ_SETTINGS
    @given(source=_SOURCE_STRATEGY)
    def test_parse_never_crashes(
        self, language: str, extractor_cls: type, source: bytes
    ) -> None:
        extractor = extractor_cls()
        # parse() must accept any bytes — tree-sitter's ERROR-node
        # recovery makes this a hard guarantee, not aspirational.
        extractor.parse(source)

    @_FUZZ_SETTINGS
    @given(source=_SOURCE_STRATEGY)
    def test_extract_yields_well_formed_items(
        self, language: str, extractor_cls: type, source: bytes
    ) -> None:
        extractor = extractor_cls()
        root = extractor.parse(source)
        items = list(extractor.extract(root, "fuzz/sample"))

        seen_ids: set[str] = set()
        for entity, features in items:
            # Shape contract.
            assert isinstance(entity, Entity)
            assert isinstance(features, FeatureSet)

            # Line numbers are 1-indexed, must be positive.
            assert entity.line > 0, (
                f"{language}: emitted entity with non-positive line "
                f"{entity.line!r} from source {source!r}"
            )

            # Null bytes in qualified_name break suppression-key
            # storage and JSON serialization in subtle ways. They
            # should never appear; if they do, the extractor is
            # bleeding through raw bytes.
            assert "\x00" not in entity.qualified_name, (
                f"{language}: null byte in qualified_name "
                f"{entity.qualified_name!r}"
            )

            # ID uniqueness inside one extract() call. Cross-call
            # uniqueness is the storage layer's job.
            assert entity.id not in seen_ids, (
                f"{language}: duplicate entity id {entity.id!r} from "
                f"source {source!r}"
            )
            seen_ids.add(entity.id)

            # Every feature value collection should be a frozenset
            # of strings — the mining layer assumes this.
            for kind, values in features.by_kind.items():
                assert isinstance(values, frozenset), (
                    f"{language}: feature kind {kind!r} is "
                    f"{type(values).__name__}, expected frozenset"
                )
                for v in values:
                    assert isinstance(v, str), (
                        f"{language}: non-str feature value {v!r} in "
                        f"{kind!r}"
                    )
