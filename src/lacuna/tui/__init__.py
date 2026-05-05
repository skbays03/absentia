"""Lacuna's TUI — primary exploration interface.

The TUI is launched by running ``lacuna`` with no subcommand from a TTY.
It scans the project, shows gaps in a navigable table, and lets the
user inspect, suppress, and reopen state without leaving the terminal.

The engine is shared with ``lacuna check``: the TUI calls
``cli.scan_corpus`` to get the same data the batch CLI prints. This
keeps the engine single-source-of-truth and means TUI feature parity
with the CLI is automatic.
"""
from .app import LacunaApp, run_tui

__all__ = ["LacunaApp", "run_tui"]
