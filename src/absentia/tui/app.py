"""Textual app for absentia's TUI.

Views:
  - Gaps    [1]  the default; what to fix
  - Rules   [2]  what conventions exist; for each rule, who follows + violates
  - Groups  [3]  diagnostic — every formed group + its members and rules
  - Stats   [4]  one-screen scan summary

Modals:
  - Suppress (s) — record a reason for an intentional gap
  - Filter   (/) — live-narrow the current list
  - Help     (?) — keybinding reference

Follow-link (f) cross-references between views — selected gap → its rule →
the group it's mined from. A breadcrumb above the list shows the path;
Esc walks back through it.

Watch mode (w) toggles a periodic re-scan while you edit.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from ..config import Config
from ..mining import Gap, Rule
from ..entities import Entity
from ..selectors import Group
from ..storage import StateLock, StateLockError, Storage, StorageVersionError


# Callback signature for opening a file at a line. Standalone runs use
# the default subprocess+`$EDITOR` strategy; embedded hosts (e.g. a
# Dev-Dashboard panel) inject a callback that forwards to their own
# editor surface.
OpenEditorCallback = Callable[[Path, int], None]


def editor_command(editor: str, file_path: Path, line: int) -> list[str]:
    """Build argv for opening ``file_path`` at ``line`` in ``editor``.

    Different editors use different conventions for the line jump:

    - vi / vim / nvim / nano / emacs / pico — ``editor +<line> <file>``
    - code / code-insiders / cursor       — ``editor --goto <file>:<line>``
    - subl / hx / helix / micro / atom    — ``editor <file>:<line>``
    - mate (TextMate)                     — ``editor -l <line> <file>``

    The ``editor`` argument may include extra flags (e.g.
    ``"code --wait"``); they're preserved before the file/line args.
    Falls back to the vi-family form for unknown editors, which works
    for any traditional Unix editor.
    """
    parts = editor.split() if editor else ["vi"]
    if not parts:
        parts = ["vi"]
    binary = Path(parts[0]).name
    fp = str(file_path)

    if binary in ("code", "code-insiders", "cursor", "windsurf"):
        return parts + ["--goto", f"{fp}:{line}"]
    if binary in ("subl", "sublime_text", "hx", "helix", "micro", "atom"):
        return parts + [f"{fp}:{line}"]
    if binary == "mate":
        return parts + ["-l", str(line), fp]
    # vi-family default — works for vi, vim, nvim, nano, emacs, pico, ed.
    return parts + [f"+{line}", fp]


# ── Modals ───────────────────────────────────────────────────────────


# Canonical Input-widget style for every absentia modal. Concatenated
# into each modal's DEFAULT_CSS so the field looks identical across
# SuppressScreen, FilterScreen, ExportPathInputScreen, and any future
# modal that yields an Input. Pinning the rule here keeps the design
# architecture consistent without per-modal copy-paste.
#
# width: 1fr  → claim the parent's content area exactly so the Input's
#               own border renders inside the dialog (not bleeding past
#               the padding's right edge — caught visually 2026-05-08).
# height: 3   → 1 row for content, 2 rows for top+bottom border.
# margin: 0   → defeat the default vertical margin that would shift
#               the field out of alignment with surrounding labels.
# border + background → make the textbox visibly distinct from the
#               surrounding dialog so users see typed text land
#               somewhere obvious.
_ABSENTIA_INPUT_CSS = """
    Input {
        width: 1fr;
        height: 3;
        margin: 0;
        border: solid $accent;
        background: $boost;
    }
