"""Series-gap detection — the fourth mining strategy.

Frequency mining catches "most do X, this doesn't." Symmetry catches
"this has left without right." Call-pair catches "this caller invokes
A but not the matching B."

Series catches a fourth flavor of gap: **a sequence with a hole**.
Not "most have X" — but "the pattern of names implies a missing
element." A migration directory containing ``0001_*.py``, ``0002_*.py``,
and ``0004_*.py`` has an implied gap at ``0003`` even if no
frequency, symmetry, or call-pair rule fires.

This is the latin "lacuna in a manuscript" sense — a void at a
specific known position in a structured sequence.

The detector groups files by directory, looks for leading-digit
basenames, clusters them by sequential proximity (so a stray
``0099_*`` doesn't pull a 96-gap range out of a real ``0001`` /
``0002`` series), and flags missing numbers within each cluster.

Output integrates with the existing Rule/Gap pipeline so the TUI,
suppress, formatters, and cross-strategy dedup all keep working.
"""
from __future__ import annotations

from typing import Any

import re
from collections import defaultdict

from .entities import Entity
from .mining import Gap, Rule


# Match a leading run of digits at the start of a filename.
_LEADING_NUMBER_RE = re.compile(r"^(\d+)")

# Match the file extension — used to render a readable
# "missing 0003_*.py" message instead of bare "missing 0003_*".
_FILE_EXTENSION_RE = re.compile(r"\.([^.]+)$")

# How big a numeric gap between consecutive existing members can be
# while still being part of the same series cluster. Keeps a stray
# ``0099_*`` from claiming a 96-element gap range against an
# ``0001`` / ``0002`` cluster.
_MAX_INTRA_CLUSTER_GAP = 5

# Minimum cluster size before we'll claim it's a "series" worth
# checking. Two-element sequences are too easy to coincidentally form.
_DEFAULT_MIN_MEMBERS = 3


def find_series_gaps(
    entities: dict[str, Entity],
    *,
    min_members: int = _DEFAULT_MIN_MEMBERS,
    max_intra_cluster_gap: int = _MAX_INTRA_CLUSTER_GAP,
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Detect missing-number gaps in same-directory file sequences.

    For each directory in the corpus, collect basenames with leading
    digits (``0001_users.py``, ``0002_orders.py``, ...). Cluster them
    by numeric proximity (``max_intra_cluster_gap``), and for each
    cluster of ≥ ``min_members``, flag any non-contiguous numbers
    inside the cluster's range as gaps.

    Returns ``(rules, gaps)`` in the same shape as the rest of the
    mining strategies: one rule per missing index (with a unique,
    stable feature_value like ``0003_*.py``), one gap per rule
    pointing at the existing entity nearest to the missing slot.
    """
    file_paths = sorted({ent.file_path for ent in entities.values()})

    # {directory: [(number, width, path)]}
    by_dir: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for path in file_paths:
        directory, _, basename = path.rpartition("/")
        match = _LEADING_NUMBER_RE.match(basename)
        if match is None:
            continue
        num_str = match.group(1)
        by_dir[directory].append((int(num_str), len(num_str), path))

    rules: list[Rule] = []
    gaps: list[Gap] = []
    n_dirs = len(by_dir)
    if progress_hook is not None:
        progress_hook(phase="grouping by directory", counter=(0, n_dirs))

    for di, (directory, members) in enumerate(by_dir.items()):
        if progress_hook is not None:
            progress_hook(
                counter=(di, n_dirs),
                item=lambda d=directory: d or "/",
            )
        if len(members) < min_members:
            continue
        members.sort()
        clusters = _cluster_by_proximity(members, max_intra_cluster_gap)

        for cluster in clusters:
            if len(cluster) < min_members:
                continue
            cluster_nums = [n for n, _w, _p in cluster]
            first, last = cluster_nums[0], cluster_nums[-1]
            expected = set(range(first, last + 1))
            present = set(cluster_nums)
            missing = sorted(expected - present)
            if not missing:
                continue

            width = max(w for _n, w, _p in cluster)
            ext = _common_extension(cluster) or ""

            for missing_num in missing:
                missing_str = f"{missing_num:0{width}d}"
                feature_value = f"{missing_str}_*{ext}"
                rule = Rule(
                    group_id=f"series:{directory or '.'}",
                    feature_kind="series",
                    feature_value=feature_value,
                    support_n=len(cluster),
                    support_total=len(expected),
                )

                # Anchor entity: the largest existing member below
                # ``missing_num``, or the first cluster member if
                # nothing precedes the gap.
                anchor_path = cluster[0][2]
                for num, _w, path in cluster:
                    if num < missing_num:
                        anchor_path = path
                    else:
                        break
                anchor_id = _first_entity_in_file(entities, anchor_path)
                if anchor_id is None:
                    # No entity in the anchor file (file might have no
                    # extractable members). Skip this gap silently;
                    # we have nothing reasonable to point the user at.
                    continue

                rules.append(rule)
                gaps.append(Gap(rule_id=rule.id, entity_id=anchor_id))

    return rules, gaps


def _cluster_by_proximity(
    members: list[tuple[int, int, str]],
    max_gap: int,
) -> list[list[tuple[int, int, str]]]:
    """Greedy clustering: split sorted members into runs where
    consecutive numbers differ by ≤ ``max_gap``."""
    if not members:
        return []
    clusters: list[list[tuple[int, int, str]]] = [[members[0]]]
    for m in members[1:]:
        if m[0] - clusters[-1][-1][0] <= max_gap:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    return clusters


def _common_extension(cluster: list[tuple[int, int, str]]) -> str:
    """Return the extension shared by all cluster members, including
    the leading dot. Empty string if extensions vary."""
    extensions: set[str] = set()
    for _n, _w, path in cluster:
        basename = path.rpartition("/")[-1]
        match = _FILE_EXTENSION_RE.search(basename)
        extensions.add(f".{match.group(1)}" if match else "")
    if len(extensions) == 1:
        return extensions.pop()
    return ""


def _first_entity_in_file(
    entities: dict[str, Entity], file_path: str,
) -> str | None:
    """Return any entity_id whose file_path matches, deterministically."""
    for ent in entities.values():
        if ent.file_path == file_path:
            return ent.id
    return None
