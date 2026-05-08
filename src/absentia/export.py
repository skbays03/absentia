"""Post-check export of scan results.

Triggered interactively from ``cmd_check`` when stdout/stdin are
TTYs and the run isn't ``--json`` / ``--quiet``. The user is asked
whether to export, then picks a format and a destination — output
lands at:

    <base>/docs/absentia/<corpus_name>/gaps-<UTC-timestamp>.<ext>

Where ``<base>`` is either a custom path the user types in, or the
default stored as ``default_export_path`` in
``~/.absentia/settings.json`` (set on first use).

Six formats ship today:

  1. Markdown      — review-friendly; pastes into PRs and issues.
  2. HTML          — print-ready CSS for Cmd/Ctrl+P → Save as PDF.
  3. Text          — plain ASCII; pipes/diffs cleanly.
  4. JSON          — machine-readable; same shape as ``--json`` plus
                     metadata wrapper.
  5. CSV           — one row per gap; sorts/filters in spreadsheets.
  6. SARIF         — SARIF 2.1.0; consumed natively by GitHub Code
                     Scanning, IntelliJ, VS Code, and most CI/IDE
                     tooling.

All renderers return strings; the caller writes the file. That
makes them straightforward to unit-test (no file I/O) and lets
the caller choose the right encoding boundary.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from ._console import stderr_console, stdout_console
from .entities import Entity
from .mining import Gap, Rule
from .settings import load_settings, save_settings


# (menu_id, name, file_extension, renderer_name)
# renderer_name is looked up in this module's globals at dispatch
# time so the table stays a plain data literal.
_FORMATS: tuple[tuple[int, str, str, str], ...] = (
    (1, "Markdown", "md", "render_markdown"),
    (2, "HTML",     "html", "render_html"),
    (3, "Text",     "txt", "render_text"),
    (4, "JSON",     "json", "render_json"),
    (5, "CSV",      "csv", "render_csv"),
    (6, "SARIF",    "sarif.json", "render_sarif"),
)


def build_export_path(
    base: Path,
    corpus_name: str,
    extension: str,
    timestamp: datetime | None = None,
) -> Path:
    """Construct ``<base>/docs/absentia/<corpus_name>/gaps-<ts>.<ext>``.

    Timestamp defaults to ``datetime.now(timezone.utc)`` and is
    formatted as filename-safe ISO 8601 (``%Y-%m-%dT%H-%M-%S``;
    colons replaced by hyphens so the path works on Windows too).
    """
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"gaps-{ts_str}.{extension}"
    return (base / "docs" / "absentia" / corpus_name / fname).resolve()


# ── Renderers ────────────────────────────────────────────────────


def _meta_dict(
    *,
    root: Path,
    scan_stats: dict[str, Any],
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
) -> dict[str, Any]:
    """Assemble the scan-metadata header all renderers share."""
    return {
        "absentia_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scan_started_at": scan_stats.get("started_at"),
        "scan_duration_ms": scan_stats.get("duration_ms"),
        "root": str(root),
        "files_seen": scan_stats.get("files_seen"),
        "entities_scanned": scan_stats.get("entities_scanned"),
        "groups": scan_stats.get("groups"),
        "rules_total": scan_stats.get("rules"),
        "gaps_total": len(gaps),
        "suppressed": scan_stats.get("suppressed"),
        "min_confidence": scan_stats.get("min_confidence"),
        "min_group_size": scan_stats.get("min_group_size"),
        "rules_referenced": len({g.rule_id for g in gaps}),
    }


def render_markdown(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    meta = _meta_dict(
        root=root, scan_stats=scan_stats, gaps=gaps, rules_by_id=rules_by_id,
    )
    lines: list[str] = []
    lines.append(f"# absentia check — `{root.name or 'scan'}`")
    lines.append("")
    lines.append(
        f"- **Scanned**: `{meta['root']}` at {meta['scan_started_at']}"
    )
    lines.append(
        f"- **Files**: {meta['files_seen']:,} · "
        f"**Entities**: {meta['entities_scanned']:,} · "
        f"**Groups**: {meta['groups']:,}"
    )
    lines.append(
        f"- **Result**: {meta['gaps_total']:,} gaps · "
        f"{meta['rules_total']:,} rules total "
        f"({meta['rules_referenced']:,} referenced by gaps) · "
        f"{meta['suppressed']:,} suppressed"
    )
    lines.append(
        f"- **Duration**: {(meta['scan_duration_ms'] or 0) / 1000:.2f} s · "
        f"**absentia** v{meta['absentia_version']}"
    )
    lines.append("")

    if not gaps:
        lines.append("**No gaps.** absentia found nothing wrong.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Gaps")
    lines.append("")
    lines.append(
        "| ID | Location | Entity | Missing | Confidence |"
    )
    lines.append("|---|---|---|---|---:|")
    for gap in gaps:
        rule = rules_by_id[gap.rule_id]
        entity = entities[gap.entity_id]
        loc = f"`{entity.file_path}:{entity.line}`"
        short = entity.qualified_name.split("::", 1)[-1]
        ent_cell = f"`{entity.kind}` `{short}`"
        miss = f"`{rule.feature_value}` ({rule.feature_kind})"
        lines.append(
            f"| `{gap.short_id}` | {loc} | {ent_cell} | {miss} "
            f"| {rule.confidence:.2f} |"
        )
    lines.append("")

    lines.append("## Rules referenced by gaps")
    lines.append("")
    lines.append("| Rule | Group | Confidence | Support |")
    lines.append("|---|---|---:|---:|")
    referenced_rule_ids = {g.rule_id for g in gaps}
    for rule_id in sorted(referenced_rule_ids):
        ref_rule = rules_by_id.get(rule_id)
        if ref_rule is None:
            continue
        lines.append(
            f"| `{ref_rule.feature_value}` ({ref_rule.feature_kind}) "
            f"| `{ref_rule.group_id}` "
            f"| {ref_rule.confidence:.2f} "
            f"| {ref_rule.support_n}/{ref_rule.support_total} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_html(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    """Print-friendly HTML. Designed for browser → Save as PDF.

    Single self-contained file: inline ``<style>``, no external
    assets, ``@media print`` rules for page breaks, monospace for
    paths and code, A4-friendly margins by default (overridable in
    the print dialog). No JavaScript.
    """
    from html import escape

    meta = _meta_dict(
        root=root, scan_stats=scan_stats, gaps=gaps, rules_by_id=rules_by_id,
    )

    style = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
           sans-serif; line-height: 1.5; max-width: 1100px; margin: 2em auto;
           padding: 0 2em; color: #1a1a1a; }
    h1 { border-bottom: 2px solid #444; padding-bottom: 0.3em; }
    h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.2em; margin-top: 2em; }
    code, .mono { font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
           font-size: 0.92em; background: #f4f4f4; padding: 0.1em 0.35em;
           border-radius: 3px; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; }
    th, td { padding: 0.45em 0.7em; border-bottom: 1px solid #ddd;
           text-align: left; vertical-align: top; }
    th { background: #f4f4f4; font-weight: 600; }
    .summary { background: #f8f8f8; border-left: 4px solid #888;
           padding: 0.8em 1em; margin: 1em 0; }
    .conf-high { color: #b00; font-weight: 600; }
    .conf-mid  { color: #c80; }
    .conf-low  { color: #888; }
    .meta-row { font-size: 0.92em; color: #555; }
    @media print {
        body { max-width: none; margin: 0; padding: 1cm; font-size: 10pt; }
        h2 { page-break-before: auto; page-break-after: avoid; }
        tr { page-break-inside: avoid; }
        a { color: inherit; text-decoration: none; }
    }
    """.strip()

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="UTF-8">')
    parts.append(
        f"<title>absentia check — {escape(root.name or 'scan')}</title>"
    )
    parts.append(f"<style>{style}</style>")
    parts.append("</head><body>")

    parts.append(
        f"<h1>absentia check — <code>{escape(root.name or 'scan')}</code></h1>"
    )
    parts.append('<div class="summary">')
    parts.append(
        f'<div><strong>Scanned</strong>: <code>{escape(meta["root"])}</code> '
        f'at {escape(meta["scan_started_at"] or "")}</div>'
    )
    parts.append(
        f'<div class="meta-row"><strong>Files</strong>: {meta["files_seen"]:,} '
        f'· <strong>Entities</strong>: {meta["entities_scanned"]:,} '
        f'· <strong>Groups</strong>: {meta["groups"]:,}</div>'
    )
    parts.append(
        f'<div class="meta-row"><strong>Result</strong>: '
        f'{meta["gaps_total"]:,} gaps · '
        f'{meta["rules_total"]:,} rules total '
        f'({meta["rules_referenced"]:,} referenced by gaps) · '
        f'{meta["suppressed"]:,} suppressed</div>'
    )
    parts.append(
        f'<div class="meta-row"><strong>Duration</strong>: '
        f'{(meta["scan_duration_ms"] or 0) / 1000:.2f} s · '
        f'<strong>absentia</strong> v{meta["absentia_version"]}</div>'
    )
    parts.append("</div>")

    if not gaps:
        parts.append(
            "<p><strong>No gaps.</strong> absentia found nothing wrong.</p>"
        )
        parts.append("</body></html>")
        return "\n".join(parts) + "\n"

    parts.append("<h2>Gaps</h2>")
    parts.append("<table>")
    parts.append(
        "<thead><tr><th>ID</th><th>Location</th><th>Entity</th>"
        "<th>Missing</th><th>Confidence</th></tr></thead><tbody>"
    )
    for gap in gaps:
        rule = rules_by_id[gap.rule_id]
        entity = entities[gap.entity_id]
        loc = f"{entity.file_path}:{entity.line}"
        short = entity.qualified_name.split("::", 1)[-1]
        conf_class = (
            "conf-high" if rule.confidence >= 0.9
            else "conf-mid" if rule.confidence >= 0.8
            else "conf-low"
        )
        parts.append(
            f"<tr>"
            f"<td><code>{escape(gap.short_id)}</code></td>"
            f"<td><code>{escape(loc)}</code></td>"
            f"<td><code>{escape(entity.kind)}</code> "
            f"<code>{escape(short)}</code></td>"
            f"<td><code>{escape(rule.feature_value)}</code> "
            f"<span class=\"meta-row\">({escape(rule.feature_kind)})</span>"
            f"</td>"
            f'<td class="{conf_class}">{rule.confidence:.2f}</td>'
            f"</tr>"
        )
    parts.append("</tbody></table>")

    parts.append("<h2>Rules referenced by gaps</h2>")
    parts.append("<table>")
    parts.append(
        "<thead><tr><th>Rule</th><th>Group</th>"
        "<th>Confidence</th><th>Support</th></tr></thead><tbody>"
    )
    for rule_id in sorted({g.rule_id for g in gaps}):
        ref_rule = rules_by_id.get(rule_id)
        if ref_rule is None:
            continue
        parts.append(
            f"<tr>"
            f"<td><code>{escape(ref_rule.feature_value)}</code> "
            f"<span class=\"meta-row\">({escape(ref_rule.feature_kind)})</span>"
            f"</td>"
            f"<td><code>{escape(ref_rule.group_id)}</code></td>"
            f"<td>{ref_rule.confidence:.2f}</td>"
            f"<td>{ref_rule.support_n}/{ref_rule.support_total}</td>"
            f"</tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def render_text(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    """Plain ASCII — diff-friendly, no markup."""
    meta = _meta_dict(
        root=root, scan_stats=scan_stats, gaps=gaps, rules_by_id=rules_by_id,
    )
    lines: list[str] = []
    lines.append(f"absentia check — {root.name or 'scan'}")
    lines.append("=" * 60)
    lines.append(f"Scanned    : {meta['root']}")
    lines.append(f"Started    : {meta['scan_started_at']}")
    lines.append(
        f"Files      : {meta['files_seen']:,}  "
        f"Entities: {meta['entities_scanned']:,}  "
        f"Groups: {meta['groups']:,}"
    )
    lines.append(
        f"Gaps       : {meta['gaps_total']:,}  "
        f"Rules total: {meta['rules_total']:,}  "
        f"Suppressed: {meta['suppressed']:,}"
    )
    lines.append(
        f"Duration   : {(meta['scan_duration_ms'] or 0) / 1000:.2f}s  "
        f"absentia v{meta['absentia_version']}"
    )
    lines.append("")

    if not gaps:
        lines.append("No gaps. absentia found nothing wrong.")
        lines.append("")
        return "\n".join(lines)

    lines.append("GAPS")
    lines.append("-" * 60)
    for gap in gaps:
        rule = rules_by_id[gap.rule_id]
        entity = entities[gap.entity_id]
        short = entity.qualified_name.split("::", 1)[-1]
        loc = f"{entity.file_path}:{entity.line}"
        lines.append(
            f"  [{gap.short_id}] {loc:<40s}  "
            f"{entity.kind} `{short}`  missing {rule.feature_value}  "
            f"({rule.confidence:.2f})"
        )
    lines.append("")
    return "\n".join(lines)


def render_json(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    """JSON dump — same gap/rule/entity shape as ``--json`` plus
    a metadata wrapper recording when/where the export happened."""
    meta = _meta_dict(
        root=root, scan_stats=scan_stats, gaps=gaps, rules_by_id=rules_by_id,
    )
    payload = {
        "meta": meta,
        "scan": scan_stats,
        "gaps": [
            {
                "id": gap.id,
                "short_id": gap.short_id,
                "rule": {
                    "id": rule.id,
                    "group_id": rule.group_id,
                    "feature_kind": rule.feature_kind,
                    "feature_value": rule.feature_value,
                    "support_n": rule.support_n,
                    "support_total": rule.support_total,
                    "confidence": round(rule.confidence, 4),
                },
                "entity": {
                    "id": entity.id,
                    "kind": entity.kind,
                    "qualified_name": entity.qualified_name,
                    "file_path": entity.file_path,
                    "line": entity.line,
                },
            }
            for gap in gaps
            for rule in [rules_by_id[gap.rule_id]]
            for entity in [entities[gap.entity_id]]
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_csv(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    """One row per gap. Header row included so the file opens
    cleanly in Excel / Numbers / Sheets without manual mapping."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "gap_id",
        "short_id",
        "file_path",
        "line",
        "entity_kind",
        "entity_qualified_name",
        "rule_id",
        "feature_kind",
        "feature_value",
        "confidence",
        "support_n",
        "support_total",
        "group_id",
    ])
    for gap in gaps:
        rule = rules_by_id[gap.rule_id]
        entity = entities[gap.entity_id]
        writer.writerow([
            gap.id,
            gap.short_id,
            entity.file_path,
            entity.line,
            entity.kind,
            entity.qualified_name,
            rule.id,
            rule.feature_kind,
            rule.feature_value,
            f"{rule.confidence:.4f}",
            rule.support_n,
            rule.support_total,
            rule.group_id,
        ])
    return buf.getvalue()


