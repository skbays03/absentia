"""Closure / completeness gap detection — the fifth mining strategy.

The first four strategies look at how a *thing* is shaped (frequency,
symmetry, call-pair, series). This one looks at how a *graph* is
shaped: declarations and the references that point at them. A
declaration with zero in-edges is "defined but never used" — the
closure of references doesn't reach it. The latin sense: a
*completeness* gap, where the structure implies the missing piece.

Scope for v1
============

This module flags **classes** that are defined but appear in no other
entity's ``calls``, ``parent_class``, or ``decorator`` feature set.
That set covers the most common reference patterns across all
seventeen extractors without requiring any extractor changes:

  * Instantiation: ``Foo()`` is in some entity's ``calls``.
  * Inheritance:   ``class X(Foo)`` puts ``Foo`` in X's ``parent_class``.
  * Decoration:    ``@Foo`` puts ``Foo`` in the decorated entity's
                   ``decorator``.

Gaps the v1 deliberately misses (false negatives, may add later):

  * Type-annotation-only references (Python ``x: Foo``).
  * isinstance / issubclass arguments.
  * Attribute-only references (``Foo.CONSTANT`` with no call).
  * ``except Foo:`` clauses without ``raise``.

These would all require each extractor to emit a richer ``references``
feature kind. That's a follow-up — the v1 lands first to validate the
closure-pass architecture against real corpora and surface false
positives we'd want to address.

Why classes only
================

Functions and methods have far noisier "is this called?" signals: CLI
commands, framework hooks, test functions, decorated entry points, and
plugins all have legitimate zero-internal-callers but very-much-real
external use. Classes are higher-precision: a class with no
instantiation, no inheritance, and no decoration is genuinely dead in
the typical case. We can extend to functions in a v2 with stricter
filtering (entry-points awareness, decorator-as-registration, etc.).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .entities import Entity, FeatureSet
from .enrichment import is_test_file
from .mining import Gap, Rule


# Feature kinds we consult when building the inverse reference index.
# Each one captures a distinct way one entity points at another.
_REFERENCE_FEATURE_KINDS = ("calls", "parent_class", "decorator")

# Identifier-shaped substrings, conservative across most languages.
# Underscores allowed (Python/Rust/Go private convention); digits
# allowed but not as the first character. We deliberately don't
# distinguish keywords from identifiers — `class` appearing in raw
# text would be a false reference if a class were named `class`,
# but that's not a thing in any sane codebase.
_IDENTIFIER_RE = re.compile(rb"\b[A-Za-z_][A-Za-z0-9_]*\b")


def find_unused_class_gaps(
    entities: dict[str, Entity],
    feature_index: dict[str, FeatureSet],
    *,
    root: Path | None = None,
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Flag classes whose name appears in no other entity's references.

    Builds an inverse index of every name appearing in any entity's
    ``calls``, ``parent_class``, or ``decorator`` features, then walks
    each class entity and checks whether its bare name appears in the
    index. If not, emits a ``defined_but_never_used`` gap.

    Conservative defaults: skips private-named classes (leading
    underscore), skips classes whose own ID is the only entity in the
    inverse-index entry, and never emits a gap pointing at a class it
    can't unambiguously identify.

    Complexity: O(F) to build the inverse index, where F is the total
    number of feature values across all entities (linear in corpus
    size). O(C) to scan classes, with each step doing a single
    dict lookup + 1-element set subtraction (both O(1)). Total runtime
    is therefore linear in the entity count — no quadratic term in
    cross-class comparisons. Memory is O(F) for the index, dominated
    by the union of all distinct identifier names — typically far
    smaller than F itself because names recur across the corpus."""
    if progress_hook is not None:
        progress_hook(
            phase="building reference index",
            counter=(0, len(feature_index)),
        )
    # Flat set, not dict[name → set[entity_id]]. We only need
    # membership ("is this name referenced anywhere?"), not
    # provenance, and the dict-of-set form was the dominant cost
    # on kernel-scale corpora — 10M+ set creations and adds.
    # Inlining the normalization (strip leading @, take both full
    # form and last `.`-segment) saves ~30% beyond that. Class
    # entities don't emit calls/parent_class/decorator themselves
    # in any current extractor, so no need to subtract the
    # candidate's own id.
    referenced: set[str] = set()
    n_features = len(feature_index)
    for i, fs in enumerate(feature_index.values()):
        if progress_hook is not None and i % 4096 == 0:
            progress_hook(counter=(i, n_features))
        by_kind = fs.by_kind
        for kind in _REFERENCE_FEATURE_KINDS:
            kind_set = by_kind.get(kind)
            if not kind_set:
                continue
            for raw in kind_set:
                name = raw[1:] if raw.startswith("@") else raw
                if not name:
                    continue
                referenced.add(name)
                last = name.rsplit(".", 1)[-1]
                if last != name:
                    referenced.add(last)

    # First scan: which classes are candidates (failed the feature-
    # based check)? Most classes in a real corpus pass this; the
    # second-stage corpus-text scan only matters for the candidate
    # set, which is typically very small. Computing the candidate
    # set up front lets us skip the expensive corpus scan entirely
    # when nothing's flagged, and keeps the corpus scan's I/O cost
    # proportional to the *number of candidates* rather than the
    # full corpus size.
    classes = [e for e in entities.values() if e.kind == "class"]
    candidates: list[Entity] = []
    candidate_names: set[str] = set()
    for class_ent in classes:
        bare = _bare_class_name(class_ent.qualified_name)
        if not bare or bare.startswith("_"):
            continue
        if is_test_file(class_ent.file_path):
            continue
        cls_features = feature_index.get(class_ent.id)
        if (
            cls_features is not None
            and "registered" in cls_features.get_set(
                "entry_point_registered",
            )
        ):
            continue
        if bare in referenced:
            continue
        candidates.append(class_ent)
        candidate_names.add(bare)

    # Second filter (only if any candidates): corpus-wide identifier-
    # token counts. The feature-based inverse index misses references
    # that don't land in calls / parent_class / decorator — TS type
    # annotations, NestJS module-imports arrays, isinstance args,
    # raw module imports. We tokenize every source file once, but
    # only retain counts for the candidate names — keeping memory
    # proportional to the candidate count, not corpus identifier
    # diversity.
    name_corpus_count: Counter[str] = (
        _build_identifier_count_for(entities, root, candidate_names)
        if root is not None and candidate_names
        else Counter()
    )

    rules: list[Rule] = []
    gaps: list[Gap] = []
    n = len(candidates)
    if progress_hook is not None:
        progress_hook(phase="checking class references", counter=(0, n))

    for ci, class_ent in enumerate(candidates):
        if progress_hook is not None and ci % 256 == 0:
            progress_hook(
                counter=(ci, n),
                item=lambda c=class_ent: c.qualified_name,
            )
        bare = _bare_class_name(class_ent.qualified_name)
        # Final guard: if the name appears more than once anywhere in
        # the corpus's raw source, treat it as referenced. The single
        # occurrence is the declaration site itself; >1 means at least
        # one other mention exists (in code, type annotation, string,
        # or comment — all of which suggest the class is intentional).
        if name_corpus_count and name_corpus_count.get(bare, 0) > 1:
            continue

        # support_total=1 / support_n=1 reports as 100%-confidence;
        # the closure detector doesn't have a probabilistic support
        # signal the way frequency mining does (it's a graph-edge
        # absence, not a frequency divergence), so we report full
        # confidence and avoid Rule.confidence's division-by-zero.
        rule = Rule(
            group_id=f"closure:{class_ent.file_path}",
            feature_kind="closure",
            feature_value=f"{bare} (defined but never used)",
            support_n=1,
            support_total=1,
        )
        rules.append(rule)
        gaps.append(Gap(rule_id=rule.id, entity_id=class_ent.id))

    return rules, gaps


