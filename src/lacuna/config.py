"""lacuna.toml configuration loading.

Frozen dataclasses model the config so it's easy to construct in tests
and pass around as a single object. Defaults match the values that were
previously hardcoded in the CLI.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_DEFAULT_DECORATOR_EXCLUDES: tuple[str, ...] = (
    "@property", "@staticmethod", "@classmethod",
)
_DEFAULT_PARENT_CLASS_EXCLUDES: tuple[str, ...] = (
    "object",
)


@dataclass(frozen=True)
class ScanConfig:
    include: tuple[str, ...] = (".",)
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = (
        "python", "javascript", "typescript", "tsx",
        "rust", "go", "java", "swift",
    )


@dataclass(frozen=True)
class MiningConfig:
    min_confidence: float = 0.8
    min_group_size: int = 3


@dataclass(frozen=True)
class DirectorySelectorConfig:
    enabled: bool = True
    min_members: int = 3
    kind_filter: tuple[str, ...] = ("function", "class")


@dataclass(frozen=True)
class DecoratorSelectorConfig:
    enabled: bool = True
    min_members: int = 3
    exclude: tuple[str, ...] = _DEFAULT_DECORATOR_EXCLUDES


@dataclass(frozen=True)
class ParentClassSelectorConfig:
    enabled: bool = True
    min_members: int = 3
    exclude: tuple[str, ...] = _DEFAULT_PARENT_CLASS_EXCLUDES
    kind_filter: tuple[str, ...] = (
        "class", "struct", "enum", "extension", "protocol",
        "interface", "trait", "impl",
    )


@dataclass(frozen=True)
class SelectorsConfig:
    directory: DirectorySelectorConfig = field(default_factory=DirectorySelectorConfig)
    decorator: DecoratorSelectorConfig = field(default_factory=DecoratorSelectorConfig)
    parent_class: ParentClassSelectorConfig = field(
        default_factory=ParentClassSelectorConfig
    )


@dataclass(frozen=True)
class Config:
    scan: ScanConfig = field(default_factory=ScanConfig)
    mining: MiningConfig = field(default_factory=MiningConfig)
    selectors: SelectorsConfig = field(default_factory=SelectorsConfig)

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        scan_raw = raw.get("scan", {})
        scan = ScanConfig(
            include=tuple(scan_raw.get("include", ScanConfig.include)),
            exclude=tuple(scan_raw.get("exclude", ScanConfig.exclude)),
            languages=tuple(scan_raw.get("languages", ScanConfig.languages)),
        )
        mining_raw = raw.get("mining", {})
        mining = MiningConfig(
            min_confidence=mining_raw.get("min_confidence", MiningConfig.min_confidence),
            min_group_size=mining_raw.get("min_group_size", MiningConfig.min_group_size),
        )
        sels_raw = raw.get("selectors", {})
        directory = DirectorySelectorConfig(
            enabled=sels_raw.get("directory", {}).get(
                "enabled", DirectorySelectorConfig.enabled),
            min_members=sels_raw.get("directory", {}).get(
                "min_members", DirectorySelectorConfig.min_members),
            kind_filter=tuple(sels_raw.get("directory", {}).get(
                "kind_filter", DirectorySelectorConfig.kind_filter)),
        )
        decorator = DecoratorSelectorConfig(
            enabled=sels_raw.get("decorator", {}).get(
                "enabled", DecoratorSelectorConfig.enabled),
            min_members=sels_raw.get("decorator", {}).get(
                "min_members", DecoratorSelectorConfig.min_members),
            exclude=tuple(sels_raw.get("decorator", {}).get(
                "exclude", DecoratorSelectorConfig.exclude)),
        )
        parent_class = ParentClassSelectorConfig(
            enabled=sels_raw.get("parent_class", {}).get(
                "enabled", ParentClassSelectorConfig.enabled),
            min_members=sels_raw.get("parent_class", {}).get(
                "min_members", ParentClassSelectorConfig.min_members),
            exclude=tuple(sels_raw.get("parent_class", {}).get(
                "exclude", ParentClassSelectorConfig.exclude)),
            kind_filter=tuple(sels_raw.get("parent_class", {}).get(
                "kind_filter", ParentClassSelectorConfig.kind_filter)),
        )
        return cls(
            scan=scan,
            mining=mining,
            selectors=SelectorsConfig(
                directory=directory,
                decorator=decorator,
                parent_class=parent_class,
            ),
        )


def find_config(start: Path) -> Path | None:
    """Search ``start`` and its parents for ``lacuna.toml``. Returns the
    first match or None."""
    for parent in [start, *start.parents]:
        candidate = parent / "lacuna.toml"
        if candidate.is_file():
            return candidate
    return None
