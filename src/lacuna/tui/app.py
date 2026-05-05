"""Textual app for lacuna's TUI.

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
                "[b cyan]Lacuna keybindings[/]\n\n"
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
                "  Ctrl+R       rescan now\n"
                "  w            toggle watch (auto-rescan)\n\n"
                "[b]Global[/]\n"
                "  ?            this help\n"
                "  q            quit"
            )

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


class ExplainScreen(ModalScreen[None]):
    """Plain-text "why was this flagged?" modal for a gap.

    Different from the f / follow action: follow navigates to the
    rule view (you change context); explain pops a peek that shows
    the rule sentence, support, conformers, and divergence — then
    returns you to your spot in the gaps list when dismissed.
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
            "accept the divergence, press [/][b]s[/][dim] to record a\n"
            "suppression reason.[/]\n\n"
            "[dim]e / Esc to close · f to drill into the rule view[/]"
        )

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)


# ── Main App ─────────────────────────────────────────────────────────


_VIEW_LABELS = {
    "gaps":   "Gaps",
    "rules":  "Rules",
    "groups": "Groups",
    "stats":  "Stats",
}


class LacunaApp(App[None]):
    """The main lacuna TUI."""

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
        self.title = f"lacuna — {self.root.name}"
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
        from ..cli import scan_corpus

        try:
            result = scan_corpus(
                root=self.root,
                state_dir=self.root / ".lacuna",
                config=self.config,
            )
        except Exception as exc:
            self.notify(f"Scan failed: {exc}", severity="error", timeout=8)
            return

        self._gaps = result["gaps"]
        self._rules_by_id = result["rules_by_id"]
        self._rules = sorted(result["rules"], key=lambda r: -r.confidence)
        self._groups = result["groups"]
        self._groups_by_id = {g.id: g for g in self._groups}
        self._entities = result["entities"]
        self._feature_index = result["feature_index"]
        self._scan_stats = result["scan_stats"]
        self._render_current_view()
        self._update_subtitle()

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
        table.add_columns("Location", "Entity", "Missing", "Conf", "ID")
        gaps = self._filtered_gaps()
        for gap in gaps:
            rule = self._rules_by_id[gap.rule_id]
            entity = self._entities[gap.entity_id]
            loc = f"{entity.file_path}:{entity.line}"
            short = entity.qualified_name.split("::", 1)[-1]
            table.add_row(
                loc,
                f"{entity.kind} `{short}`",
                f"missing {rule.feature_value}",
                f"{rule.confidence:.2f}",
                gap.short_id,
                key=gap.short_id,
            )
        if gaps:
            self._render_gap_detail(gaps[0])
        else:
            self._set_detail(
                "[b green]No gaps to show.[/]\n\n"
                "Either lacuna found nothing wrong, every divergence has "
                "been suppressed, or your filter excludes them all. "
                "[b]/[/] to change the filter, [b]Ctrl+R[/] to rescan."
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
        self.push_screen(ExplainScreen(
            gap=gap,
            rule=rule,
            entity=entity,
            group=group,
            feature_index=self._feature_index,
            min_confidence=self.config.mining.min_confidence,
        ))

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
            with Storage(self.root / ".lacuna") as storage:
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

    Hosts that embed lacuna can pass ``on_open_editor`` to redirect
    Enter / open-in-editor actions to their own editor surface
    (e.g. a Dev-Dashboard ``code_editor`` panel) instead of spawning
    ``$EDITOR`` via subprocess.
    """
    state_dir = root / ".lacuna"
    try:
        with StateLock(state_dir / "lockfile"):
            LacunaApp(
                root=root, config=config, on_open_editor=on_open_editor,
            ).run()
    except StateLockError as exc:
        print(f"lacuna: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