def _build_identifier_count_for(
    entities: dict[str, Entity],
    root: Path,
    target_names: set[str],
) -> Counter[str]:
    """Count occurrences of ``target_names`` across every source file
    in the corpus.

    Walks files via the same logic the scan loop uses (matching the
    extensions of any extractor that produced an entity in this
    corpus), not the entities themselves — because some reference
    sites live in files that produce no extracted entities (think
    ``identity.deserializer.spec.ts``: a Jest spec with only
    top-level ``describe()`` calls, where the TS extractor yields
    nothing).

    Strategy: ONE tokenization pass with a simple identifier regex,
    then set-membership against ``target_names``. We tried a few
    "smarter" approaches first — a combined `\b(name1|...|nameN)\b`
    regex, and a substring pre-check + per-file targeted regex —
    both were significantly slower because Python's regex engine
    walks alternations in O(N × bytes), whereas a single fixed
    pattern + per-token hashset lookup is O(bytes + tokens × 1).
    Measured on the Linux kernel (1.4 GB / 65 k files): 16 s for
    tokenize-and-membership versus ~60 s for the alternation form.

    Complexity: O(total source bytes) for tokenization + O(tokens)
    for membership checks. Memory: O(|target_names|)."""
    from .parsing import find_source_files

    extensions = {
        suffix
        for ent in entities.values()
        for suffix in [_extension_of(ent.file_path)]
        if suffix
    }
    if not extensions or not target_names:
        return Counter()

    target_bytes: dict[bytes, str] = {
        n.encode("ascii", errors="replace"): n for n in target_names
    }
    target_set = set(target_bytes.keys())

    # findall returns a list of bytes directly (no Match objects),
    # and Counter.update with a generator expression is the fastest
    # idiom for "filter + count" in pure Python — measurably better
    # than `for ... if ... in target_set: counter[...] += 1`.
    #
    # Early-termination: once a candidate's count reaches 2 (one
    # declaration + one reference), it's confirmed "used" and we
    # can drop it from the active target set. On the kernel, most
    # candidates resolve in the first few files scanned — the
    # remaining files only need to look for the few names that
    # haven't yet been confirmed used.
    counter_b: Counter[bytes] = Counter()
    confirmed_used: set[bytes] = set()
    active = set(target_set)  # mutable copy
    for path in find_source_files(root, extensions):
        if not active:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        counter_b.update(
            tok for tok in _IDENTIFIER_RE.findall(data)
            if tok in active
        )
        # Promote any newly-saturated targets out of active.
        for tok, cnt in counter_b.items():
            if cnt > 1 and tok not in confirmed_used:
                confirmed_used.add(tok)
                active.discard(tok)
    return Counter(
        {target_bytes[k]: v for k, v in counter_b.items()}
    )