def render_sarif(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> str:
    """SARIF 2.1.0 — consumed natively by GitHub Code Scanning,
    IntelliJ, VS Code, and most IDE/CI dashboards.

    One ``run`` with absentia as the tool driver. Each gap becomes
    a ``result`` with a ``ruleId`` + a single ``location``. Rules
    are emitted as ``tool.driver.rules`` so consumers can group /
    filter by rule and show rule descriptions inline.
    """
    referenced_rules = sorted({g.rule_id for g in gaps})
    rule_index = {rid: i for i, rid in enumerate(referenced_rules)}

    sarif_rules = []
    for rid in referenced_rules:
        rule = rules_by_id.get(rid)
        if rule is None:
            continue
        sarif_rules.append({
            "id": rid,
            "name": f"{rule.feature_kind}/{rule.feature_value}",
            "shortDescription": {
                "text": (
                    f"Members of `{rule.group_id}` typically have "
                    f"`{rule.feature_value}` ({rule.feature_kind}); "
                    f"this one doesn't."
                ),
            },
            "fullDescription": {
                "text": (
                    f"Mined as a convention from {rule.support_n} of "
                    f"{rule.support_total} members of group "
                    f"`{rule.group_id}` (confidence "
                    f"{rule.confidence:.2f})."
                ),
            },
            "defaultConfiguration": {
                # Map confidence → SARIF level. Absentia rules at the
                # default 0.8 threshold are advisory ("note"); high-
                # confidence (≥0.9) get bumped to "warning".
                "level": (
                    "warning" if rule.confidence >= 0.9 else "note"
                ),
            },
            "properties": {
                "absentia.feature_kind": rule.feature_kind,
                "absentia.feature_value": rule.feature_value,
                "absentia.support_n": rule.support_n,
                "absentia.support_total": rule.support_total,
                "absentia.confidence": round(rule.confidence, 4),
            },
        })

    results = []
    for gap in gaps:
        rule = rules_by_id.get(gap.rule_id)
        entity = entities.get(gap.entity_id)
        if rule is None or entity is None:
            continue
        ridx = rule_index.get(gap.rule_id, 0)
        results.append({
            "ruleId": gap.rule_id,
            "ruleIndex": ridx,
            "level": (
                "warning" if rule.confidence >= 0.9 else "note"
            ),
            "message": {
                "text": (
                    f"`{entity.qualified_name}` is missing "
                    f"`{rule.feature_value}` "
                    f"({rule.feature_kind}). {rule.support_n} of "
                    f"{rule.support_total} sibling members in "
                    f"`{rule.group_id}` have it (confidence "
                    f"{rule.confidence:.2f})."
                ),
            },
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": entity.file_path,
                    },
                    "region": {
                        "startLine": entity.line,
                    },
                },
            }],
            "fingerprints": {
                "absentia/v1": gap.id,
            },
            "partialFingerprints": {
                "primary": gap.short_id,
            },
        })

    payload = {
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
            "Schemata/sarif-schema-2.1.0.json"
        ),
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "absentia",
                    "version": __version__,
                    "informationUri": "https://github.com/skbays03/absentia",
                    "rules": sarif_rules,
                },
            },
            "results": results,
            "originalUriBaseIds": {
                "ROOTPATH": {
                    "uri": Path(str(scan_stats.get("root", root))).as_uri(),
                },
            },
            "invocations": [{
                "executionSuccessful": True,
                "endTimeUtc": _meta_dict(
                    root=root, scan_stats=scan_stats,
                    gaps=gaps, rules_by_id=rules_by_id,
                )["exported_at"],
            }],
        }],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# ── Interactive prompt flow ─────────────────────────────────────


