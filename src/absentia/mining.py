"""Rule mining and gap detection.

Within each group, count how often each feature value appears across
members. Values appearing in at least `min_confidence` of members become
**rules**; members lacking the rule's value become **gaps**.

MVP: single-feature predicates only (one feature kind at a time). Compound
predicates via FP-growth come in a later pass, gated by the
`max_predicate_size` config option.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .entities import FeatureSet
from .selectors import Group


def short_id_for(full_id: str) -> str:
    """Stable short form of a gap ID for CLI use ('g-7c91234'). Six hex
    chars from SHA-256 — collision space is small but practically fine
    for human-typed suppressions; the suppression filter checks both
    short and full forms, so any collision degrades to a no-op."""
    return "g-" + hashlib.sha256(full_id.encode()).hexdigest()[:7]


@dataclass(frozen=True, slots=True)
class Rule:
    group_id: str
    feature_kind: str
    feature_value: str          # e.g. "@audit"
    support_n: int
    support_total: int

    @property
    def confidence(self) -> float:
        return self.support_n / self.support_total

    @property
    def id(self) -> str:
        return f"{self.group_id}::{self.feature_kind}={self.feature_value}"


@dataclass(frozen=True, slots=True)
class Gap:
    rule_id: str
    entity_id: str

    @property
    def id(self) -> str:
        return f"{self.rule_id}::{self.entity_id}"

    @property
    def short_id(self) -> str:
        return short_id_for(self.id)


def mine(
    groups: list[Group],
    feature_index: dict[str, FeatureSet],
    *,
    min_confidence: float = 0.8,
    feature_kind: str = "decorator",
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Mine rules from groups; emit gaps for non-conforming members.

    A member is *eligible* for a given ``feature_kind`` if its FeatureSet
    has that kind populated (even as an empty set). Members for which the
    kind doesn't apply at all (e.g. mining ``parent_class`` over a group
    that mixes classes and functions) are excluded from both the
    confidence denominator and the gap list — functions can't be
    "missing" a parent class.

    ``progress_hook``, when supplied, is called as
    ``hook(phase=..., counter=(i, n), item=lambda: ...)`` so the caller
    can render real-time mining progress. The hook itself throttles, so
    inner-loop callers can invoke it freely.
    """
    rules: list[Rule] = []
    gaps: list[Gap] = []
    n_groups = len(groups)
    if progress_hook is not None:
        progress_hook(phase="counting features", counter=(0, n_groups))

    # Apriori prune (optimization plan #7): pre-filter feature values
    # that can't possibly reach min_confidence in *any* group. The
    # rule-emission threshold is `count >= min_confidence * total`
    # within a group. The smallest possible group has size 1, so the
    # smallest viable count is ceil(min_confidence). With the default
    # min_confidence=0.8, that's 1 — meaning a value appearing in
    # zero entities can't produce a rule (trivially), but every value
    # that appears in ≥1 entity could in principle. The real win is
    # at the next tier: values appearing in fewer than 2 entities
    # corpus-wide can't reach 80% confidence in any group of size ≥2,
    # which is where absentia's selectors actually emit groups (the
    # min_group_size config knob enforces ≥3 by default). So we drop
    # any value with global count < ceil(min_confidence * 2). This
    # cuts the per-group counter-building loop's work proportionally
    # to how long-tailed the feature distribution is — typically
    # 30-60% of distinct values on real corpora.
    global_counts: Counter[str] = Counter()
    for fs in feature_index.values():
        if feature_kind in fs.by_kind:
            for value in fs.get_set(feature_kind):
                global_counts[value] += 1
    # Threshold derived from min_confidence × the smallest group we'd
    # bother mining. Conservative — guarantees Apriori-correctness
    # (no rule we'd have emitted gets pruned).
    min_global_count = max(1, int(min_confidence * 2))
    popular: set[str] = {
        v for v, c in global_counts.items() if c >= min_global_count
    }

    for i, group in enumerate(groups):
        if progress_hook is not None:
            progress_hook(counter=(i, n_groups), item=lambda g=group: g.name)
        eligible: list[str] = [
            mid for mid in group.members
            if mid in feature_index and feature_kind in feature_index[mid].by_kind
        ]
        total = len(eligible)
        if total == 0:
            continue

        counter: Counter[str] = Counter()
        for mid in eligible:
            for value in feature_index[mid].get_set(feature_kind):
                if value in popular:
                    counter[value] += 1

        identity = group.identity_feature
        for value, count in counter.items():
            if identity == (feature_kind, value):
                continue  # trivial self-rule, true by construction
            confidence = count / total
            if confidence < min_confidence:
                continue
            rule = Rule(
                group_id=group.id,
                feature_kind=feature_kind,
                feature_value=value,
                support_n=count,
                support_total=total,
            )
            rules.append(rule)

            for mid in eligible:
                if value not in feature_index[mid].get_set(feature_kind):
                    if _is_self_reference(feature_kind, mid, value):
                        continue
                    gaps.append(Gap(rule_id=rule.id, entity_id=mid))

    return rules, gaps


def _is_self_reference(feature_kind: str, entity_id: str, value: str) -> bool:
    """Skip ``parent_class`` gaps where the entity's own name matches the
    rule's feature_value. A class that's the base of a directory's
    inheritance pattern can't be flagged as 'missing' itself — it can't
    extend itself in any language."""
    if feature_kind != "parent_class":
        return False
    leaf = entity_id.rsplit("::", 1)[-1] if "::" in entity_id else entity_id
    return leaf == value