"""


class SuppressScreen(ModalScreen[tuple[list[str], str] | None]):
    """Prompt for a suppression reason. Returns
    ``(list_of_short_ids, reason)``.

    Single-row callers pass a one-element list. Bulk callers (gaps
    view with multi-select active) pass N — the same reason gets
    applied to every short_id on the list.

    Styled to match ExportPathInputScreen — visible bordered textbox
    with a boost background so the user clearly sees their typed
    text land somewhere distinct from the surrounding labels.
    """

    DEFAULT_CSS = """
    SuppressScreen { align: center middle; }
    #dialog {
        width: 70; height: 12;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    """ + _ABSENTIA_INPUT_CSS

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, short_ids: list[str], header: str) -> None:
        super().__init__()
        self._short_ids = list(short_ids)
        self._header = header

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._header)
            yield Label("Reason  (Enter saves, Esc cancels):")
            yield Input(placeholder="Why this gap is intentional…",
                        id="reason_input")

    def on_mount(self) -> None:
        self.query_one("#reason_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        if reason:
            self.dismiss((self._short_ids, reason))

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilterScreen(ModalScreen[str | None]):
    """Prompt for a filter expression. Returns the typed string or None."""

    DEFAULT_CSS = """
    FilterScreen { align: center middle; }
    #dialog {
        width: 70; height: 9;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    """ + _ABSENTIA_INPUT_CSS

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Filter  (Enter applies, Esc cancels, empty clears)")
            yield Input(value=self._current,
                        placeholder="substring or 'kind=class' / 'conf>=0.9'…",
                        id="filter_input")

    def on_mount(self) -> None:
        self.query_one("#filter_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandPaletteScreen(ModalScreen[str | None]):
    """Fuzzy-search modal listing every TUI action.

    Opens via ``Ctrl+P``; type to narrow the list, ↑/↓ to navigate,
    Enter to dispatch the highlighted action via
    ``app.run_action(action)``. Returns the action name on Enter so
    the parent handler does the dispatch (keeps the screen-level
    code from needing the App import).

    Each entry: ``(label, description, action_name, keystroke)``.
    The keystroke is shown dim on the right so the palette doubles
    as a discoverability surface — power users learn shortcuts by
    seeing them here.
    """

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; }
    #palette_dialog {
        width: 90; height: 24;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #palette_dialog Label { margin-bottom: 1; }
    #palette_results { height: 1fr; border: solid $accent; }
    """ + _ABSENTIA_INPUT_CSS

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    # (label, description, action, keystroke). Action is the
    # binding action name (e.g. "view_gaps"); the parent app
    # routes the dismissal via run_action.
    _ENTRIES: list[tuple[str, str, str, str]] = [
        # Views
        ("View: Gaps",         "What to fix",                          "view_gaps",         "1"),
        ("View: Rules",        "Conventions absentia mined",           "view_rules",        "2"),
        ("View: Groups",       "Every formed group + members",         "view_groups",       "3"),
        ("View: Stats",        "One-screen scan summary",              "view_stats",        "4"),
        ("View: Suppressions", "Active suppressions (local + project)", "view_suppressions", "5"),
        # Triage
        ("Filter…",            "Narrow the current view",              "filter",            "/"),
        ("Sort cycle",         "Cycle sort key for current view",      "cycle_sort",        "S"),
        ("Toggle multi-select", "Mark/unmark cursor row",              "toggle_select",     "Space"),
        ("Explain",            "Why was this gap flagged?",            "explain",           "e"),
        ("Suppress",           "Suppress selected gap(s) with reason", "suppress",          "s"),
        ("Remove suppression", "Unsuppress (suppressions view only)",  "remove_suppression", "r"),
        ("Follow link",        "Drill into the related view",          "follow",            "f"),
        ("Back",               "Pop nav stack",                        "back",              "Esc"),
        ("Open in editor",     "Open selected entity in $EDITOR",      "open_in_editor",    "Enter"),
        # Lifecycle
        ("Rescan",             "Re-run the scan now",                  "rescan",            "Ctrl+R"),
        ("Watch toggle",       "Auto-rescan every 2 s",                "toggle_watch",      "w"),
        # Output / config
        ("Export",             "Export results (md/html/txt/…)",       "export",            "x"),
        ("Settings",           "Edit machine-wide settings.json",      "settings",          ","),
        ("Help",               "Show keybinding reference",            "help",              "?"),
        ("Quit",               "Exit the TUI",                         "quit",              "q"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._highlighted_idx = 0
        self._matches: list[tuple[str, str, str, str]] = list(self._ENTRIES)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette_dialog"):
            yield Label(
                "[b cyan]Command palette[/]   "
                "[dim]type to filter · ↑↓ navigate · Enter run · Esc close[/]"
            )
            yield Input(
                placeholder="Search commands…",
                id="palette_input",
            )
            yield Static("", id="palette_results")

    def on_mount(self) -> None:
        self.query_one("#palette_input", Input).focus()
        self._refresh_results()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the results list as the user types. Reset the
        highlighted index whenever the match set changes so we
        don't point past the end."""
        query = event.value.strip().lower()
        if query:
            self._matches = [
                e for e in self._ENTRIES
                if query in e[0].lower()
                or query in e[1].lower()
                or query in e[3].lower()
            ]
        else:
            self._matches = list(self._ENTRIES)
        if self._highlighted_idx >= len(self._matches):
            self._highlighted_idx = max(0, len(self._matches) - 1)
        self._refresh_results()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._matches:
            return
        action = self._matches[self._highlighted_idx][2]
        self.dismiss(action)

    def on_key(self, event) -> None:  # noqa: ANN001 — Textual Key
        """↑/↓ move the highlight while the Input retains focus."""
        if event.key == "up":
            if self._matches:
                self._highlighted_idx = max(0, self._highlighted_idx - 1)
                self._refresh_results()
            event.stop()
        elif event.key == "down":
            if self._matches:
                self._highlighted_idx = min(
                    len(self._matches) - 1, self._highlighted_idx + 1,
                )
                self._refresh_results()
            event.stop()

    def _refresh_results(self) -> None:
        if not self._matches:
            text = "[dim]No commands match.[/]"
        else:
            lines: list[str] = []
            for i, (label, desc, _action, keys) in enumerate(self._matches):
                marker = "[bright_yellow]▸[/]" if i == self._highlighted_idx else " "
                key_cell = f"[dim cyan]{keys}[/]"
                lines.append(
                    f"{marker} [b]{label:<22}[/]  [dim]{desc:<40}[/]  "
                    f"{key_cell}"
                )
            text = "\n".join(lines)
        try:
            self.query_one("#palette_results", Static).update(text)
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Show keybinding reference."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help_dialog {
        width: 64; height: 24;
        background: $surface;
        border: thick $secondary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help_dialog"):
            yield Static(
                "[b cyan]Absentia keybindings[/]\n\n"
                "[b]Views[/]\n"
                "  1  Gaps           4  Stats\n"
                "  2  Rules          5  Suppressions\n"
                "  3  Groups\n\n"
                "[b]Navigation[/]\n"
                "  j / ↓        next row\n"
                "  k / ↑        previous row\n"
                "  Enter        open in $EDITOR\n"
                "  f            follow link to related view\n"
                "  Esc          back (pops nav stack)\n\n"
                "[b]Actions[/]\n"
                "  /            filter current view\n"
                "  e            explain why selected gap was flagged\n"
                "  s            suppress selected gap\n"
                "               (also works inside the e modal —\n"
                "                no need to close it first)\n"
                "  S            cycle sort key for current view\n"
                "               (gaps: conf↓ → conf↑ → file → entity)\n"
                "  Space        toggle multi-select on the cursor\n"
                "               row (gaps + suppressions views).\n"
                "               s/r then operate on the whole set.\n"
                "  r            remove suppression(s) on view 5\n"
                "  x            export scan results (md/html/txt/\n"
                "               json/csv/sarif) to default_export_path\n"
                "  ,            open settings panel (jobs_default,\n"
                "               default_export_path, intro hint,\n"
                "               + open absentia.toml in $EDITOR)\n"
                "  Ctrl+R       rescan now\n"
                "  w            toggle watch (auto-rescan)\n\n"
                "[b]Global[/]\n"
                "  ?            this help\n"
                "  Ctrl+P       command palette (fuzzy-search\n"
                "               every TUI action by name)\n"
                "  q            quit"
            )

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


class LoadingScreen(ModalScreen[None]):
    """Per-stage scan progress, mirroring the CLI's five-stage display.

    Pushed onto the screen stack while ``scan_corpus`` runs in a
    Textual worker thread; popped when the scan finishes (success or
    failure). Shows the same ``walk → parse → store → mine →
    finalize`` story the CLI's interactive text mode shows, so a
    user who's seen one understands the other immediately.

    Stage state per row:
      ○ pending  (dim)        — not yet reached
      ◐ active   (yellow)     — currently running
      ✓ done     (green)      — finished, with summary detail

    Parse stage shows a live ``done / total files (N%)`` counter
    while running, populated by ``progress_callback`` reports from
    the worker. Other stages are too fast to need granular progress
    on real-world projects.

    Bound to ``q`` / Esc / Ctrl-C as a non-destructive escape
    hatch — quits the app cleanly without waiting for the scan to
    finish. The scan owns the lifecycle of normal completion; the
    user owns the lifecycle of "I'm done waiting."
    """

    DEFAULT_CSS = """
    LoadingScreen { align: center middle; }
    #loading_dialog {
        width: 76; height: 14;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #loading_title { margin-bottom: 1; }
    """

    # Escape hatch: a long scan (kernel-scale, slow disk, etc.)
    # used to leave the user stuck on this screen with no way out
    # short of Ctrl-C from the host shell. q / Esc / ctrl+c now
    # quit the app cleanly. The scan worker is daemon-flagged so
    # it dies with the process; any in-flight Storage operations
    # roll back via the `with Storage(...)` context, so there's no
    # half-committed state to worry about. We don't try to
    # gracefully cancel scan_corpus mid-stage — it doesn't have an
    # interruption protocol, and the user's intent here is "I'm
    # leaving," not "pause without quitting."
    BINDINGS = [
        Binding("q", "stop_and_quit", "Quit"),
        Binding("escape", "stop_and_quit", "Quit"),
        Binding("ctrl+c", "stop_and_quit", "Quit"),
    ]

    def action_stop_and_quit(self) -> None:
        """Quit the app and guarantee process termination.

        ``app.exit()`` alone schedules the Textual event loop to
        wind down, but if the scan worker is mid-stage (especially
        deep inside scan_corpus's tree-sitter calls), the daemon
        thread can keep the process alive in the background — the
        user gets their shell prompt back without the process
        actually exiting, leaving an orphan that holds the SQLite
        WAL until the kernel reaps it.

        Belt-and-suspenders: schedule a hard ``os._exit(0)`` after a
        short grace period so the process really dies even if
        Textual's teardown gets stuck. flock-based StateLock is
        kernel-released, so the lockfile is freed regardless;
        SQLite's WAL recovers on next open. The "no partial state
        saved" hint in the loading dialog already sets the
        expectation that this is a clean abort, not a graceful
        cancel.
        """
        import os
        import threading

        self.app.exit()
        # 500 ms gives Textual's normal teardown a chance to win the
        # race; if it doesn't, force-exit. Daemon timer dies with
        # the process (no leak if Textual exits cleanly first).
        threading.Timer(0.5, lambda: os._exit(0)).start()

    _STAGES = ("walk", "parse", "store", "mine", "finalize")
    _STAGE_LABELS = {
        "walk":     "Walking corpus",
        "parse":    "Scanning files",
        "store":    "Loading store",
        "mine":     "Mining rules",
        "finalize": "Finalizing",
    }
    # Same braille spinner frames the CLI's progress.Spinner uses.
    # Keeping them aligned is deliberate: a user who's seen one
    # recognizes the other immediately, and anything more
    # decorative would just be a second pattern to memorize.
    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _SPINNER_INTERVAL_S = 0.08

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._states: dict[str, str] = {s: "pending" for s in self._STAGES}
        self._details: dict[str, str] = {s: "" for s in self._STAGES}
        self._parse_done = 0
        self._parse_total = 0
        self._frame_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading_dialog"):
            yield Label(
                f"absentia · scanning [b cyan]{self._root.name}[/]",
                id="loading_title",
            )
            yield Static("", id="stage_list")
            yield Static(
                "[dim]q / Esc to quit (no partial state saved)[/]",
                id="loading_hint",
            )

    def on_mount(self) -> None:
        self._refresh()
        # Drive the spinner — same cadence the CLI uses (80 ms/frame
        # ≈ 12.5 Hz). Textual's set_interval returns a Timer that
        # auto-stops when the screen is dismissed, so no explicit
        # cleanup needed.
        self.set_interval(self._SPINNER_INTERVAL_S, self._tick_spinner)

    def _tick_spinner(self) -> None:
        # Only refresh when at least one stage is active; while
        # everything is pending or all are done there's nothing to
        # animate and a refresh would just churn the widget.
        if any(s == "active" for s in self._states.values()):
            self._frame_idx = (
                self._frame_idx + 1
            ) % len(self._SPINNER_FRAMES)
            self._refresh()

    def update_stage(
        self, stage: str, event: str, **details: object,
    ) -> None:
        """Apply a stage transition reported by the worker thread.

        Always called via ``app.call_from_thread`` so it's safe to
        touch Textual widget state. ``details`` carries the kwargs
        passed by ``scan_corpus``'s ``stage_callback``.
        """
        if stage not in self._states:
            return
        if event == "started":
            self._states[stage] = "active"
            if stage == "parse":
                total = details.get("total", 0)
                if isinstance(total, int):
                    self._parse_total = total
        elif event == "finished":
            self._states[stage] = "done"
            self._details[stage] = self._format_summary(details)
        self._refresh()

    def update_parse_progress(self, done: int, total: int) -> None:
        """Drive the live ``done/total`` counter on the parse row."""
        self._parse_done = done
        self._parse_total = total
        if self._states["parse"] == "active":
            self._refresh()

    def _format_summary(self, details: dict) -> str:
        from ..progress import _format_time
        parts: list[str] = []
        if "files" in details:
            parts.append(f"{details['files']:,d} files")
        if "entities" in details:
            parts.append(f"{details['entities']:,d} entities")
        if "rules" in details:
            parts.append(f"{details['rules']:,d} rules")
        if "gaps" in details and "rules" not in details:
            parts.append(f"{details['gaps']:,d} gaps")
        if "duration_ms" in details:
            secs = float(details["duration_ms"]) / 1000
            parts.append(_format_time(secs))
        return " · ".join(parts)

    def _refresh(self) -> None:
        # Active stage uses the rolling braille spinner frame in cyan
        # — same color the CLI's progress.Spinner uses, so the
        # animation reads identically across surfaces. Pending is
        # dim; done is the green ✓ everyone recognizes.
        active_glyph = (
            f"[cyan]{self._SPINNER_FRAMES[self._frame_idx]}[/]"
        )
        lines: list[str] = []
        for stage in self._STAGES:
            label = self._STAGE_LABELS[stage]
            state = self._states[stage]
            if state == "pending":
                glyph = "[dim]·[/]"
            elif state == "active":
                glyph = active_glyph
            else:  # done
                glyph = "[bright_green]✓[/]"

            row = f" {glyph}  [b]{label}[/]"
            if (
                state == "active" and stage == "parse"
                and self._parse_total > 0
            ):
                pct = int(100 * self._parse_done / self._parse_total)
                row += (
                    f"  [dim]{self._parse_done:,d}/"
                    f"{self._parse_total:,d} files ({pct}%)[/]"
                )
            elif state == "done" and self._details[stage]:
                row += f"  [dim]{self._details[stage]}[/]"
            lines.append(row)
        try:
            self.query_one("#stage_list", Static).update("\n".join(lines))
        except Exception:
            # Screen has been popped; widget query will raise. Safe
            # to ignore — late callbacks just no-op.
            pass


# Per-language palette mapped to rich color names for use inside
# Textual widgets (the engine's _color._LANG_COLORS uses raw ANSI
# escapes for the stderr progress UI; not portable to rich markup).
# Keep the language→hue assignments in sync with _color.py so the
# CLI parse bar's per-worker colors match the TUI table's per-row
# colors for the same file.
_TUI_LANG_COLORS: dict[str, str] = {
    "python":     "bright_blue",
    "javascript": "bright_yellow",
    "typescript": "blue",
    "tsx":        "blue",
    "rust":       "bright_red",
    "go":         "bright_cyan",
    "java":       "red",
    "ruby":       "red",
    "csharp":     "bright_magenta",
    "c":          "bright_white",
    "cpp":        "bright_white",
    "php":        "magenta",
    "kotlin":     "magenta",
    "scala":      "bright_magenta",
    "lua":        "blue",
    "bash":       "green",
    "swift":      "bright_yellow",
}

# File extension → language tag. Mirrors the extractor entry-point
# group's coverage; unknown suffixes fall back to no special color
# (cyan via TableColumn default). Kept here rather than imported
# from extractors/ so the TUI doesn't pay an extractor-discovery
# tax just to color a file path.
_EXT_TO_LANG: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript", ".mjs":  "javascript", ".cjs":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".rs":   "rust",
    ".go":   "go",
    ".java": "java",
    ".rb":   "ruby",
    ".cs":   "csharp",
    ".c":    "c", ".h": "c",
    ".cpp":  "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".php":  "php",
    ".kt":   "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".lua":  "lua",
    ".sh":   "bash", ".bash": "bash",
    ".swift": "swift",
}


def _lang_color_for_path(file_path: str) -> str:
    """Pick a rich color name for a file path's extension. Returns
    ``"cyan"`` (the table-default location style) for unknown."""
    from os.path import splitext
    ext = splitext(file_path)[1].lower()
    lang = _EXT_TO_LANG.get(ext)
    if lang is None:
        return "cyan"
    return _TUI_LANG_COLORS.get(lang, "cyan")


def _confidence_glyph_style(confidence: float) -> str:
    """Severity dot color matching output._confidence_style.

    bright_green ≥ 0.95, green ≥ 0.80, yellow below.
    """
    if confidence >= 0.95:
        return "bright_green"
    if confidence >= 0.80:
        return "green"
    return "yellow"


def _highlight_match(content: str, query: str) -> str:
    """Wrap case-insensitive matches of ``query`` in ``content`` with
    ``[reverse yellow]…[/]`` markup so the substring that matched the
    filter is visible at a glance.

    Empty query returns content unchanged. Preserves the original
    casing of the matched substring; only the case of the query is
    folded for the comparison.
    """
    if not query:
        return content
    lower = content.lower()
    q = query.lower()
    out: list[str] = []
    i = 0
    while i < len(content):
        idx = lower.find(q, i)
        if idx < 0:
            out.append(content[i:])
            break
        out.append(content[i:idx])
        out.append(f"[reverse yellow]{content[idx:idx + len(q)]}[/]")
        i = idx + len(q)
    return "".join(out)


class ExportFormatScreen(ModalScreen[int | None]):
    """Pick a post-scan export format from inside the TUI.

    Returns the menu_id (1–6) for the chosen format, or ``None`` on
    Esc / cancel. The parent app translates the id into a renderer
    via ``export._FORMATS`` and writes the result to disk under the
    saved ``default_export_path``.

    Number keys map directly to formats so power users can fire and
    forget — ``x → 1`` writes Markdown, ``x → 4`` writes JSON, etc.
    """

    DEFAULT_CSS = """
    ExportFormatScreen { align: center middle; }
    #export_dialog {
        width: 50; height: 14;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("1", "pick_1", "Markdown"),
        Binding("2", "pick_2", "HTML"),
        Binding("3", "pick_3", "Text"),
        Binding("4", "pick_4", "JSON"),
        Binding("5", "pick_5", "CSV"),
        Binding("6", "pick_6", "SARIF"),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="export_dialog"):
            yield Label("[b]Export scan results[/]")
            yield Static(
                "\n"
                "  [b]1[/]) Markdown   [dim](.md)[/]\n"
                "  [b]2[/]) HTML       [dim](.html)[/]\n"
                "  [b]3[/]) Text       [dim](.txt)[/]\n"
                "  [b]4[/]) JSON       [dim](.json)[/]\n"
                "  [b]5[/]) CSV        [dim](.csv)[/]\n"
                "  [b]6[/]) SARIF      [dim](.sarif.json)[/]\n\n"
                "  [dim]Esc / q to cancel[/]"
            )

    def action_pick_1(self) -> None: self.dismiss(1)
    def action_pick_2(self) -> None: self.dismiss(2)
    def action_pick_3(self) -> None: self.dismiss(3)
    def action_pick_4(self) -> None: self.dismiss(4)
    def action_pick_5(self) -> None: self.dismiss(5)
    def action_pick_6(self) -> None: self.dismiss(6)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportLocationScreen(ModalScreen[str | None]):
    """Pick custom-path vs default-path for the TUI export flow.

    Returns ``"custom"``, ``"default"``, or ``None`` on Esc/q.
    Mirrors the CLI's post-export Location prompt so the
    behavior + keystrokes transfer between surfaces.
    """

    DEFAULT_CSS = """
    ExportLocationScreen { align: center middle; }
    #export_loc_dialog {
        width: 50; height: 9;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("1", "pick_custom", "Custom"),
        Binding("2", "pick_default", "Default"),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="export_loc_dialog"):
            yield Label("[b]Export location[/]")
            yield Static(
                "\n  [b]1[/]) Custom path\n"
                "  [b]2[/]) Default path  [dim](from settings)[/]\n\n"
                "  [dim]Esc / q to cancel[/]"
            )

    def action_pick_custom(self) -> None: self.dismiss("custom")
    def action_pick_default(self) -> None: self.dismiss("default")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportPathInputScreen(ModalScreen[str | None]):
    """Text-input modal for typing an export base path.

    Used in two contexts — collecting a one-off custom path, or
    setting the default for the first time. The ``prompt`` arg
    distinguishes the two so the user knows which one they're
    answering. Returns the typed string (caller resolves ~ and
    relative paths) or ``None`` on Esc / empty submit.
    """

    DEFAULT_CSS = """
    ExportPathInputScreen { align: center middle; }
    #export_path_dialog {
        width: 80; height: 12;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #export_path_dialog Label { margin-bottom: 1; }
    """ + _ABSENTIA_INPUT_CSS

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="export_path_dialog"):
            yield Label(self._prompt)
            yield Label(
                "[dim]Enter saves · Esc cancels · ~/ and absolute "
                "paths supported[/]"
            )
            yield Input(
                value=self._initial,
                placeholder="e.g. ~/exports or /tmp/absentia",
                id="path_input",
            )

    def on_mount(self) -> None:
        self.query_one("#path_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsIntInputScreen(ModalScreen[str | None]):
    """Generic int-input modal used by SettingsScreen for jobs_default.

    Returns the typed string verbatim (caller validates / parses)
    or ``None`` on Esc / empty submit. Same canonical input
    styling as every other absentia modal.
    """

    DEFAULT_CSS = """
    SettingsIntInputScreen { align: center middle; }
    #settings_int_dialog {
        width: 60; height: 12;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #settings_int_dialog Label { margin-bottom: 1; }
    """ + _ABSENTIA_INPUT_CSS

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_int_dialog"):
            yield Label(self._prompt)
            yield Label(
                "[dim]Positive integer · Enter saves · "
                "Esc cancels · empty/0 = auto[/]"
            )
            yield Input(
                value=self._initial,
                placeholder="e.g. 4",
                id="settings_int_input",
            )

    def on_mount(self) -> None:
        self.query_one("#settings_int_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[None]):
    """Edit machine-wide ``~/.absentia/settings.json`` from the TUI.

    Three editable fields plus a hand-off to the user's editor for
    the per-project ``absentia.toml`` (which is too structured to
    edit cleanly in a TUI form — the user gets full power via
    their preferred editor):

      1) jobs_default          (int | None)
      2) default_export_path   (str | None)
      3) info_hint_shown_at    (str | None) — reset to None to
                               see the first-run hint again

      e) Open this project's absentia.toml in $EDITOR

    Number keys map directly to the action-per-field convention
    used by ExportFormatScreen / ExportLocationScreen so the TUI's
    modal UX stays consistent.
    """

    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    #settings_dialog {
        width: 86; height: 22;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("1", "edit_jobs", "Edit jobs"),
        Binding("2", "edit_path", "Edit export path"),
        Binding("3", "reset_hint", "Reset intro hint"),
        Binding("e", "open_toml", "Open absentia.toml"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        with Vertical(id="settings_dialog"):
            yield Static(self._render_state(), id="settings_body")

    def on_mount(self) -> None:
        self._refresh()

    def _render_state(self) -> str:
        from ..settings import load_settings, settings_path
        s = load_settings()
        jobs = (
            "[dim]auto (cpu/2)[/]"
            if s.jobs_default is None
            else f"[cyan]{s.jobs_default}[/]"
        )
        path = (
            f"[cyan]{s.default_export_path}[/]"
            if s.default_export_path else "[dim](unset)[/]"
        )
        hint = (
            f"[dim]{s.info_hint_shown_at}[/]"
            if s.info_hint_shown_at else "[dim](never shown)[/]"
        )
        toml_path = self._root / "absentia.toml"
        toml_status = (
            "[cyan]exists[/]" if toml_path.exists()
            else "[yellow](no absentia.toml — run `absentia init`)[/]"
        )
        return (
            "[b cyan]absentia settings[/]\n"
            f"[dim]{settings_path()}[/]\n\n"
            f"[b]1[/]) Default workers       {jobs}\n"
            f"     [dim]Pin --jobs default for `absentia check`. "
            f"Empty / 0 reverts to auto.[/]\n\n"
            f"[b]2[/]) Default export path   {path}\n"
            f"     [dim]Base dir for `x`-key + CLI export prompt. "
            f"~/ supported.[/]\n\n"
            f"[b]3[/]) Intro hint shown at   {hint}\n"
            f"     [dim]Press 3 to reset; the next launch will "
            f"show the hint again.[/]\n\n"
            f"[b]e[/]) Open this project's [cyan]absentia.toml[/] "
            f"in [b]$EDITOR[/]   {toml_status}\n\n"
            "[dim]Esc / q to close[/]"
        )

    def _refresh(self) -> None:
        try:
            self.query_one(
                "#settings_body", Static,
            ).update(self._render_state())
        except Exception:
            pass

    def action_edit_jobs(self) -> None:
        from ..settings import load_settings
        s = load_settings()
        initial = "" if s.jobs_default is None else str(s.jobs_default)
        self.app.push_screen(
            SettingsIntInputScreen(
                prompt="Default worker count:",
                initial=initial,
            ),
            self._on_jobs_edited,
        )

    def _on_jobs_edited(self, raw: str | None) -> None:
        if raw is None:
            return
        # Empty or zero → revert to None ("auto"); otherwise must be
        # a positive integer.
        if not raw or raw == "0":
            value: int | None = None
        else:
            try:
                parsed = int(raw)
            except ValueError:
                self.app.notify(
                    f"Invalid integer: {raw!r}",
                    severity="warning", timeout=6,
                )
                return
            if parsed < 1:
                value = None
            else:
                value = parsed
        from dataclasses import replace
        from ..settings import load_settings, save_settings
        s = load_settings()
        try:
            save_settings(replace(s, jobs_default=value))
        except OSError as exc:
            self.app.notify(
                f"Couldn't save settings: {exc}",
                severity="error", timeout=8,
            )
            return
        self._refresh()
        label = "auto" if value is None else str(value)
        self.app.notify(f"jobs_default = {label}", timeout=4)

    def action_edit_path(self) -> None:
        from ..settings import load_settings
        s = load_settings()
        initial = s.default_export_path or ""
        self.app.push_screen(
            ExportPathInputScreen(
                prompt="Default export base path:",
                initial=initial,
            ),
            self._on_path_edited,
        )

    def _on_path_edited(self, raw: str | None) -> None:
        if raw is None or not raw:
            return
        try:
            base = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            self.app.notify(
                f"Invalid path: {exc}",
                severity="warning", timeout=8,
            )
            return
        from dataclasses import replace
        from ..settings import load_settings, save_settings
        s = load_settings()
        try:
            save_settings(replace(s, default_export_path=str(base)))
        except OSError as exc:
            self.app.notify(
                f"Couldn't save settings: {exc}",
                severity="error", timeout=8,
            )
            return
        self._refresh()
        self.app.notify(f"default_export_path = {base}", timeout=4)

    def action_reset_hint(self) -> None:
        from dataclasses import replace
        from ..settings import load_settings, save_settings
        s = load_settings()
        try:
            save_settings(replace(s, info_hint_shown_at=None))
        except OSError as exc:
            self.app.notify(
                f"Couldn't reset hint: {exc}",
                severity="error", timeout=8,
            )
            return
        self._refresh()
        self.app.notify(
            "Intro hint will fire on next launch.", timeout=4,
        )

    def action_open_toml(self) -> None:
        toml_path = self._root / "absentia.toml"
        if not toml_path.exists():
            self.app.notify(
                f"No absentia.toml in {self._root.name}. "
                "Run `absentia init` first.",
                severity="warning", timeout=8,
            )
            return
        # Dismiss this modal first so $EDITOR has a clean terminal.
        # The parent app's _open_in_editor handles the
        # suspend / subprocess / FileNotFoundError dance.
        self.dismiss()
        # mypy: self.app is App, but only AbsentiaApp has the helper.
        opener = getattr(self.app, "_open_in_editor", None)
        if opener is not None:
            opener(toml_path, 1)

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


class ExplainScreen(ModalScreen[str | None]):
    """Plain-text "why was this flagged?" modal for a gap.

    Different from the f / follow action: follow navigates to the
    rule view (you change context); explain pops a peek that shows
    the rule sentence, support, conformers, and divergence — then
    returns you to your spot in the gaps list when dismissed.

    Returns:
      ``"suppress"``  — user pressed ``s`` to chain into the
                        suppression flow for the same gap.
      ``None``        — closed without action.
    """

    DEFAULT_CSS = """
    ExplainScreen { align: center middle; }
    #explain_dialog {
        width: 86; height: 24;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("e", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("s", "request_suppress", "Suppress"),
    ]

    def __init__(
        self,
        gap: Gap,
        rule: Rule,
        entity: Entity,
        group: Group | None,
        feature_index: dict,
        min_confidence: float,
    ) -> None:
        super().__init__()
        self._gap = gap
        self._rule = rule
        self._entity = entity
        self._group = group
        self._feature_index = feature_index
        self._min_confidence = min_confidence

    def compose(self) -> ComposeResult:
        with Vertical(id="explain_dialog"):
            yield Static(self._render_text())

    def _render_text(self) -> str:
        rule = self._rule
        entity = self._entity
        short = entity.qualified_name.rsplit("::", 1)[-1]

        conformers: list[str] = []
        if self._group is not None:
            for mid in self._group.members:
                if mid == entity.qualified_name:
                    continue
                fset = self._feature_index.get(mid)
                if fset is None:
                    continue
                if rule.feature_value in fset.get_set(rule.feature_kind):
                    conformers.append(mid.rsplit("::", 1)[-1])

        if conformers:
            shown = ", ".join(conformers[:8])
            if len(conformers) > 8:
                shown += f", … (+{len(conformers) - 8} more)"
            conformer_block = shown
        else:
            conformer_block = "[dim](no other conformers visible)[/]"

        return (
            f"[b cyan]Why flagged[/]   [b]{short}[/]   "
            f"[dim]({entity.kind})[/]\n"
            f"               {entity.file_path}:{entity.line}\n\n"
            f"[b]Rule[/]      {rule.support_n} of {rule.support_total} "
            f"members of [yellow]{rule.group_id}[/]\n"
            f"          have [b]{rule.feature_kind}[/] = "
            f"[cyan]{rule.feature_value}[/]\n"
            f"          confidence [b]{rule.confidence:.2f}[/] "
            f"(threshold {self._min_confidence:.2f})\n\n"
            f"[b]Conforms[/]  {conformer_block}\n\n"
            f"[b]Diverges[/]  [b red]{short}[/]   ← this gap\n\n"
            "[dim]Most members of this group exhibit the pattern; this\n"
            "one doesn't. To follow the convention, edit the file. To\n"
            "accept the divergence, press [/][b]s[/][dim] right here\n"
            "to record a suppression reason without closing first.[/]\n\n"
            "[dim]s to suppress · e / Esc to close[/]"
        )

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)

    def action_request_suppress(self) -> None:
        """Hand off to the parent's suppress flow for this same gap.

        The dismiss-with-sentinel pattern keeps Storage I/O out of
        the modal — the parent app already owns the StateLock + the
        rescan-after-suppress logic, so funnelling through the
        existing handler keeps both code paths in sync.
        """
        self.dismiss("suppress")


# ── Main App ─────────────────────────────────────────────────────────


_VIEW_LABELS = {
    "gaps":         "Gaps",
    "rules":        "Rules",
    "groups":       "Groups",
    "stats":        "Stats",
    "suppressions": "Suppressions",
}


def _load_project_suppressions(root: Path) -> list[dict]:
    """TUI-side adapter over ``_suppressions.load_project_suppressions``.

    Adds the ``"source": "project"`` discriminator the
    Suppressions view needs to distinguish read-only project rows
    from local-DB ones. The underlying schema + parsing is now
    shared with ``scan_corpus``'s engine-level enforcement so both
    surfaces see the same set of project entries from the same
    ``absentia.toml`` parse path.
    """
    from .._suppressions import load_project_suppressions
    return [
        {**entry, "source": "project"}
        for entry in load_project_suppressions(root)
    ]


class AbsentiaApp(App[None]):
    """The main  absentia TUI."""

    DEFAULT_CSS = """
    Screen { background: $surface; }
    #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    /* Layout: table flexes, detail is fixed-small, preview is a
       fixed bottom pane showing code context around the selected
       gap. height: 1fr on the DataTable means "take whatever's
       left after the fixed-height widgets claim theirs"; detail
       and preview stay readable on tall terminals (each row of
       the table is more visible) and survive small terminals (the
       fixed budget is small). */
    DataTable { height: 1fr; min-height: 8; }
    #detail {
        height: 6;
        padding: 0 2;
        border: solid $accent;
    }
    #preview {
        height: 12;
        padding: 0 2;
        border: solid $accent;
        background: $boost;
    }
    #stats_text {
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+r", "rescan", "Rescan"),
        Binding("1", "view_gaps", "Gaps"),
        Binding("2", "view_rules", "Rules"),
        Binding("3", "view_groups", "Groups"),
        Binding("4", "view_stats", "Stats"),
        Binding("5", "view_suppressions", "Suppressions"),
        Binding("space", "toggle_select", "Select"),
        Binding("r", "remove_suppression", "Remove suppression"),
        Binding("s", "suppress", "Suppress"),
        Binding("S", "cycle_sort", "Sort"),
        Binding("e", "explain", "Explain"),
        Binding("x", "export", "Export"),
        Binding("comma", "settings", "Settings"),
        Binding("f", "follow", "Follow"),
        Binding("escape", "back", "Back"),
        Binding("slash", "filter", "Filter"),
        Binding("w", "toggle_watch", "Watch"),
        Binding("question_mark", "help", "Help"),
        Binding("enter", "open_in_editor", "Open"),
        Binding("ctrl+p", "command_palette", "Command palette"),
    ]

    def __init__(
        self,
        root: Path,
        config: Config,
        on_open_editor: OpenEditorCallback | None = None,
        jobs: int | None = None,
    ) -> None:
        super().__init__()
        self.root = root
        self.config = config
        self._on_open_editor = on_open_editor
        # User-requested worker count for TUI scans (top-level
        # `--jobs N`). None → fall back to 1, the safe default for
        # macOS spawn-mode multiprocessing under Textual's event
        # loop. See the comment in _do_scan.
        self._jobs: int = jobs if jobs is not None and jobs > 0 else 1
        self._gaps: list[Gap] = []
        self._rules: list[Rule] = []
        self._rules_by_id: dict[str, Rule] = {}
        self._groups: list[Group] = []
        self._groups_by_id: dict[str, Group] = {}
        self._entities: dict[str, Entity] = {}
        self._feature_index: dict = {}
        self._scan_stats: dict = {}
        self._view: str = "gaps"
        self._filter: dict[str, str] = {
            "gaps": "", "rules": "", "groups": "", "suppressions": "",
        }
        self._nav_stack: list[tuple[str, str]] = []
        # Multi-select state — per view, set of row keys (short_id
        # for gaps / suppressions). `space` toggles. `s` (gaps view)
        # / `r` (suppressions view) operate on the selection if
        # non-empty, otherwise on the cursor row. Cleared on view
        # switch or rescan to avoid stale references.
        self._selected: dict[str, set[str]] = {
            "gaps": set(), "suppressions": set(),
        }
        # Cached project-wide suppressions parsed from absentia.toml.
        # Read once per scan; refreshed when the user re-runs after
        # editing the TOML in $EDITOR.
        self._project_suppressions: list[dict] = []
        # Cached snapshot of state.db local suppressions for the
        # Suppressions view. Same lifecycle as scan results — written
        # by _scan_done and read by the suppressions render path.
        self._local_suppressions: dict[str, dict] = {}
        from textual.timer import Timer
        self._watch_timer: Timer | None = None
        # Stashes the (menu_id, name, ext, fn_name) tuple while the
        # x-export flow walks through Format → Location → Path
        # modals; cleared when the chain finishes (success, cancel,
        # or write failure).
        self._pending_export_fmt: tuple[int, str, str, str] | None = None
        # Per-view sort key cycled by capital `S`. Default ordering
        # for each view matches what the engine returns naturally
        # so cold first-impressions don't change unless the user
        # opts in. The order list per view is the cycle that S
        # walks through.
        self._sort_keys: dict[str, str] = {
            "gaps":   "conf_desc",
            "rules":  "conf_desc",
            "groups": "members_desc",
        }
        self._sort_cycles: dict[str, list[str]] = {
            "gaps":   ["conf_desc", "conf_asc", "file", "entity"],
            "rules":  ["conf_desc", "support_desc", "group"],
            "groups": ["members_desc", "members_asc", "name"],
        }
        self._sort_labels: dict[str, str] = {
            "conf_desc":    "conf↓",
            "conf_asc":     "conf↑",
            "file":         "file",
            "entity":       "entity",
            "support_desc": "support↓",
            "group":        "group",
            "members_desc": "members↓",
            "members_asc":  "members↑",
            "name":         "name",
        }

    # ── Layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="breadcrumb")
        with ContentSwitcher(id="switcher", initial="table_pane"):
            with Vertical(id="table_pane"):
                yield DataTable(
                    id="main_table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
                yield Static("(loading…)", id="detail")
                # Bottom context pane — code lines surrounding the
                # currently-selected gap. Header line shows
                # ``file_path | line N-M`` so the user knows where
                # they are without checking the gap row. Updated on
                # every cursor-row change via
                # on_data_table_row_highlighted.
                yield Static(
                    "[dim](select a gap to preview)[/]",
                    id="preview",
                )
            with Vertical(id="stats_pane"):
                yield Static("(loading…)", id="stats_text")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"absentia — {self.root.name}"
        # Show a quick estimate in the subtitle so the user has a sense
        # of how long the cold scan will take. The actual scan happens
        # one tick later via call_after_refresh — that lets Textual
        # paint the estimate first, then run the (blocking) scan.
        self._set_estimate_subtitle()
        self.call_after_refresh(self._do_scan)

    def _set_estimate_subtitle(self) -> None:
        """Compute and display 'estimating ~Xs · scanning…' as a
        transient subtitle. Replaced by `_update_subtitle` once the
        real scan finishes. Failures fall through silently — the
        estimator must never block the TUI.
        """
        try:
            from ..estimator import (
                _format_seconds, estimate, walk_corpus,
            )
            from ..extractors import discover_extractors, extension_dispatch
            from ..parallel import default_jobs

            extractors = discover_extractors(self.config.scan.languages)
            ext_to = extension_dispatch(extractors)
            shape = walk_corpus(self.root, ext_to)
            if shape.files == 0:
                self.sub_title = "scanning…"
                return

            from ..calibration import calibrated_bps_table, load_calibration
            from ..estimator import PARALLEL_FRACTION
            cal = load_calibration()
            bps_table = (
                calibrated_bps_table(
                    cal.machine_speed_factor, cal.per_language_bps,
                )
                if cal else None
            )
            p = cal.amdahl_p if cal else PARALLEL_FRACTION
            est = estimate(
                by_language_bytes=shape.by_language_bytes,
                jobs=default_jobs(),
                bps_table=bps_table,
                parallel_fraction=p,
            )
            note = "" if cal else " (uncalibrated)"
            self.sub_title = (
                f"estimating ~{_format_seconds(est.parallel_time_s)}"
                f"{note} · scanning…"
            )
        except Exception:
            self.sub_title = "scanning…"

    # ── Scan ──────────────────────────────────────────────────────────

    def _do_scan(self) -> None:
        """Push the loading screen + run the scan in a worker thread.

        Off-main-thread is required for two reasons:
          1. The Textual event loop must keep painting (the loading
             panel updates, the user can quit mid-scan, etc.).
          2. ``scan_corpus`` is blocking on parse + mine and would
             freeze the UI for the duration if run inline.

        Stage and per-file progress are reported from the worker via
        ``app.call_from_thread`` so widget state is mutated only on
        the main thread (Textual's threading contract).
        """
        loading = LoadingScreen(self.root)
        self.push_screen(loading)

        def _worker() -> None:
            from ..cli import scan_corpus

            # Closure state for the parse-progress accumulator. The
            # engine's progress_callback reports per-file increments
            # (not (done, total) — its actual signature is
            # ``(count_increment, item=None)``), so we sum here and
            # forward the cumulative done + the total captured on
            # the parse-stage start event.
            parse_state = {"done": 0, "total": 0}

            def stage_cb(stage: str, event: str, **details: object) -> None:
                if stage == "parse" and event == "started":
                    parse_state["done"] = 0
                    total = details.get("total", 0)
                    if isinstance(total, int):
                        parse_state["total"] = total
                # Trampoline back onto the main thread so the
                # widget update is safe.
                self.call_from_thread(
                    loading.update_stage, stage, event, **details,
                )

            def parse_cb(increment: int, item: object = None) -> None:
                parse_state["done"] += int(increment)
                self.call_from_thread(
                    loading.update_parse_progress,
                    parse_state["done"], parse_state["total"],
                )

            # Default jobs=1 inside the TUI: spawn-mode
            # ProcessPoolExecutor (the macOS multiprocessing default)
            # doesn't play nicely with Textual's running event loop —
            # the spawn child's fd validation surfaces as `bad value(s)
            # in fds_to_keep` on any non-trivial corpus. Single-process
            # scans avoid the issue. Users who know their platform
            # handles process spawn cleanly under Textual can opt into
            # parallelism via top-level `--jobs N`; the CLI path
            # (`absentia check`) gets full parallelism unconditionally.
            try:
                result = scan_corpus(
                    root=self.root,
                    state_dir=self.root / ".absentia",
                    config=self.config,
                    jobs=self._jobs,
                    stage_callback=stage_cb,
                    progress_callback=parse_cb,
                )
            except Exception as exc:
                self.call_from_thread(self._scan_failed, exc)
                return
            self.call_from_thread(self._scan_done, result)

        self.run_worker(_worker, thread=True, exclusive=True)

    def _scan_done(self, result: dict) -> None:
        """Main-thread callback when the worker finishes successfully."""
        self._gaps = result["gaps"]
        self._rules_by_id = result["rules_by_id"]
        self._rules = sorted(result["rules"], key=lambda r: -r.confidence)
        self._groups = result["groups"]
        self._groups_by_id = {g.id: g for g in self._groups}
        self._entities = result["entities"]
        self._feature_index = result["feature_index"]
        self._scan_stats = result["scan_stats"]
        # Refresh the suppressions caches for the Suppressions view.
        # Local: read straight from the state DB. Project: re-parse
        # absentia.toml (the user may have edited it between scans
        # via the Settings panel's $EDITOR handoff).
        self._refresh_suppressions_cache()
        # Stale multi-select sets across views need clearing — the
        # gap a user marked may have been suppressed mid-scan, the
        # suppression they marked may have been removed elsewhere.
        for view_sel in self._selected.values():
            view_sel.clear()
        # Pop the loading screen IF it's still on top — a user who
        # navigated into a modal during the scan would have stacked
        # on top of it, in which case the LoadingScreen is buried
        # and we leave the stack alone. (Edge case; in practice the
        # user can't reach a modal while LoadingScreen is up because
        # LoadingScreen has no dismiss bindings.)
        if isinstance(self.screen, LoadingScreen):
            self.pop_screen()
        self._render_current_view()
        self._update_subtitle()

    def _refresh_suppressions_cache(self) -> None:
        """Re-pull local + project suppressions. Called after each
        scan and after every add / remove from the TUI."""
        try:
            with Storage(self.root / ".absentia") as storage:
                self._local_suppressions = storage.load_suppressions()
        except (StorageVersionError, StateLockError):
            self._local_suppressions = {}
        self._project_suppressions = _load_project_suppressions(self.root)

    def _scan_failed(self, exc: BaseException) -> None:
        """Main-thread callback when the worker raises."""
        if isinstance(self.screen, LoadingScreen):
            self.pop_screen()
        self.notify(f"Scan failed: {exc}", severity="error", timeout=8)

    # ── Subtitle / breadcrumb ────────────────────────────────────────

    def _update_subtitle(self) -> None:
        s = self._scan_stats
        suppressed = s.get("suppressed", 0)
        sup = f" · {suppressed} suppressed" if suppressed else ""
        watch = " · ●watching" if self._watch_timer else ""
        # Surface the current sort key so the user sees what they
        # cycled into when capital-S is pressed. Stats view has no
        # sortable list, so the indicator is suppressed there.
        sort_key = self._sort_keys.get(self._view)
        sort_note = (
            f" · sort: {self._sort_labels.get(sort_key, sort_key)}"
            if sort_key and self._view != "stats" else ""
        )
        self.sub_title = (
            f"[{_VIEW_LABELS[self._view]}] · "
            f"{s.get('entities_scanned', 0)} entities · "
            f"{s.get('rules', 0)} rules · "
            f"{len(self._gaps)} gaps{sup}{sort_note}{watch}"
        )

    def _update_breadcrumb(self) -> None:
        crumbs = []
        for view, item_id in self._nav_stack:
            crumbs.append(f"{_VIEW_LABELS[view]}({item_id})")
        crumbs.append(_VIEW_LABELS[self._view])
        flt = self._filter.get(self._view, "")
        suffix = f"  /[yellow]{flt}[/]" if flt else ""
        self.query_one("#breadcrumb", Static).update(
            "  ▸ ".join(crumbs) + suffix
        )

    # ── Render dispatch ───────────────────────────────────────────────

    def _render_current_view(self) -> None:
        switcher = self.query_one("#switcher", ContentSwitcher)
        if self._view == "stats":
            switcher.current = "stats_pane"
            self._render_stats()
        else:
            switcher.current = "table_pane"
            self._render_table()
        self._update_breadcrumb()
        self._update_subtitle()

    def _render_table(self) -> None:
        table = self.query_one("#main_table", DataTable)
        table.clear(columns=True)
        if self._view == "gaps":
            self._render_gaps_table(table)
        elif self._view == "rules":
            self._render_rules_table(table)
        elif self._view == "groups":
            self._render_groups_table(table)
        elif self._view == "suppressions":
            self._render_suppressions_table(table)

    # ── Gaps view ─────────────────────────────────────────────────────

    def _filtered_gaps(self) -> list[Gap]:
        f = self._filter.get("gaps", "").lower()
        if f:
            picked: list[Gap] = []
            for g in self._gaps:
                entity = self._entities.get(g.entity_id)
                rule = self._rules_by_id.get(g.rule_id)
                haystack = " ".join([
                    g.short_id,
                    entity.file_path if entity else "",
                    entity.qualified_name if entity else "",
                    rule.feature_value if rule else "",
                ]).lower()
                if f in haystack:
                    picked.append(g)
        else:
            picked = list(self._gaps)
        return self._sort_gaps(picked)

    def _sort_gaps(self, gaps: list[Gap]) -> list[Gap]:
        """Apply the current Gaps-view sort key to a list of gaps."""
        key = self._sort_keys.get("gaps", "conf_desc")

        def conf(g: Gap) -> float:
            r = self._rules_by_id.get(g.rule_id)
            return r.confidence if r is not None else 0.0

        def file_key(g: Gap) -> str:
            e = self._entities.get(g.entity_id)
            return (e.file_path, e.line) if e else ("", 0)  # type: ignore[return-value]

        def entity_key(g: Gap) -> str:
            e = self._entities.get(g.entity_id)
            return e.qualified_name if e else ""

        if key == "conf_desc":
            return sorted(gaps, key=lambda g: -conf(g))
        if key == "conf_asc":
            return sorted(gaps, key=conf)
        if key == "file":
            return sorted(gaps, key=file_key)
        if key == "entity":
            return sorted(gaps, key=entity_key)
        return gaps  # unknown key — leave as-is

    def _render_gaps_table(self, table: DataTable) -> None:
        from rich.text import Text

        table.add_columns(
            "▣", "●", "Location", "Entity", "Missing", "Conf", "ID",
        )
        gaps = self._filtered_gaps()
        flt = self._filter.get("gaps", "")
        sel_set = self._selected.get("gaps", set())
        for gap in gaps:
            rule = self._rules_by_id[gap.rule_id]
            entity = self._entities[gap.entity_id]
            short = entity.qualified_name.split("::", 1)[-1]

            # Multi-select marker — bright yellow ▣ when the row is
            # in the selection bucket, dim · otherwise. Same glyph
            # convention the suppressions view uses.
            mark = (
                Text("▣", style="bright_yellow")
                if gap.short_id in sel_set
                else Text("·", style="dim")
            )

            # Severity dot — color encodes confidence at a glance,
            # matching the CLI table's leftmost column.
            dot_style = _confidence_glyph_style(rule.confidence)
            dot = Text("●", style=dot_style)

            # Location: file path colored by language, line number
            # in dim. Filter matches highlighted in reverse-yellow.
            lang_color = _lang_color_for_path(entity.file_path)
            loc_markup = (
                f"[{lang_color}]{_highlight_match(entity.file_path, flt)}[/]"
                f"[dim]:{entity.line}[/]"
            )
            location = Text.from_markup(loc_markup)

            entity_markup = (
                f"{_highlight_match(entity.kind, flt)} "
                f"`[yellow]{_highlight_match(short, flt)}[/]`"
            )
            entity_cell = Text.from_markup(entity_markup)

            missing_markup = (
                f"missing "
                f"[red]{_highlight_match(rule.feature_value, flt)}[/]"
            )
            missing_cell = Text.from_markup(missing_markup)

            conf_cell = Text(
                f"{rule.confidence:.2f}", style=dot_style,
            )
            id_cell = Text.from_markup(
                f"[dim]{_highlight_match(gap.short_id, flt)}[/]"
            )

            table.add_row(
                mark, dot, location, entity_cell, missing_cell,
                conf_cell, id_cell,
                key=gap.short_id,
            )

        if gaps:
            self._render_gap_detail(gaps[0])
        elif self._gaps:
            # There ARE gaps but the filter excluded them all.
            self._set_detail(
                f"[b]No gaps match the filter[/] [yellow]/{flt}[/].\n\n"
                f"[dim]/[/] to change the filter (Esc inside the input "
                f"clears it), [dim]Ctrl+R[/] to rescan."
            )
        elif self._scan_stats:
            # Successful scan with zero gaps — celebrate + show what
            # absentia did so the user knows it actually ran.
            stats = self._scan_stats
            ent = stats.get("entities_scanned", 0)
            rules = stats.get("rules", 0)
            sup = stats.get("suppressed", 0)
            dur = (stats.get("duration_ms", 0) or 0) / 1000
            sup_line = (
                f"\n[dim]({sup:,d} divergence{'s' if sup != 1 else ''} "
                f"suppressed via [b]s[/].)[/]"
            ) if sup else ""
            self._set_detail(
                f"[bright_green b]✓ All clean.[/]\n\n"
                f"absentia scanned [b]{ent:,d}[/] entities and learned "
                f"[b]{rules:,d}[/] convention{'s' if rules != 1 else ''} "
                f"in [b]{dur:.2f}s[/] — every entity follows the patterns "
                f"its peers do."
                f"{sup_line}\n\n"
                f"[dim]Ctrl+R to rescan · w to watch · q to quit · "
                f"x to export[/]"
            )
        else:
            # Initial render before scan completes — shouldn't happen
            # in practice (LoadingScreen covers the table), but a
            # defensive fallback prevents an empty pane if something
            # races.
            self._set_detail(
                "[dim]Waiting for scan results…[/]"
            )

    # ── Rules view ────────────────────────────────────────────────────

    def _filtered_rules(self) -> list[Rule]:
        f = self._filter.get("rules", "").lower()
        if f:
            picked = []
            for r in self._rules:
                haystack = " ".join([
                    r.id, r.group_id, r.feature_value,
                    f"{r.confidence:.2f}",
                ]).lower()
                if f in haystack:
                    picked.append(r)
        else:
            picked = list(self._rules)
        return self._sort_rules(picked)

    def _sort_rules(self, rules: list[Rule]) -> list[Rule]:
        key = self._sort_keys.get("rules", "conf_desc")
        if key == "conf_desc":
            return sorted(rules, key=lambda r: -r.confidence)
        if key == "support_desc":
            return sorted(rules, key=lambda r: -r.support_n)
        if key == "group":
            return sorted(rules, key=lambda r: r.group_id)
        return rules

    def _render_rules_table(self, table: DataTable) -> None:
        table.add_columns(
            "Rule (kind = value)", "Group", "Support", "Conf",
        )
        rules = self._filtered_rules()
        for rule in rules:
            table.add_row(
                f"{rule.feature_kind} = {rule.feature_value}",
                rule.group_id,
                f"{rule.support_n}/{rule.support_total}",
                f"{rule.confidence:.2f}",
                key=rule.id,
            )
        if rules:
            self._render_rule_detail(rules[0])
        else:
            self._set_detail("[b]No rules match the current filter.[/]")

    def _render_rule_detail(self, rule: Rule) -> None:
        # Escape every user-data interpolation: member names can contain
        # [] (Python typing like List[int], parameterized C++ template
        # types, etc.), and rule.feature_value can carry decorator-arg
        # parens / brackets. Unescaped, those tokens crash rich's
        # markup parser with MarkupError when Static.update parses the
        # detail pane. Caught when the Rules view tried to render every
        # rule (Gaps view hides this because it only renders rules with
        # current divergences, which often skips the rules carrying
        # bracket-bearing values).
        from rich.markup import escape as _esc

        group = self._groups_by_id.get(rule.group_id)
        members_with: list[str] = []
        members_without: list[str] = []
        if group is not None:
            for mid in group.members:
                fset = None
                ent = self._entities.get(mid)
                # find features by entity_id by re-querying — use storage state
                # via the index we already have in _entities; the rule already
                # carried support_n/total so we can just enumerate members
                # against the feature index loaded into the app.
                fset = self._lookup_features(mid)
                if fset and rule.feature_value in fset.get_set(rule.feature_kind):
                    members_with.append(mid.rsplit("::", 1)[-1])
                else:
                    if ent and self._is_eligible(ent, rule.feature_kind):
                        members_without.append(mid.rsplit("::", 1)[-1])
        with_text = "  ".join(f"✓ {_esc(m)}" for m in members_with[:9])
        if len(members_with) > 9:
            with_text += f"   …(+{len(members_with) - 9} more)"
        without_text = "  ".join(f"✗ {_esc(m)}" for m in members_without[:9])
        if len(members_without) > 9:
            without_text += f"   …(+{len(members_without) - 9} more)"
        self._set_detail(
            f"[b cyan]{_esc(rule.id)}[/]\n\n"
            f"[b]Group[/]    {_esc(rule.group_id)}\n"
            f"[b]Pattern[/]  {_esc(rule.feature_kind)} = "
            f"[cyan]{_esc(rule.feature_value)}[/]\n"
            f"[b]Support[/]  {rule.support_n} / {rule.support_total}   "
            f"(confidence [b]{rule.confidence:.2f}[/])\n\n"
            f"[b]With ({len(members_with)})[/]:    "
            f"{with_text or '(none — group is the violator pool)'}\n\n"
            f"[b]Without ({len(members_without)})[/]: "
            f"{without_text or '(none — every eligible member exhibits the pattern)'}\n\n"
            f"[b]f[/] to follow into the group · [b]Esc[/] back"
        )

    def _lookup_features(self, entity_id: str):
        # The TUI doesn't keep the full feature_index in memory after the
        # initial scan (it's not needed for the gaps view). For Rules-view
        # detail we re-derive eligibility from the entity's known kind +
        # the feature_kind. Returning None is fine — the detail still
        # shows correct support counts, just not member-level breakdown.
        return None

    def _is_eligible(self, entity: Entity, feature_kind: str) -> bool:
        if feature_kind == "parent_class":
            return entity.kind in (
                "class", "struct", "enum", "extension", "protocol",
                "interface", "trait", "impl", "module", "record",
            )
        return entity.kind in ("function", "method")

    # ── Groups view ───────────────────────────────────────────────────

    def _filtered_groups(self) -> list[Group]:
        f = self._filter.get("groups", "").lower()
        if f:
            picked = [
                g for g in self._groups
                if f in g.id.lower() or f in g.selector_type.lower()
            ]
        else:
            picked = list(self._groups)
        return self._sort_groups(picked)

    def _sort_groups(self, groups: list[Group]) -> list[Group]:
        key = self._sort_keys.get("groups", "members_desc")
        if key == "members_desc":
            return sorted(groups, key=lambda g: -len(g.members))
        if key == "members_asc":
            return sorted(groups, key=lambda g: len(g.members))
        if key == "name":
            return sorted(groups, key=lambda g: g.id)
        return groups

    def _render_groups_table(self, table: DataTable) -> None:
        table.add_columns("Group", "Selector", "Members", "Rules")
        groups = self._filtered_groups()
        # Map group_id → rule count for quick lookup
        rule_counts: dict[str, int] = {}
        for r in self._rules:
            rule_counts[r.group_id] = rule_counts.get(r.group_id, 0) + 1

        for group in groups:
            table.add_row(
                group.name,
                group.selector_type,
                str(len(group.members)),
                str(rule_counts.get(group.id, 0)),
                key=group.id,
            )
        if groups:
            self._render_group_detail(groups[0])
        else:
            self._set_detail("[b]No groups match the current filter.[/]")

    def _render_group_detail(self, group: Group) -> None:
        # Same defensive escape as the gap / rule detail renders —
        # group ids can carry `[]` (e.g. typed-class parent_class
        # values), member tail names can carry brackets, and rule
        # feature values can be anything.
        from rich.markup import escape as _esc

        rules_for = [r for r in self._rules if r.group_id == group.id]
        member_names = [
            _esc(m.rsplit("::", 1)[-1]) for m in group.members[:20]
        ]
        more = (
            f"   …(+{len(group.members) - 20} more)"
            if len(group.members) > 20 else ""
        )
        rules_text = (
            "\n".join(
                f"  {_esc(r.feature_kind)} = {_esc(r.feature_value)}   "
                f"{r.support_n}/{r.support_total} ({r.confidence:.2f})"
                for r in rules_for
            ) or "  (none — no feature reached the confidence threshold)"
        )
        self._set_detail(
            f"[b cyan]{_esc(group.id)}[/]\n\n"
            f"[b]Selector[/]  {_esc(group.selector_type)}\n"
            f"[b]Members[/]   {len(group.members)}\n"
            f"  {'  '.join(member_names)}{more}\n\n"
            f"[b]Rules ({len(rules_for)})[/]:\n{rules_text}\n\n"
            f"[b]f[/] to follow to first member · [b]Esc[/] back"
        )

    # ── Suppressions view ────────────────────────────────────────────

    def _filtered_suppressions(self) -> list[dict]:
        """Merge local + project suppressions into a single sortable
        list of dicts with unified shape for the DataTable.

        Each row: ``{key, source, label, target, reason, created}``
        where ``key`` is the row id (short_id for local, a synthetic
        ``project::N`` for read-only project entries), and ``source``
        is ``"local"`` or ``"project"``.
        """
        rows: list[dict] = []

        # Local — from state.db. Map gap.short_id back to the entity
        # name when the suppressed gap is still in the current scan
        # (it usually isn't — the suppression hides it from results).
        # Fall back to the stored full_id when the entity isn't in
        # the current corpus.
        for short_id, info in self._local_suppressions.items():
            full_id = info.get("full_id") or ""
            target = ""
            # full_id format: "<rule_id>::<entity_id>". Pull the
            # entity tail for display.
            if "::" in str(full_id):
                target = str(full_id).split("::", 1)[1]
            rows.append({
                "key":     short_id,
                "source":  "local",
                "label":   short_id,
                "target":  target or full_id,
                "reason":  info.get("reason") or "",
                "created": info.get("created_at") or "",
            })

        # Project — read-only entries from absentia.toml. Engine
        # doesn't enforce them today (advisory until wired up); the
        # row is shown so users can review what's committed without
        # leaving the TUI.
        for i, entry in enumerate(self._project_suppressions):
            target_parts = []
            if entry.get("entity"):
                target_parts.append(str(entry["entity"]))
            if entry.get("rule"):
                target_parts.append(f"({entry['rule']})")
            rows.append({
                "key":     f"project::{i}",
                "source":  "project",
                "label":   f"project#{i + 1}",
                "target":  " ".join(target_parts),
                "reason":  entry.get("reason") or "",
                "created": entry.get("created") or "",
            })

        # Filter using the existing `/`-search infrastructure.
        f = self._filter.get("suppressions", "").lower()
        if f:
            rows = [
                r for r in rows
                if f in (
                    f"{r['label']} {r['target']} {r['reason']} "
                    f"{r['source']}"
                ).lower()
            ]
        return rows

    def _render_suppressions_table(self, table: DataTable) -> None:
        """Render the merged local + project suppression list."""
        from rich.text import Text

        table.add_columns(
            "▣", "Source", "ID", "Target", "Reason", "Created",
        )
        rows = self._filtered_suppressions()
        flt = self._filter.get("suppressions", "")
        sel_set = self._selected.get("suppressions", set())

        for row in rows:
            key = row["key"]
            mark = (
                Text("▣", style="bright_yellow")
                if key in sel_set else Text("·", style="dim")
            )
            source_style = (
                "cyan" if row["source"] == "local" else "magenta"
            )
            source_cell = Text(row["source"], style=source_style)
            label_cell = Text.from_markup(
                f"[dim]{_highlight_match(row['label'], flt)}[/]"
            )
            target_cell = Text.from_markup(
                _highlight_match(row["target"], flt) or "[dim]—[/]"
            )
            reason_cell = Text.from_markup(
                _highlight_match(row["reason"] or "[dim](no reason)[/]", flt)
                if row["reason"] else "[dim](no reason)[/]"
            )
            created_cell = Text(
                row["created"][:19] if row["created"] else "—",
                style="dim",
            )
            table.add_row(
                mark,
                source_cell,
                label_cell,
                target_cell,
                reason_cell,
                created_cell,
                key=key,
            )

        if rows:
            self._render_suppression_detail(rows[0])
        else:
            self._set_detail(
                "[b]No suppressions yet.[/]\n\n"
                "Press [b]s[/] on a gap (in the Gaps view) to add "
                "one.\n"
                "Project-wide entries live in "
                "[cyan]absentia.toml[/]'s [b][[suppress]][/] blocks "
                "and show up here once added; press [b],[/] then "
                "[b]e[/] to open the file."
            )

    def _render_suppression_detail(self, row: dict) -> None:
        """Detail-pane card for the highlighted suppression."""
        # Escape user-data fields (target = entity qualified_name with
        # optional rule_id, reason = free-form user text). Both can
        # carry [ ] brackets that confuse rich's markup parser.
        from rich.markup import escape as _esc

        source = row["source"]
        target = _esc(row["target"]) if row["target"] else "—"
        reason = (
            _esc(row["reason"]) if row["reason"]
            else "[dim](no reason recorded)[/]"
        )
        created = _esc(row["created"]) if row["created"] else "—"
        if source == "local":
            actions = (
                "[b]r[/] remove · [b]Space[/] toggle multi-select · "
                "[b]r[/] (with selection) bulk-remove"
            )
        else:
            actions = (
                "[dim]Read-only — edit via [b],[/]"
                "[dim] settings → [b]e[/]"
                "[dim] open absentia.toml.[/]"
            )
        self._set_detail(
            f"[b cyan]{_esc(row['label'])}[/]   [dim]({source})[/]\n\n"
            f"[b]Target[/]   {target}\n"
            f"[b]Reason[/]   {reason}\n"
            f"[b]Created[/]  {created}\n\n"
            f"{actions}"
        )

    # ── Multi-select + bulk operations ───────────────────────────────

    def action_toggle_select(self) -> None:
        """Space toggles row selection on Gaps + Suppressions views.

        Adds a visual ▣ marker in the leftmost column of selected
        rows. Subsequent ``s`` (gaps) / ``r`` (suppressions) operate
        on the whole selection if non-empty, else fall back to the
        cursor row. No-op on Rules / Groups / Stats — those views
        don't have actions that benefit from bulk selection.
        """
        if self._view not in ("gaps", "suppressions"):
            return
        sel = self._selected_id()
        if sel is None:
            return
        bucket = self._selected.setdefault(self._view, set())
        if sel in bucket:
            bucket.discard(sel)
        else:
            bucket.add(sel)
        self._render_table()

    def action_remove_suppression(self) -> None:
        """Remove the selected suppression(s) and rescan.

        Multi-select aware: if any rows are checked via Space, all of
        them are removed in one batch (project-source rows are
        skipped with a notification — they're read-only here).
        Otherwise the cursor row is removed.
        """
        if self._view != "suppressions":
            self.notify(
                "Switch to the Suppressions view (5) first.",
                severity="warning",
            )
            return

        bucket = self._selected.get("suppressions", set())
        targets: list[str] = list(bucket) if bucket else []
        if not targets:
            sel = self._selected_id()
            if sel is None:
                return
            targets = [sel]

        # Project-source rows are read-only here; skip them and
        # surface a single hint so the user knows why.
        local_keys: list[str] = []
        skipped_project = 0
        for k in targets:
            if k.startswith("project::"):
                skipped_project += 1
            else:
                local_keys.append(k)

        if not local_keys:
            self.notify(
                "Project-wide suppressions are read-only here. "
                "Edit absentia.toml via , → e.",
                severity="warning", timeout=8,
            )
            return

        try:
            with Storage(self.root / ".absentia") as storage:
                for short_id in local_keys:
                    storage.remove_suppression(short_id)
        except (StorageVersionError, StateLockError) as exc:
            self.notify(
                f"Couldn't remove: {exc}",
                severity="error", timeout=8,
            )
            return

        # Clear selection + rescan so the unsuppressed gap reappears
        # in the gaps view.
        bucket.clear()
        msg_parts = [f"Removed {len(local_keys)} suppression"]
        if len(local_keys) != 1:
            msg_parts[0] += "s"
        if skipped_project:
            msg_parts.append(
                f"(skipped {skipped_project} project entry"
                f"{'ies' if skipped_project != 1 else 'y'})"
            )
        self.notify(" ".join(msg_parts))
        self._do_scan()

    # ── Stats view ────────────────────────────────────────────────────

    def _render_stats(self) -> None:
        s = self._scan_stats
        kind_counts: dict[str, int] = {}
        for e in self._entities.values():
            kind_counts[e.kind] = kind_counts.get(e.kind, 0) + 1
        kind_summary = ", ".join(
            f"{k}: {v}" for k, v in sorted(kind_counts.items())
        )

        # Top contributing groups by rule count
        rule_count_per_group: dict[str, int] = {}
        gap_count_per_group: dict[str, int] = {}
        for r in self._rules:
            rule_count_per_group[r.group_id] = (
                rule_count_per_group.get(r.group_id, 0) + 1
            )
        for g in self._gaps:
            rule = self._rules_by_id.get(g.rule_id)
            if rule:
                gap_count_per_group[rule.group_id] = (
                    gap_count_per_group.get(rule.group_id, 0) + 1
                )
        top_groups = sorted(
            self._groups,
            key=lambda g: -rule_count_per_group.get(g.id, 0),
        )[:5]
        top_groups_text = "\n".join(
            f"  {g.id:<48s} {len(g.members):>3d} members "
            f"· {rule_count_per_group.get(g.id, 0)} rules "
            f"· {gap_count_per_group.get(g.id, 0)} gaps"
            for g in top_groups
        ) or "  (no groups discovered)"

        self.query_one("#stats_text", Static).update(
            f"[b cyan]Scan[/]\n"
            f"  Started     {s.get('started_at', '-')}\n"
            f"  Duration    {s.get('duration_ms', 0):.0f} ms\n"
            f"  Languages   {', '.join(self.config.scan.languages)}\n\n"
            f"[b cyan]Corpus[/]\n"
            f"  Entities    {s.get('entities_scanned', 0):>5}    "
            f"({kind_summary})\n"
            f"  Files       {s.get('files_seen', 0):>5}    "
            f"({s.get('files_unchanged', 0)} unchanged from cache)\n\n"
            f"[b cyan]Mining[/]\n"
            f"  Groups      {s.get('groups', 0):>5}\n"
            f"  Rules       {s.get('rules', 0):>5}    at confidence ≥ "
            f"{s.get('min_confidence', 0):.2f}\n"
            f"  Gaps        {len(self._gaps):>5}    "
            f"({s.get('suppressed', 0)} suppressed)\n\n"
            f"[b cyan]Top contributing groups[/]\n{top_groups_text}"
        )

    # ── Detail pane / selection ──────────────────────────────────────

    def _set_detail(self, text: str) -> None:
        self.query_one("#detail", Static).update(text)

    def _set_preview(self, text: str) -> None:
        """Update the bottom code-context pane.

        Tolerant of races where the widget hasn't mounted yet (e.g.
        very early calls during compose); silently no-ops in that
        case. Same defensive shape as LoadingScreen._refresh.
        """
        try:
            self.query_one("#preview", Static).update(text)
        except Exception:
            pass

    def _render_gap_preview(self, gap: Gap) -> None:
        """Render a code-context block for the selected gap.

        Reads the gap's source file, slices ~5 lines on either side
        of the gap entity's declaration line, and renders them with
        the target line marked with ``▶`` in bright_yellow. Header
        on the first line: ``<file_path> | lines N-M``.

        Failures (file unreadable, line out of range, binary file)
        surface as a dim error message in the preview pane rather
        than crashing the TUI.
        """
        entity = self._entities.get(gap.entity_id)
        if entity is None:
            self._set_preview("[dim](no entity for gap)[/]")
            return
        target = self.root / entity.file_path
        line = entity.line

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._set_preview(
                f"[b cyan]{entity.file_path}[/]\n\n"
                f"[red]Couldn't read file:[/] [dim]{exc}[/]"
            )
            return

        lines = text.splitlines()
        total = len(lines)
        if line < 1 or line > total:
            self._set_preview(
                f"[b cyan]{entity.file_path}[/]\n\n"
                f"[red]Line {line} out of range[/] [dim]"
                f"(file has {total} lines).[/]"
            )
            return

        # ±4 lines of context, clamped to file bounds. Total preview
        # body = up to 9 lines, fits the 12-row pane (1 header +
        # blank + 9 lines = 11; one row of border).
        pad = 4
        start = max(1, line - pad)
        end = min(total, line + pad)
        width = len(str(end))

        from rich.markup import escape as _md_escape

        body: list[str] = []
        for ln in range(start, end + 1):
            content = _md_escape(lines[ln - 1])
            num = f"{ln:>{width}}"
            if ln == line:
                # The gap line — yellow arrow + bold content so the
                # eye lands instantly even on a wide terminal.
                body.append(
                    f"[bright_yellow]{num}[/] "
                    f"[bright_yellow]▶[/] [b]{content}[/]"
                )
            else:
                body.append(f"[dim]{num}  [/]   {content}")

        header = (
            f"[b cyan]{_md_escape(entity.file_path)}[/] "
            f"[dim]| lines {start}-{end}[/]"
        )
        self._set_preview(header + "\n\n" + "\n".join(body))

    # (Row-cursor change handler is the unified
    # on_data_table_row_highlighted further down — it dispatches to
    # the right detail-renderer per view AND updates the preview
    # pane when the gaps view is active.)

    def _selected_id(self) -> str | None:
        if self._view == "stats":
            return None
        table = self.query_one("#main_table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        return row_key.value if row_key else None

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected,
    ) -> None:
        """DataTable owns Enter; route it to our open-in-editor action so
        the App's binding works with the table focused."""
        self.action_open_in_editor()

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted,
    ) -> None:
        """Update the detail pane (and gaps-only preview pane) on
        row-cursor change.

        Per view:
          - gaps   → render the gap's detail card AND the bottom
                     code-context preview (file:line + ±4 lines).
          - rules  → render the rule's group / support / members
                     breakdown into the detail pane.
          - groups → render the group's selector / members card.

        Stats view has no row-cursor; this handler is a no-op there
        (the gate is `if event.row_key is None` since stats pane
        doesn't have a #main_table populated).
        """
        if event.row_key is None or event.row_key.value is None:
            return
        rk = event.row_key.value
        if self._view == "gaps":
            for gap in self._gaps:
                if gap.short_id == rk:
                    self._render_gap_detail(gap)
                    self._render_gap_preview(gap)
                    return
        elif self._view == "rules":
            rule = self._rules_by_id.get(rk)
            if rule is not None:
                self._render_rule_detail(rule)
        elif self._view == "groups":
            group = self._groups_by_id.get(rk)
            if group is not None:
                self._render_group_detail(group)
        elif self._view == "suppressions":
            for row in self._filtered_suppressions():
                if row["key"] == rk:
                    self._render_suppression_detail(row)
                    return

    def _render_gap_detail(self, gap: Gap) -> None:
        # Escape every user-data field — feature_value / qualified_name
        # / file_path can contain [ ] / parens / unicode that breaks
        # rich's markup parser when Static.update reparses the string.
        from rich.markup import escape as _esc

        rule = self._rules_by_id[gap.rule_id]
        entity = self._entities[gap.entity_id]
        self._set_detail(
            f"[b cyan]{gap.short_id}[/]   ([dim]{_esc(gap.id)}[/])\n\n"
            f"[b]Entity[/]   {_esc(entity.qualified_name)}\n"
            f"         {_esc(entity.file_path)}:{entity.line}   "
            f"[dim]\\[{_esc(entity.kind)}][/]\n\n"
            f"[b]Rule[/]     {_esc(rule.id)}\n"
            f"         {rule.support_n}/{rule.support_total} members of "
            f"[yellow]{_esc(rule.group_id)}[/] have "
            f"[cyan]{_esc(rule.feature_value)}[/]\n"
            f"         confidence [b]{rule.confidence:.2f}[/]\n\n"
            f"[b]Verdict[/]  this entity does not have "
            f"[cyan]{_esc(rule.feature_value)}[/].\n"
            f"         [b]e[/] explain · [b]s[/] suppress · "
            f"[b]Enter[/] open · [b]f[/] follow to rule"
        )

    # ── View switching ────────────────────────────────────────────────

    def _switch_view(self, view: str) -> None:
        if view not in _VIEW_LABELS:
            return
        self._view = view
        self._render_current_view()

    def action_view_gaps(self) -> None:    self._switch_view("gaps")
    def action_view_rules(self) -> None:   self._switch_view("rules")
    def action_view_groups(self) -> None:  self._switch_view("groups")
    def action_view_stats(self) -> None:   self._switch_view("stats")

    def action_view_suppressions(self) -> None:
        self._switch_view("suppressions")

    # ── Cross-view follow + breadcrumb ───────────────────────────────

    def action_follow(self) -> None:
        sel = self._selected_id()
        if sel is None:
            return
        if self._view == "gaps":
            gap = next((g for g in self._gaps if g.short_id == sel), None)
            if gap is None:
                return
            self._nav_stack.append((self._view, sel))
            self._switch_view("rules")
            self._select_row(gap.rule_id)
        elif self._view == "rules":
            rule = self._rules_by_id.get(sel)
            if rule is None:
                return
            self._nav_stack.append((self._view, sel))
            self._switch_view("groups")
            self._select_row(rule.group_id)
        elif self._view == "groups":
            group = self._groups_by_id.get(sel)
            if group is None or not group.members:
                return
            entity = self._entities.get(group.members[0])
            if entity is None:
                return
            # No table to land on for a single entity; just open in editor.
            self._open_entity_in_editor(entity)

    async def action_back(self) -> None:
        if not self._nav_stack:
            return
        view, sel = self._nav_stack.pop()
        self._switch_view(view)
        self._select_row(sel)

    def _select_row(self, key: str) -> None:
        if self._view == "stats":
            return
        table = self.query_one("#main_table", DataTable)
        try:
            row_idx = table.get_row_index(key)
        except Exception:
            return
        table.move_cursor(row=row_idx)

    # ── Filter, suppress, rescan, watch, help, open ──────────────────

    def action_filter(self) -> None:
        if self._view == "stats":
            self.notify("Filter doesn't apply to Stats view.")
            return
        current = self._filter.get(self._view, "")
        self.push_screen(FilterScreen(current), self._filter_done)

    def _filter_done(self, value: str | None) -> None:
        if value is None:
            return
        self._filter[self._view] = value
        self._render_current_view()

    def action_explain(self) -> None:
        if self._view != "gaps":
            self.notify("Explain only applies to gaps.")
            return
        sel = self._selected_id()
        if sel is None:
            return
        gap = next((g for g in self._gaps if g.short_id == sel), None)
        if gap is None:
            return
        rule = self._rules_by_id.get(gap.rule_id)
        entity = self._entities.get(gap.entity_id)
        if rule is None or entity is None:
            return
        group = self._groups_by_id.get(rule.group_id)
        self.push_screen(
            ExplainScreen(
                gap=gap,
                rule=rule,
                entity=entity,
                group=group,
                feature_index=self._feature_index,
                min_confidence=self.config.mining.min_confidence,
            ),
            self._explain_done,
        )

    def _explain_done(self, result: str | None) -> None:
        """Handle the Explain modal's exit signal.

        ``"suppress"`` means the user pressed ``s`` inside the modal —
        chain into the existing suppress flow for the still-selected
        gap. Falls through silently otherwise.
        """
        if result == "suppress":
            self.action_suppress()

    def action_suppress(self) -> None:
        """Suppress the cursor row, or every selected row if any.

        Multi-select aware: pressing Space on one or more gap rows
        marks them; ``s`` then pops a single SuppressScreen asking
        for one shared reason. The reason is applied to every
        marked gap. With no selection, the cursor row is suppressed
        (existing single-row behavior).
        """
        if self._view != "gaps":
            self.notify("Suppress only applies to gaps.")
            return
        bucket = self._selected.get("gaps", set())
        targets: list[str] = list(bucket) if bucket else []
        if not targets:
            sel = self._selected_id()
            if sel is None:
                return
            targets = [sel]

        # Resolve to (short_id, full_id) pairs and assemble the
        # modal header. For single-row, use the existing
        # "Suppress g-XXXX (missing FOO)" form so the keystroke
        # behavior is unchanged when the user hasn't multi-selected.
        if len(targets) == 1:
            short = targets[0]
            gap = next((g for g in self._gaps if g.short_id == short), None)
            if gap is None:
                return
            rule = self._rules_by_id[gap.rule_id]
            header = (
                f"Suppress [bold cyan]{short}[/]   "
                f"(missing {rule.feature_value})"
            )
        else:
            header = (
                f"Suppress [bold]{len(targets)}[/] gaps   "
                f"[dim](one shared reason)[/]"
            )
        self.push_screen(
            SuppressScreen(targets, header),
            self._suppress_done,
        )

    def _suppress_done(
        self, result: tuple[list[str], str] | None,
    ) -> None:
        if result is None:
            return
        short_ids, reason = result
        try:
            with Storage(self.root / ".absentia") as storage:
                for sid in short_ids:
                    gap = next(
                        (g for g in self._gaps if g.short_id == sid), None,
                    )
                    full_id = gap.id if gap else None
                    storage.add_suppression(
                        short_id=sid, full_id=full_id, reason=reason,
                    )
        except StorageVersionError as exc:
            self.notify(
                f"Storage error: {exc}", severity="error", timeout=8,
            )
            return
        # Selection consumed — clear it so the next keystroke isn't
        # surprised by leftover marks.
        self._selected.get("gaps", set()).clear()
        n = len(short_ids)
        self.notify(
            f"Suppressed {n} gap{'s' if n != 1 else ''}", timeout=4,
        )
        self._do_scan()

    def action_rescan(self) -> None:
        self.notify("Rescanning…")
        self._do_scan()

    def action_toggle_watch(self) -> None:
        if self._watch_timer is None:
            self._watch_timer = self.set_interval(2.0, self._do_scan)
            self.notify("Watch mode on (rescanning every 2s)")
        else:
            self._watch_timer.stop()
            self._watch_timer = None
            self.notify("Watch mode off")
        self._update_subtitle()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_command_palette(self) -> None:
        """Open the fuzzy-search command palette.

        Mirrors the Cmd+K / Ctrl+P pattern from VS Code, JetBrains,
        and most modern editors. Lists every TUI action with its
        keystroke; users learn shortcuts by seeing them, and power
        users can just type "exp" → Enter rather than memorizing x.
        """
        self.push_screen(
            CommandPaletteScreen(),
            self._on_command_palette_done,
        )

    async def _on_command_palette_done(self, action: str | None) -> None:
        """Dispatch the selected action via Textual's run_action.

        ``run_action`` is async (returns a coroutine) — the
        callback is async so we can await it directly. Push-screen
        callbacks accept both sync and async forms. ``run_action``
        accepts the action name as a string and routes it through
        the standard binding-resolution path — same code path the
        keystroke would take, so dispatch stays in lockstep with
        the bindings table.
        """
        if not action:
            return
        try:
            await self.run_action(action)
        except Exception as exc:
            self.notify(
                f"Couldn't run action {action!r}: {exc}",
                severity="error", timeout=8,
            )

    def action_cycle_sort(self) -> None:
        """Capital-S: advance the current view's sort key one step.

        Each view has its own cycle (defined in __init__'s
        ``_sort_cycles``); pressing S walks through the options and
        wraps around. Stats view has no sortable list so the action
        is a no-op there. Re-renders the table immediately so the
        user sees the new ordering, and updates the subtitle to
        show the active sort label.
        """
        if self._view not in self._sort_cycles:
            return
        cycle = self._sort_cycles[self._view]
        current = self._sort_keys.get(self._view, cycle[0])
        try:
            idx = cycle.index(current)
        except ValueError:
            idx = -1
        next_key = cycle[(idx + 1) % len(cycle)]
        self._sort_keys[self._view] = next_key
        self._render_current_view()
        label = self._sort_labels.get(next_key, next_key)
        self.notify(f"Sort: {label}", timeout=2)

    def action_settings(self) -> None:
        """Open the machine-wide settings panel.

        Edits ``~/.absentia/settings.json`` (jobs_default,
        default_export_path, info_hint_shown_at) and provides a
        one-keystroke handoff to ``$EDITOR`` for the per-project
        ``absentia.toml`` — the structured config that's better
        edited in a real editor than a TUI form.
        """
        self.push_screen(SettingsScreen(self.root))

    def action_export(self) -> None:
        """Open the export-format picker.

        Mirrors the post-check CLI export prompt but TUI-native: a
        modal lists the six formats, the user picks one, the file
        is written under the saved ``default_export_path`` (with
        the same ``<base>/docs/absentia/<corpus>/gaps-<UTC>.<ext>``
        layout the CLI uses), and a notification confirms.

        Falls through with a notification when:
          - there's no scan result loaded yet (initial scan still
            running, or it failed);
          - no ``default_export_path`` is configured (the user
            needs to set one via the CLI's export prompt first).
        """
        if not self._scan_stats:
            self.notify("Scan still loading; try again in a moment.")
            return
        if not self._gaps and not self._rules:
            self.notify(
                "Nothing to export yet — no gaps or rules.",
                severity="warning",
            )
            return
        self.push_screen(ExportFormatScreen(), self._on_export_format_chosen)

    def _on_export_format_chosen(self, fmt_idx: int | None) -> None:
        """Format picked → push the location chooser.

        Stashes the format tuple on ``self._pending_export_fmt``
        so the location-screen callback can find it without
        re-deriving from ``fmt_idx``.
        """
        if fmt_idx is None:
            return
        from .. import export as exp_mod

        fmt = next(
            (f for f in exp_mod._FORMATS if f[0] == fmt_idx), None,
        )
        if fmt is None:
            self.notify("Unknown format choice.", severity="error")
            return
        self._pending_export_fmt = fmt
        self.push_screen(
            ExportLocationScreen(),
            self._on_export_location_chosen,
        )

    def _on_export_location_chosen(self, choice: str | None) -> None:
        """Location picked → either write straight to the saved
        default, or push the path-input screen for a custom value /
        first-time default setup."""
        fmt = self._pending_export_fmt
        if choice is None or fmt is None:
            self._pending_export_fmt = None
            return

        if choice == "custom":
            self.push_screen(
                ExportPathInputScreen(prompt="Custom base path:"),
                self._on_export_custom_path,
            )
            return

        # choice == "default"
        from ..settings import load_settings
        settings = load_settings()
        if settings.default_export_path is not None:
            base = Path(
                settings.default_export_path,
            ).expanduser().resolve()
            self._do_export_write(base, fmt)
            return

        # No default yet — prompt to set one + remember it.
        self.push_screen(
            ExportPathInputScreen(
                prompt="No default set yet — base path to remember:",
            ),
            self._on_export_set_default,
        )

    def _validate_path(self, raw: str) -> Path | None:
        """Resolve ``~`` and relative-path syntax safely.

        Returns the absolute Path on success, ``None`` after
        notifying the user about the specific failure (so the
        caller knows to re-prompt rather than silently swallow).
        Catches the full set of exceptions ``Path.expanduser`` /
        ``Path.resolve`` can raise (RuntimeError on broken
        ``$HOME`` resolution, OSError on path-too-long /
        permission, ValueError on null bytes, etc.).
        """
        try:
            return Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            self.notify(
                f"Invalid path: {exc}",
                severity="warning", timeout=8,
            )
            return None

    def _on_export_custom_path(self, raw: str | None) -> None:
        """One-off custom path; not saved to settings.

        Re-prompts on invalid input so the user can correct without
        starting the format/location flow over from scratch.
        """
        fmt = self._pending_export_fmt
        if raw is None or fmt is None:
            self._pending_export_fmt = None
            return
        base = self._validate_path(raw)
        if base is None:
            self.push_screen(
                ExportPathInputScreen(
                    prompt="Custom base path (try again):",
                    initial=raw,
                ),
                self._on_export_custom_path,
            )
            return
        self._do_export_write(base, fmt)

    def _on_export_set_default(self, raw: str | None) -> None:
        """First-time default-path set: persist to settings.json
        before writing the export. Subsequent picks of the
        default-path option go through the no-prompt fast path.

        Re-prompts on invalid input. Settings are only written
        AFTER validation succeeds so a bad path can't poison the
        saved default.
        """
        fmt = self._pending_export_fmt
        if raw is None or fmt is None:
            self._pending_export_fmt = None
            return
        base = self._validate_path(raw)
        if base is None:
            self.push_screen(
                ExportPathInputScreen(
                    prompt="Base path to remember (try again):",
                    initial=raw,
                ),
                self._on_export_set_default,
            )
            return
        from dataclasses import replace
        from ..settings import load_settings, save_settings
        settings = load_settings()
        try:
            save_settings(
                replace(settings, default_export_path=str(base)),
            )
        except OSError as exc:
            self.notify(
                f"Couldn't save default path: {exc}",
                severity="warning", timeout=8,
            )
        else:
            self.notify(
                f"Saved {base} as default export path", timeout=6,
            )
        self._do_export_write(base, fmt)

    def _do_export_write(
        self, base: Path, fmt: tuple,
    ) -> None:
        """Render the export and write to disk under
        ``<base>/docs/absentia/<corpus>/gaps-<UTC-ts>.<ext>``,
        notifying the user of the result either way."""
        from .. import export as exp_mod

        _menu_id, fmt_name, fmt_ext, fmt_fn_name = fmt
        corpus_name = self.root.name or "scan"
        out_path = exp_mod.build_export_path(
            base, corpus_name, fmt_ext,
        )
        try:
            renderer = getattr(exp_mod, fmt_fn_name)
            body = renderer(
                root=self.root,
                gaps=self._gaps,
                rules_by_id=self._rules_by_id,
                entities=self._entities,
                scan_stats=self._scan_stats,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(body, encoding="utf-8")
        except OSError as exc:
            self.notify(
                f"Export failed: {exc}", severity="error", timeout=8,
            )
            self._pending_export_fmt = None
            return
        self.notify(
            f"Exported {fmt_name} to {out_path}", timeout=10,
        )
        self._pending_export_fmt = None

    def action_open_in_editor(self) -> None:
        if self._view == "gaps":
            sel = self._selected_id()
            if sel is None:
                return
            gap = next((g for g in self._gaps if g.short_id == sel), None)
            if gap is None:
                return
            self._open_entity_in_editor(self._entities[gap.entity_id])

    def _open_entity_in_editor(self, entity: Entity) -> None:
        self._open_in_editor(self.root / entity.file_path, entity.line)

    def _open_in_editor(self, target: Path, line: int = 1) -> None:
        """Open ``target`` at ``line`` in the user's editor.

        Embedded hosts (Dev-Dashboard panel) get forwarded via the
        ``on_open_editor`` callback registered at app construction.
        Standalone runs spawn ``$EDITOR`` via subprocess inside
        ``self.suspend()`` so Textual restores cooked terminal
        mode for the editor and re-enters the alternate screen on
        exit.

        Reused by both the gap-row Enter handler and the Settings
        panel's "open absentia.toml" action.
        """
        if self._on_open_editor is not None:
            # Embedded mode (e.g. Dev-Dashboard panel) — host owns the
            # editor surface; we just forward the file + line.
            try:
                self._on_open_editor(target, line)
            except Exception as exc:
                self.notify(
                    f"Editor callback failed: {exc}",
                    severity="error",
                )
            return

        # Standalone mode — spawn $EDITOR via subprocess.
        editor = os.environ.get("EDITOR") or "vi"
        cmd = editor_command(editor, target, line)
        try:
            with self.suspend():
                subprocess.run(cmd, check=False)
        except FileNotFoundError:
            self.notify(
                f"Editor '{cmd[0]}' not found in $PATH",
                severity="error",
            )


def run_tui(
    root: Path,
    config: Config,
    on_open_editor: OpenEditorCallback | None = None,
    jobs: int | None = None,
) -> int:
    """Acquire the state lock and run the TUI.

    Hosts that embed  absentia can pass ``on_open_editor`` to redirect
    Enter / open-in-editor actions to their own editor surface
    (e.g. a Dev-Dashboard ``code_editor`` panel) instead of spawning
    ``$EDITOR`` via subprocess.

    ``jobs`` overrides the default single-process scan inside the
    TUI. Passed through to ``AbsentiaApp._do_scan`` → ``scan_corpus``.
    Defaults to ``None`` which the app interprets as "use 1" — see
    the comment in ``_do_scan`` for the macOS spawn-mode caveat.
    """
    state_dir = root / ".absentia"
    try:
        with StateLock(state_dir / "lockfile"):
            AbsentiaApp(
                root=root, config=config,
                on_open_editor=on_open_editor, jobs=jobs,
            ).run()
    except StateLockError as exc:
        print(f"absentia: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