def _read_choice(prompt: str, default: str = "") -> str:
    """``input()`` wrapper. Returns the default on bare Enter or
    EOF; returns "" on KeyboardInterrupt to signal cancel."""
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    return raw or default


def _resolve_base_path(
    location_choice: str,
) -> Path | None:
    """Translate the menu pick into an absolute base path.

    ``"1"`` → custom path prompt.
    ``"2"`` → default from settings.json, prompting to set one if
              none is recorded yet.

    Returns ``None`` on cancel / invalid input. Persists the
    user's first-time default-path choice via ``save_settings``.
    """
    if location_choice == "1":
        raw = _read_choice("  Custom base path: ")
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    if location_choice == "2":
        settings = load_settings()
        if settings.default_export_path is None:
            stdout_console.print(
                "  [dim]No default export path set yet.[/]"
            )
            raw = _read_choice("  Default base path to remember: ")
            if not raw:
                return None
            base = Path(raw).expanduser().resolve()
            save_settings(replace(settings, default_export_path=str(base)))
            stdout_console.print(
                "  [dim]Saved as default in[/] [cyan]"
                "~/.absentia/settings.json[/][dim].[/]"
            )
            return base
        return Path(settings.default_export_path).expanduser().resolve()

    return None


def prompt_and_export(
    *,
    root: Path,
    gaps: list[Gap],
    rules_by_id: dict[str, Rule],
    entities: dict[str, Entity],
    scan_stats: dict[str, Any],
) -> Path | None:
    """Drive the y/N → format → location → write flow.

    Returns the written ``Path`` on success, ``None`` if the user
    cancels at any step or the write fails. Failures are surfaced
    as ``Export Failed!`` in red on stderr; success as
    ``Exported to : <path>`` with the path in cyan.

    Caller is responsible for skipping this in non-interactive
    contexts (``--json``, ``--quiet``, non-TTY); this function
    does not re-check those.
    """
    # 1. Confirm
    ans = _read_choice("Export results? [y/N]: ", default="n")
    if ans.lower() not in ("y", "yes"):
        return None

    # 2. Format
    stdout_console.print()
    stdout_console.print("  Format:")
    for menu_id, name, ext, _ in _FORMATS:
        stdout_console.print(f"    {menu_id}) {name}  [dim](.{ext})[/]")
    fmt_raw = _read_choice("  Choose format [1]: ", default="1")
    fmt_entry = next(
        (f for f in _FORMATS if str(f[0]) == fmt_raw),
        None,
    )
    if fmt_entry is None:
        stderr_console.print("[red]Export Failed![/] [dim]unknown format[/]")
        return None
    _, fmt_name, fmt_ext, fmt_fn_name = fmt_entry

    # 3. Location
    stdout_console.print()
    stdout_console.print("  Location:")
    stdout_console.print("    1) Custom path")
    stdout_console.print("    2) Default path  [dim](from settings)[/]")
    loc_raw = _read_choice("  Choose location [2]: ", default="2")
    base = _resolve_base_path(loc_raw)
    if base is None:
        stderr_console.print(
            "[red]Export Failed![/] [dim]no destination chosen[/]"
        )
        return None

    # 4. Build path + render + write
    corpus_name = root.name or "scan"
    out_path = build_export_path(base, corpus_name, fmt_ext)
    try:
        renderer = globals()[fmt_fn_name]
        body = renderer(
            root=root,
            gaps=gaps,
            rules_by_id=rules_by_id,
            entities=entities,
            scan_stats=scan_stats,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
    except (OSError, KeyError, ValueError) as exc:
        stderr_console.print(
            f"[red]Export Failed![/] [dim]{exc}[/]"
        )
        return None

    stdout_console.print(
        f"\nExported to : [cyan]{out_path}[/]"
    )
    return out_path
