"""Textual app for lacuna's TUI.

MVP scope:

  - One view: gaps list + detail pane
  - Header (title + scan stats)
  - Footer (key bindings)
  - q to quit, Ctrl+R to rescan
  - s to suppress (modal: enter reason, Enter saves, Esc cancels)
  - Enter to open the gap's file in $EDITOR

Out of scope for now (planned for follow-up):

  - Rules / Groups / Stats views
  - Live filter (``/``)
  - Watch mode (auto re-mine on file change)
  - Help overlay (footer covers it for now)
  - Cross-reference jumps (``f``)

The data layer is the same one ``lacuna check`` consumes — see
``lacuna.cli.scan_corpus``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from ..config import Config
from ..mining import Gap, Rule
from ..entities import Entity
from ..storage import StateLock, StateLockError, Storage


class SuppressScreen(ModalScreen[tuple[str, str] | None]):
    """Modal that prompts for a suppression reason.

    Returns ``(short_id, reason)`` on save, or ``None`` on cancel.
    """

    DEFAULT_CSS = """
    SuppressScreen {
        align: center middle;
    }
    #dialog {
        width: 70;
        height: 11;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #dialog Label {
        margin-bottom: 1;
    }
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


class LacunaApp(App[None]):
    """The main lacuna TUI."""

    DEFAULT_CSS = """
    Screen {
        background: $surface;
    }
    DataTable {
        height: 65%;
    }
    #detail {
        height: 1fr;
        padding: 1 2;
        border: solid $accent;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+r", "rescan", "Rescan"),
        Binding("s", "suppress", "Suppress"),
        Binding("enter", "open_in_editor", "Open in $EDITOR"),
    ]

    def __init__(self, root: Path, config: Config) -> None:
        super().__init__()
        self.root = root
        self.config = config
        self._gaps: list[Gap] = []
        self._rules: dict[str, Rule] = {}
        self._entities: dict[str, Entity] = {}
        self._scan_stats: dict = {}

    # ── Layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield DataTable(id="gaps_table",
                        cursor_type="row",
                        zebra_stripes=True)
        yield Static("(loading scan…)", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"lacuna — {self.root.name}"
        self.sub_title = str(self.root)

        table = self.query_one("#gaps_table", DataTable)
        table.add_columns("Location", "Entity", "Missing", "Conf", "ID")

        self._do_scan()

    # ── Scan + render ────────────────────────────────────────────────

    def _do_scan(self) -> None:
        from ..cli import scan_corpus

        try:
            result = scan_corpus(
                root=self.root,
                state_dir=self.root / ".lacuna",
                config=self.config,
            )
        except Exception as exc:  # any scan failure should not crash the TUI
            self.notify(f"Scan failed: {exc}", severity="error", timeout=8)
            return

        self._gaps = result["gaps"]
        self._rules = result["rules_by_id"]
        self._entities = result["entities"]
        self._scan_stats = result["scan_stats"]
        self._refresh_table()
        self._update_subtitle()

    def _refresh_table(self) -> None:
        table = self.query_one("#gaps_table", DataTable)
        table.clear()
        for gap in self._gaps:
            rule = self._rules[gap.rule_id]
            entity = self._entities[gap.entity_id]
            loc = f"{entity.file_path}:{entity.line}"
            short = entity.qualified_name.split("::", 1)[-1]
            entity_label = f"{entity.kind} `{short}`"
            table.add_row(
                loc,
                entity_label,
                f"missing {rule.feature_value}",
                f"{rule.confidence:.2f}",
                gap.short_id,
                key=gap.short_id,
            )
        if self._gaps:
            self._render_detail_for_index(0)
        else:
            self.query_one("#detail", Static).update(
                "[b green]No gaps.[/]\n\n"
                "Either lacuna found nothing wrong, or every divergence "
                "has been suppressed. Press [b]Ctrl+R[/] to rescan."
            )

    def _update_subtitle(self) -> None:
        s = self._scan_stats
        unchanged = s.get("files_unchanged", 0)
        cache = f" ({unchanged} unchanged)" if unchanged else ""
        suppressed = s.get("suppressed", 0)
        sup = f" · {suppressed} suppressed" if suppressed else ""
        self.sub_title = (
            f"{s.get('entities_scanned', 0)} entities · "
            f"{s.get('rules', 0)} rules · "
            f"{len(self._gaps)} gaps{sup} · "
            f"{s.get('duration_ms', 0):.0f}ms{cache}"
        )

    # ── Selection / detail ───────────────────────────────────────────

    def _selected_gap(self) -> Gap | None:
        table = self.query_one("#gaps_table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        if row_key is None:
            return None
        for gap in self._gaps:
            if gap.short_id == row_key.value:
                return gap
        return None

    def _render_detail_for_index(self, idx: int) -> None:
        if not (0 <= idx < len(self._gaps)):
            return
        self._render_detail(self._gaps[idx])

    def _render_detail(self, gap: Gap) -> None:
        rule = self._rules[gap.rule_id]
        entity = self._entities[gap.entity_id]
        text = (
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
            f"         [b]s[/] to suppress · "
            f"[b]Enter[/] to open · "
            f"[b]Ctrl+R[/] to rescan"
        )
        self.query_one("#detail", Static).update(text)

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted,
    ) -> None:
        if event.row_key is None:
            return
        for gap in self._gaps:
            if gap.short_id == event.row_key.value:
                self._render_detail(gap)
                return

    # ── Actions ───────────────────────────────────────────────────────

    def action_rescan(self) -> None:
        self.notify("Rescanning…")
        self._do_scan()

    def action_suppress(self) -> None:
        gap = self._selected_gap()
        if gap is None:
            return
        rule = self._rules[gap.rule_id]
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
            with Storage(self.root / ".lacuna") as storage:
                storage.add_suppression(
                    short_id=short_id, full_id=full_id, reason=reason,
                )
        except StorageVersionError as exc:  # noqa: F821 - imported lazily
            self.notify(f"Storage error: {exc}", severity="error")
            return
        self.notify(f"Suppressed {short_id}", severity="information")
        self._do_scan()

    def action_open_in_editor(self) -> None:
        gap = self._selected_gap()
        if gap is None:
            return
        entity = self._entities[gap.entity_id]
        editor = os.environ.get("EDITOR") or "vi"
        target = self.root / entity.file_path
        # +N opens at line N for vi/vim/neovim/nano. Editors that don't
        # honour the flag ignore it harmlessly.
        try:
            with self.suspend():
                subprocess.run(
                    [editor, f"+{entity.line}", str(target)],
                    check=False,
                )
        except FileNotFoundError:
            self.notify(
                f"Editor '{editor}' not found in $PATH",
                severity="error",
            )


# Lazy import so the module loads without textual installed (tests without TUI)
try:
    from ..storage import StorageVersionError  # noqa: F401
except Exception:
    pass


def run_tui(root: Path, config: Config) -> int:
    """Entry point: acquire the state lock, launch the app, return its
    exit code."""
    state_dir = root / ".lacuna"
    try:
        with StateLock(state_dir / "lockfile"):
            LacunaApp(root=root, config=config).run()
    except StateLockError as exc:
        print(f"lacuna: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
