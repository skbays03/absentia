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


class SuppressScreen(ModalScreen[tuple[str, str] | None]):
    """Prompt for a suppression reason. Returns ``(short_id, reason)``."""

    DEFAULT_CSS = """
    SuppressScreen { align: center middle; }
    #dialog {
        width: 70; height: 11;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #dialog Label { margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, gap_short_id: str, missing: str) -> None:
        super().__init__()
        self._gap_short_id = gap_short_id
        self._missing = missing

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"Suppress [bold cyan]{self._gap_short_id}[/]   "
                f"({self._missing})"
            )
            yield Label("Reason  (Enter saves, Esc cancels):")
            yield Input(placeholder="Why this gap is intentional…",
                        id="reason_input")

    def on_mount(self) -> None:
        self.query_one("#reason_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        if reason:
            self.dismiss((self._gap_short_id, reason))

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilterScreen(ModalScreen[str | None]):
    """Prompt for a filter expression. Returns the typed string or None."""

    DEFAULT_CSS = """
    FilterScreen { align: center middle; }
    #dialog {
        width: 70; height: 7;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    """

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
                "  1  Gaps      4  Stats\n"
                "  2  Rules\n"
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
                "  x            export scan results (md/html/txt/\n"
                "               json/csv/sarif) to default_export_path\n"
                "  Ctrl+R       rescan now\n"
                "  w            toggle watch (auto-rescan)\n\n"
                "[b]Global[/]\n"
                "  ?            this help\n"
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

    Cannot be dismissed manually — the scan owns the lifecycle.
    """

    DEFAULT_CSS = """
    LoadingScreen { align: center middle; }
    #loading_dialog {
        width: 76; height: 12;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #loading_title { margin-bottom: 1; }
    """

    # Empty BINDINGS: the user can't dismiss the loader; the scan
    # tears it down when it finishes. Quitting still works because
    # AbsentiaApp's `q` binding fires from the parent screen.
    BINDINGS = []

    _STAGES = ("walk", "parse", "store", "mine", "finalize")
    _STAGE_LABELS = {
        "walk":     "Walking corpus",
        "parse":    "Scanning files",
        "store":    "Loading store",
        "mine":     "Mining rules",
        "finalize": "Finalizing",
    }

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._states: dict[str, str] = {s: "pending" for s in self._STAGES}
        self._details: dict[str, str] = {s: "" for s in self._STAGES}
        self._parse_done = 0
        self._parse_total = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading_dialog"):
            yield Label(
                f"absentia · scanning [b cyan]{self._root.name}[/]",
                id="loading_title",
            )
            yield Static("", id="stage_list")

    def on_mount(self) -> None:
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
        lines: list[str] = []
        for stage in self._STAGES:
            label = self._STAGE_LABELS[stage]
            state = self._states[stage]
            if state == "pending":
                glyph = "[dim]○[/]"
            elif state == "active":
                glyph = "[yellow]◐[/]"
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
    /* Make the textbox visually obvious — a bordered, taller field
       so the user sees their typed characters land somewhere
       distinct from the surrounding labels. */
    #path_input {
        border: solid $accent;
        background: $boost;
        height: 3;
    }
    """

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
    "gaps":   "Gaps",
    "rules":  "Rules",
    "groups": "Groups",
    "stats":  "Stats",
}


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
    DataTable { height: 65%; }
    #detail {
        height: 1fr;
        padding: 1 2;
        border: solid $accent;
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
        Binding("s", "suppress", "Suppress"),
        Binding("e", "explain", "Explain"),
        Binding("x", "export", "Export"),
        Binding("f", "follow", "Follow"),
        Binding("escape", "back", "Back"),
        Binding("slash", "filter", "Filter"),
        Binding("w", "toggle_watch", "Watch"),
        Binding("question_mark", "help", "Help"),
        Binding("enter", "open_in_editor", "Open"),
    ]

    def __init__(
        self,
        root: Path,
        config: Config,
        on_open_editor: OpenEditorCallback | None = None,
    ) -> None:
        super().__init__()
        self.root = root
        self.config = config
        self._on_open_editor = on_open_editor
        self._gaps: list[Gap] = []
        self._rules: list[Rule] = []
        self._rules_by_id: dict[str, Rule] = {}
        self._groups: list[Group] = []
        self._groups_by_id: dict[str, Group] = {}
        self._entities: dict[str, Entity] = {}
        self._feature_index: dict = {}
        self._scan_stats: dict = {}
        self._view: str = "gaps"
        self._filter: dict[str, str] = {"gaps": "", "rules": "", "groups": ""}
        self._nav_stack: list[tuple[str, str]] = []
        from textual.timer import Timer
        self._watch_timer: Timer | None = None
        # Stashes the (menu_id, name, ext, fn_name) tuple while the
        # x-export flow walks through Format → Location → Path
        # modals; cleared when the chain finishes (success, cancel,
        # or write failure).
        self._pending_export_fmt: tuple[int, str, str, str] | None = None

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

            # jobs=1 inside the TUI is intentional: spawn-mode
            # ProcessPoolExecutor (the macOS multiprocessing default)
            # doesn't play nicely with Textual's running event loop —
            # the spawn child's fd validation surfaces as `bad value(s)
            # in fds_to_keep`. Mac users would hit this on any non-
            # trivial corpus. Single-process scans avoid the issue
            # entirely; the CLI path (`absentia check`) still gets
            # full parallelism. Most TUI scans are incremental anyway,
            # so should_parallelize would skip the pool even at higher
            # jobs.
            try:
                result = scan_corpus(
                    root=self.root,
                    state_dir=self.root / ".absentia",
                    config=self.config,
                    jobs=1,
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
        self.sub_title = (
            f"[{_VIEW_LABELS[self._view]}] · "
            f"{s.get('entities_scanned', 0)} entities · "
            f"{s.get('rules', 0)} rules · "
            f"{len(self._gaps)} gaps{sup}{watch}"
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

    # ── Gaps view ─────────────────────────────────────────────────────

    def _filtered_gaps(self) -> list[Gap]:
        f = self._filter.get("gaps", "").lower()
        if not f:
            return self._gaps
        out = []
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
                out.append(g)
        return out

    def _render_gaps_table(self, table: DataTable) -> None:
        from rich.text import Text

        table.add_columns(
            "●", "Location", "Entity", "Missing", "Conf", "ID",
        )
        gaps = self._filtered_gaps()
        flt = self._filter.get("gaps", "")
        for gap in gaps:
            rule = self._rules_by_id[gap.rule_id]
            entity = self._entities[gap.entity_id]
            short = entity.qualified_name.split("::", 1)[-1]

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
                dot, location, entity_cell, missing_cell,
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
        if not f:
            return self._rules
        out = []
        for r in self._rules:
            haystack = " ".join([r.id, r.group_id, r.feature_value,
                                 f"{r.confidence:.2f}"]).lower()
            if f in haystack:
                out.append(r)
        return out

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
        with_text = "  ".join(f"✓ {m}" for m in members_with[:9])
        if len(members_with) > 9:
            with_text += f"   …(+{len(members_with) - 9} more)"
        without_text = "  ".join(f"✗ {m}" for m in members_without[:9])
        if len(members_without) > 9:
            without_text += f"   …(+{len(members_without) - 9} more)"
        self._set_detail(
            f"[b cyan]{rule.id}[/]\n\n"
            f"[b]Group[/]    {rule.group_id}\n"
            f"[b]Pattern[/]  {rule.feature_kind} = "
            f"[cyan]{rule.feature_value}[/]\n"
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
        if not f:
            return self._groups
        return [
            g for g in self._groups
            if f in g.id.lower() or f in g.selector_type.lower()
        ]

    def _render_groups_table(self, table: DataTable) -> None:
        table.add_columns("Group", "Selector", "Members", "Rules")
        groups = sorted(self._filtered_groups(), key=lambda g: -len(g.members))
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
        rules_for = [r for r in self._rules if r.group_id == group.id]
        member_names = [m.rsplit("::", 1)[-1] for m in group.members[:20]]
        more = (
            f"   …(+{len(group.members) - 20} more)"
            if len(group.members) > 20 else ""
        )
        rules_text = (
            "\n".join(
                f"  {r.feature_kind} = {r.feature_value}   "
                f"{r.support_n}/{r.support_total} ({r.confidence:.2f})"
                for r in rules_for
            ) or "  (none — no feature reached the confidence threshold)"
        )
        self._set_detail(
            f"[b cyan]{group.id}[/]\n\n"
            f"[b]Selector[/]  {group.selector_type}\n"
            f"[b]Members[/]   {len(group.members)}\n"
            f"  {'  '.join(member_names)}{more}\n\n"
            f"[b]Rules ({len(rules_for)})[/]:\n{rules_text}\n\n"
            f"[b]f[/] to follow to first member · [b]Esc[/] back"
        )

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
        if event.row_key is None or event.row_key.value is None:
            return
        rk = event.row_key.value
        if self._view == "gaps":
            for gap in self._gaps:
                if gap.short_id == rk:
                    self._render_gap_detail(gap)
                    return
        elif self._view == "rules":
            rule = self._rules_by_id.get(rk)
            if rule is not None:
                self._render_rule_detail(rule)
        elif self._view == "groups":
            group = self._groups_by_id.get(rk)
            if group is not None:
                self._render_group_detail(group)

    def _render_gap_detail(self, gap: Gap) -> None:
        rule = self._rules_by_id[gap.rule_id]
        entity = self._entities[gap.entity_id]
        self._set_detail(
            f"[b cyan]{gap.short_id}[/]   ([dim]{gap.id}[/])\n\n"
            f"[b]Entity[/]   {entity.qualified_name}\n"
            f"         {entity.file_path}:{entity.line}   "
            f"[dim]\\[{entity.kind}][/]\n\n"
            f"[b]Rule[/]     {rule.id}\n"
            f"         {rule.support_n}/{rule.support_total} members of "
            f"[yellow]{rule.group_id}[/] have "
            f"[cyan]{rule.feature_value}[/]\n"
            f"         confidence [b]{rule.confidence:.2f}[/]\n\n"
            f"[b]Verdict[/]  this entity does not have "
            f"[cyan]{rule.feature_value}[/].\n"
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
        if self._view != "gaps":
            self.notify("Suppress only applies to gaps.")
            return
        sel = self._selected_id()
        if sel is None:
            return
        gap = next((g for g in self._gaps if g.short_id == sel), None)
        if gap is None:
            return
        rule = self._rules_by_id[gap.rule_id]
        self.push_screen(
            SuppressScreen(gap.short_id, f"missing {rule.feature_value}"),
            self._suppress_done,
        )

    def _suppress_done(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        short_id, reason = result
        gap = next((g for g in self._gaps if g.short_id == short_id), None)
        full_id = gap.id if gap else None
        try:
            with Storage(self.root / ".absentia") as storage:
                storage.add_suppression(
                    short_id=short_id, full_id=full_id, reason=reason,
                )
        except StorageVersionError as exc:
            self.notify(f"Storage error: {exc}", severity="error")
            return
        self.notify(f"Suppressed {short_id}")
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
        target = self.root / entity.file_path
        if self._on_open_editor is not None:
            # Embedded mode (e.g. Dev-Dashboard panel) — host owns the
            # editor surface; we just forward the file + line.
            try:
                self._on_open_editor(target, entity.line)
            except Exception as exc:
                self.notify(
                    f"Editor callback failed: {exc}",
                    severity="error",
                )
            return

        # Standalone mode — spawn $EDITOR via subprocess.
        editor = os.environ.get("EDITOR") or "vi"
        cmd = editor_command(editor, target, entity.line)
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
) -> int:
    """Acquire the state lock and run the TUI.

    Hosts that embed  absentia can pass ``on_open_editor`` to redirect
    Enter / open-in-editor actions to their own editor surface
    (e.g. a Dev-Dashboard ``code_editor`` panel) instead of spawning
    ``$EDITOR`` via subprocess.
    """
    state_dir = root / ".absentia"
    try:
        with StateLock(state_dir / "lockfile"):
            AbsentiaApp(
                root=root, config=config, on_open_editor=on_open_editor,
            ).run()
    except StateLockError as exc:
        print(f"absentia: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
