"""Machine-wide user settings.

A small JSON file at ``~/.absentia/settings.json`` holding preferences
that apply across every project on this machine. Currently just
``jobs_default`` — the override for :func:`parallel.default_jobs`,
which would otherwise pick half of detected cores.

Kept separate from per-project ``absentia.toml`` (a versioned,
checked-in config) and from ``calibration.json`` (machine
benchmark data — written by the calibration flow, not the user).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


SETTINGS_FILENAME = "settings.json"


@dataclass(frozen=True)
class Settings:
    """User preferences that survive across ``absentia`` invocations.

    ``jobs_default`` of ``None`` means "auto" — fall back to
    ``cpu_count // 2``. A positive integer pins the default; a
    user can still override per-invocation with ``check --jobs N``.

    ``info_hint_shown_at`` of ``None`` means the first-run "Tip: run
    `absentia --info` for an introduction" hint hasn't been shown
    yet. The CLI sets it to an ISO 8601 UTC timestamp the first
    time the hint fires (TTY only) so the hint shows once, ever.
    """
    jobs_default: int | None = None
    info_hint_shown_at: str | None = None


def settings_path() -> Path:
    """Default location: ``~/.absentia/settings.json``."""
    return Path.home() / ".absentia" / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> Settings:
    """Read settings; return defaults on missing/unparseable file.

    Treating "couldn't parse" as "no settings" matches calibration's
    approach — a malformed cache should never break the tool.
    """
    p = path if path is not None else settings_path()
    if not p.exists():
        return Settings()
    try:
        raw = json.loads(p.read_text())
        if not isinstance(raw, dict):
            return Settings()
        jd = raw.get("jobs_default")
        if jd is not None and (not isinstance(jd, int) or jd < 1):
            jd = None
        ihs = raw.get("info_hint_shown_at")
        if ihs is not None and not isinstance(ihs, str):
            ihs = None
        return Settings(jobs_default=jd, info_hint_shown_at=ihs)
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(s: Settings, path: Path | None = None) -> None:
    """Write atomically: tmp + rename."""
    p = path if path is not None else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2) + "\n")
    tmp.replace(p)