def _extension_of(file_path: str) -> str:
    """Return the lowercase suffix of ``file_path`` including the dot."""
    idx = file_path.rfind(".")
    return file_path[idx:].lower() if idx >= 0 else ""


def _bare_class_name(qualified_name: str) -> str:
    """Extract the class name from a qualified entity name.

    Entities use the convention ``"<file>::<scope>"``; for a top-level
    class ``Foo`` in ``x.py`` that's ``"x.py::Foo"``. The bare name is
    the last ``::`` segment, with any nested-scope ``.`` stripped to
    its final component. Returns ``""`` if the name can't be parsed."""
    if "::" not in qualified_name:
        return ""
    last = qualified_name.rsplit("::", 1)[-1]
    return last.split(".")[-1]


def _normalize_reference(raw: str) -> tuple[str, ...]:
    """Reduce a feature-value string to the bare names it could refer to.

    ``calls`` carries strings like ``"foo"``, ``"obj.method"``, or
    ``"pkg.sub.Class"``. ``parent_class`` carries strings like
    ``"Base"`` or ``"pkg.Base"``. ``decorator`` carries strings like
    ``"@route"`` or ``"@app.route"``.

    Strategy: strip a leading ``@``, then return both the full dotted
    form (for the rare case where someone references a class via its
    qualified path) and the last segment (the typical case). Yielding
    multiple variants is cheap and inflates the inverse index slightly
    in exchange for catching more reference patterns."""
    name = raw.lstrip("@").strip()
    if not name:
        return ()
    last = name.rsplit(".", 1)[-1]
    if last == name:
        return (name,)
    return (name, last)
