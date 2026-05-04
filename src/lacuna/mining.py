"""Rule mining and gap detection.

Within each group, count how often each feature value appears across
members. Values appearing in at least `min_confidence` of members become
**rules**; members lacking the rule's value become **gaps**.

MVP: single-feature predicates only (one feature kind at a time). Compound
predicates via FP-growth come in a later pass, gated by the
`max_predicate_size` config option.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .entities import FeatureSet
from .selectors import Group


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Gap:
    rule_id: str
    entity_id: str

    @property
    def id(self) -> str:
        return f"{self.rule_id}::{self.entity_id}"


def mine(
    groups: list[Group],
    feature_index: dict[str, FeatureSet],
    *,
    min_confidence: float = 0.8,
    feature_kind: str = "decorator",
) -> tuple[list[Rule], list[Gap]]:
    """Mine rules from groups; emit gaps for non-conforming members."""
    rules: list[Rule] = []
    gaps: list[Gap] = []

    for group in groups:
        # Tally each feature value's support across the group's members.
        counter: Counter[str] = Counter()
        for entity_id in group.members:
            features = feature_index.get(entity_id)
            if features is None:
                continue
            for value in features.get_set(feature_kind):
                counter[value] += 1

        total = len(group.members)
        if total == 0:
            continue

        for value, count in counter.items():
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

            # Members lacking this value are gaps under this rule.
            for entity_id in group.members:
                features = feature_index.get(entity_id)
                if features is None or value not in features.get_set(feature_kind):
                    gaps.append(Gap(rule_id=rule.id, entity_id=entity_id))

    return rules, gaps
