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

# Match a filename of the shape ``<prefix>_<letter>.<ext>`` — e.g.
# ``part_a.md``, ``lecture_b.txt``. The letter must be a single
# a-z / A-Z. Used by find_letter_series_gaps (Item E).
_LETTER_SERIES_RE = re.compile(r"^(.+_)([A-Za-z])\.([^.]+)$")

# Match a directory name of the shape ``<prefix>v<number>`` — e.g.
# ``config_v2``, ``apiV3``, ``schema_v01``. Captures the prefix
# (everything before the v marker) and the trailing integer.
# Used by find_version_directory_gaps (Item F).
_VERSION_DIR_RE = re.compile(r"^(.+?)[vV](\d+)$")

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


# ── Letter-series detector (Item E) ──────────────────────────────


def find_letter_series_gaps(
    entities: dict[str, Entity],
    *,
    min_members: int = _DEFAULT_MIN_MEMBERS,
    max_intra_cluster_gap: int = _MAX_INTRA_CLUSTER_GAP,
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Detect missing-letter gaps in same-directory file sequences.

    Recognizes filenames of the shape ``<prefix>_<letter>.<ext>``:
    ``part_a.md``, ``lecture_b.txt``, ``chapter_a.rst``. For each
    ``(directory, prefix, ext)`` group, clusters letters by ordinal
    proximity (so a stray ``part_z.md`` doesn't pull a 24-element
    range out of an ``a/b/d`` series), and flags missing letters
    inside each cluster's range as gaps.

    Output mirrors ``find_series_gaps``: one rule per missing letter
    with a stable feature_value like ``part_c.md``, one gap per rule
    pointing at the existing entity nearest to the missing slot. Case
    is preserved from the cluster's first member so ``Part_A.md``
    stays uppercase, ``part_a.md`` stays lowercase."""
    file_paths = sorted({ent.file_path for ent in entities.values()})

    # {(directory, prefix, ext, case): [(ordinal, letter, path)]}
    by_group: dict[
        tuple[str, str, str, str], list[tuple[int, str, str]]
    ] = defaultdict(list)
    for path in file_paths:
        directory, _, basename = path.rpartition("/")
        match = _LETTER_SERIES_RE.match(basename)
        if match is None:
            continue
        prefix, letter, ext = match.group(1), match.group(2), match.group(3)
        case = "upper" if letter.isupper() else "lower"
        ordinal = ord(letter.lower()) - ord("a")
        by_group[(directory, prefix, ext, case)].append(
            (ordinal, letter, path)
        )

    rules: list[Rule] = []
    gaps: list[Gap] = []
    n_groups = len(by_group)
    if progress_hook is not None:
        progress_hook(
            phase="grouping by directory + prefix",
            counter=(0, n_groups),
        )

    for gi, (key, members) in enumerate(by_group.items()):
        directory, prefix, ext, case = key
        if progress_hook is not None:
            progress_hook(
                counter=(gi, n_groups),
                item=lambda d=directory, p=prefix: f"{d or '/'}::{p}",
            )
        if len(members) < min_members:
            continue
        members.sort()
        clusters = _cluster_letters_by_proximity(
            members, max_intra_cluster_gap,
        )

        for cluster in clusters:
            if len(cluster) < min_members:
                continue
            ords = [o for o, _l, _p in cluster]
            first, last = ords[0], ords[-1]
            expected = set(range(first, last + 1))
            present = set(ords)
            missing = sorted(expected - present)
            if not missing:
                continue

            for missing_ord in missing:
                missing_letter = chr(ord("a") + missing_ord)
                if case == "upper":
                    missing_letter = missing_letter.upper()
                feature_value = f"{prefix}{missing_letter}.{ext}"
                rule = Rule(
                    group_id=f"letter-series:{directory or '.'}::{prefix}",
                    feature_kind="series",
                    feature_value=feature_value,
                    support_n=len(cluster),
                    support_total=len(expected),
                )

                anchor_path = cluster[0][2]
                for ordv, _l, path in cluster:
                    if ordv < missing_ord:
                        anchor_path = path
                    else:
                        break
                anchor_id = _first_entity_in_file(entities, anchor_path)
                if anchor_id is None:
                    continue

                rules.append(rule)
                gaps.append(Gap(rule_id=rule.id, entity_id=anchor_id))

    return rules, gaps


def _cluster_letters_by_proximity(
    members: list[tuple[int, str, str]],
    max_gap: int,
) -> list[list[tuple[int, str, str]]]:
    """Greedy clustering for the (ordinal, letter, path) tuples."""
    if not members:
        return []
    clusters: list[list[tuple[int, str, str]]] = [[members[0]]]
    for m in members[1:]:
        if m[0] - clusters[-1][-1][0] <= max_gap:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    return clusters


# ── Version-directory series detector (Item F) ───────────────────


def find_version_directory_gaps(
    entities: dict[str, Entity],
    *,
    min_members: int = _DEFAULT_MIN_MEMBERS,
    max_intra_cluster_gap: int = _MAX_INTRA_CLUSTER_GAP,
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Detect missing version directories in the corpus.

    Recognizes directory names of the shape ``<prefix>v<number>``:
    ``config_v1``, ``api_v2``, ``schema_v04``, also ``apiV3`` /
    ``configV1`` (capital V). For each ``(parent_directory, prefix,
    case)`` group, clusters version numbers by numeric proximity and
    flags missing versions inside each cluster's range as gaps.

    Doesn't require introducing a directory entity kind. Walks the
    distinct directories already present in the entity store, parses
    the basename, and renders gaps that anchor at the nearest existing
    sibling version directory's first entity. Width is preserved
    (``v01`` style stays zero-padded)."""
    directory_paths = sorted(
        {ent.file_path.rpartition("/")[0] for ent in entities.values()}
    )
    # Walk every prefix component too, not just immediate parents —
    # ``src/api_v2/users.py`` should classify ``src/api_v2`` as a
    # versioned directory, not just ``src``.
    seen_dirs: set[str] = set()
    for d in directory_paths:
        parts = d.split("/")
        for i in range(1, len(parts) + 1):
            seen_dirs.add("/".join(parts[:i]))

    # {(parent, prefix, case_marker): [(version_number, width, dir_path)]}
    by_group: dict[
        tuple[str, str, str], list[tuple[int, int, str]]
    ] = defaultdict(list)
    for d in seen_dirs:
        if not d:
            continue
        parent, _, basename = d.rpartition("/")
        match = _VERSION_DIR_RE.match(basename)
        if match is None:
            continue
        prefix, num_str = match.group(1), match.group(2)
        # Recover the case of the v/V from the original basename.
        v_marker = basename[len(prefix)]  # 'v' or 'V'
        by_group[(parent, prefix, v_marker)].append(
            (int(num_str), len(num_str), d)
        )

    rules: list[Rule] = []
    gaps: list[Gap] = []
    n_groups = len(by_group)
    if progress_hook is not None:
        progress_hook(
            phase="grouping by parent + prefix",
            counter=(0, n_groups),
        )

    for gi, (key, members) in enumerate(by_group.items()):
        parent, prefix, v_marker = key
        if progress_hook is not None:
            progress_hook(
                counter=(gi, n_groups),
                item=lambda p=parent, pre=prefix: f"{p or '/'}::{pre}v*",
            )
        if len(members) < min_members:
            continue
        members.sort()
        clusters = _cluster_by_proximity(members, max_intra_cluster_gap)

        for cluster in clusters:
            if len(cluster) < min_members:
                continue
            cluster_nums = [n for n, _w, _d in cluster]
            first, last = cluster_nums[0], cluster_nums[-1]
            expected = set(range(first, last + 1))
            present = set(cluster_nums)
            missing = sorted(expected - present)
            if not missing:
                continue
            width = max(w for _n, w, _d in cluster)

            for missing_num in missing:
                missing_str = f"{missing_num:0{width}d}"
                feature_value = f"{prefix}{v_marker}{missing_str}"
                rule = Rule(
                    group_id=f"version-dir:{parent or '.'}::{prefix}",
                    feature_kind="series",
                    feature_value=feature_value,
                    support_n=len(cluster),
                    support_total=len(expected),
                )

                anchor_dir = cluster[0][2]
                for num, _w, dpath in cluster:
                    if num < missing_num:
                        anchor_dir = dpath
                    else:
                        break
                anchor_id = _first_entity_in_dir(entities, anchor_dir)
                if anchor_id is None:
                    continue

                rules.append(rule)
                gaps.append(Gap(rule_id=rule.id, entity_id=anchor_id))

    return rules, gaps


def _first_entity_in_dir(
    entities: dict[str, Entity], dir_path: str,
) -> str | None:
    """Return any entity_id whose file lives under ``dir_path``."""
    prefix = dir_path + "/"
    for ent in entities.values():
        if ent.file_path.startswith(prefix):
            return ent.id
    return None


# ── Ordinal-alphabet series detector (Item G) ────────────────────


# Each entry is (alphabet_id, members). The "members" tuple is
# ordered for stable output but order doesn't affect detection —
# what matters is set membership. To add a new alphabet, append a
# row here and re-run the tests; the engine picks it up immediately
# without selector or mining changes.
# Each alphabet is a slot list. Each slot is a tuple of synonym
# names — a class "has the slot" if any synonym matches one of its
# methods. This collapses the false-positive case where a class
# with create/update/delete + findAll fired three separate gaps
# for "missing read", "missing get", and "missing list" against
# three independent alphabets. Now read/get/list/find/findAll/
# findOne all count as the same slot, and the gap fires only when
# *no* synonym is present.
#
# `name_hints` is a tuple of substrings that must appear in the
# class name for the alphabet to fire. Empty tuple = no gating
# (any class can match). Hints prevent the "happens-to-have-init-
# and-close so missing run" false positive on lifecycle services.
_OrdinalAlphabet = tuple[
    str,                      # alphabet id
    tuple[tuple[str, ...], ...],  # ordered slots, each = synonym tuple
    tuple[str, ...],          # name_hints (case-insensitive substrings)
]

_ORDINAL_ALPHABETS: tuple[_OrdinalAlphabet, ...] = (
    (
        "crud",
        (
            ("create", "add", "insert"),
            ("read", "get", "list", "find", "findAll", "findOne",
             "fetch", "show"),
            ("update", "edit", "modify", "patch"),
            ("delete", "remove", "destroy"),
        ),
        # Only fire on classes whose name hints at CRUD intent —
        # repositories, services, controllers, resolvers,
        # explicit CRUD test classes. Rules out lifecycle / utility
        # classes that happen to share a method name.
        ("crud", "repo", "repository", "controller", "service",
         "resolver", "dao", "store", "manager"),
    ),
    (
        "lifecycle_three",
        (
            ("init", "initialize", "setup", "start"),
            ("run", "execute", "process"),
            ("close", "teardown", "stop", "shutdown", "cleanup"),
        ),
        # Only fire when the class clearly represents a unit of
        # work / job / task — not on every lifecycle-shaped class.
        ("job", "task", "worker", "runner", "pipeline", "step"),
    ),
    (
        "ordinal_three",
        (
            ("first",),
            ("second",),
            ("third",),
        ),
        (),  # no gating; the names are unambiguous enough on their own
    ),
)


# How completely the alphabet must already be implemented before we
# claim "this class is following the convention." A class with only
# one matching slot shouldn't get flagged for missing the rest;
# that's not a convention being followed, it's just one method
# that happens to share a name with a known alphabet.
_MIN_PRESENT_FRACTION = 0.75


def find_ordinal_series_gaps(
    entities: dict[str, Entity],
    *,
    alphabets: tuple[_OrdinalAlphabet, ...] = _ORDINAL_ALPHABETS,
    min_present_fraction: float = _MIN_PRESENT_FRACTION,
    progress_hook: Any = None,
) -> tuple[list[Rule], list[Gap]]:
    """Detect named-alphabet series with one element missing.

    A class ``TestUserCRUD`` with methods ``test_create``,
    ``test_read``, ``test_update`` — but no ``test_delete`` — has
    a hole at the fourth CRUD slot. Same shape as numeric / letter
    series, but the "alphabet" is a known ordinal vocabulary (CRUD,
    init/run/close, first/second/third) instead of a positional
    sequence.

    Each alphabet is a list of *slots*; each slot is a tuple of
    synonyms (e.g. read/get/list/find all count as the read slot).
    A slot is "present" if any synonym matches a class method.
    The detector fires when ≥``min_present_fraction`` of slots are
    present and exactly one slot is empty — that empty slot is the
    gap. Synonyms collapse the prior false positive where a class
    with create/update/delete + findAll triggered three separate
    gaps.

    Each alphabet also carries a ``name_hints`` tuple. If non-empty,
    the class name (case-insensitive) must contain at least one
    hint substring. Rules out the "lifecycle service happens to
    have init+close so flagged for missing run" failure mode."""
    methods_by_class: dict[str, list[Entity]] = defaultdict(list)
    classes_by_qn: dict[str, Entity] = {}
    for ent in entities.values():
        if ent.kind == "class":
            classes_by_qn[ent.qualified_name] = ent
        elif ent.kind == "method":
            class_qn = ent.qualified_name.rsplit(".", 1)[0]
            methods_by_class[class_qn].append(ent)

    rules: list[Rule] = []
    gaps: list[Gap] = []
    n_classes = len(classes_by_qn)
    if progress_hook is not None:
        progress_hook(
            phase="checking ordinal alphabets",
            counter=(0, n_classes),
        )

    for ci, (class_qn, class_ent) in enumerate(classes_by_qn.items()):
        if progress_hook is not None:
            progress_hook(
                counter=(ci, n_classes),
                item=lambda q=class_qn: q,
            )
        method_names = {
            m.qualified_name.rsplit(".", 1)[-1]
            for m in methods_by_class.get(class_qn, ())
        }
        if not method_names:
            continue
        class_basename = class_qn.rsplit("::", 1)[-1].lower()

        for alpha_id, slots, name_hints in alphabets:
            if name_hints and not any(
                h in class_basename for h in name_hints
            ):
                continue
            min_present = max(2, int(min_present_fraction * len(slots)))

            for prefix in _candidate_prefixes(method_names):
                normalized = {
                    n[len(prefix):] if n.startswith(prefix) else n
                    for n in method_names
                }
                # Each slot is present iff any of its synonyms is
                # in the normalized method set.
                present_slots: list[int] = []
                missing_slots: list[int] = []
                for i, synonyms in enumerate(slots):
                    if any(s in normalized for s in synonyms):
                        present_slots.append(i)
                    else:
                        missing_slots.append(i)
                if len(present_slots) < min_present:
                    continue
                if len(missing_slots) != 1:
                    continue
                missing_idx = missing_slots[0]
                missing_canonical = slots[missing_idx][0]
                feature_value = f"{prefix}{missing_canonical}"
                rule = Rule(
                    group_id=f"ordinal:{alpha_id}::{class_qn}",
                    feature_kind="series",
                    feature_value=feature_value,
                    support_n=len(present_slots),
                    support_total=len(slots),
                )
                rules.append(rule)
                gaps.append(Gap(rule_id=rule.id, entity_id=class_ent.id))
                break
            else:
                continue
            break  # one alphabet hit per class — outer loop break

    return rules, gaps


def _candidate_prefixes(method_names: set[str]) -> list[str]:
    """Return the empty prefix plus the single most-common
    underscore-separated prefix in ``method_names`` (if there is
    one shared by ≥2 names). Conservative — we don't try every
    possible prefix to avoid combinatorial blowup."""
    candidates: list[str] = [""]
    counter: dict[str, int] = defaultdict(int)
    for n in method_names:
        if "_" in n:
            counter[n.split("_", 1)[0] + "_"] += 1
    if counter:
        most_common = max(counter.items(), key=lambda kv: kv[1])
        if most_common[1] >= 2:
            candidates.append(most_common[0])
    return candidates
