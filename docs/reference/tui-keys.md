# TUI Keybindings

The same reference is available in-app via `?`.

## Views

| Key | Action |
|---|---|
| `1` | Gaps — what to fix |
| `2` | Rules — what conventions exist |
| `3` | Groups — every formed group + its members |
| `4` | Stats — one-screen scan summary |

## Navigation

| Key | Action |
|---|---|
| `j` / `↓` | Next row (inherited from Textual's DataTable defaults) |
| `k` / `↑` | Previous row (inherited from Textual's DataTable defaults) |
| `Enter` | Open selected entity in `$EDITOR` |
| `f` | Follow link to related view (gap → rule → group) |
| `Esc` | Back (pops navigation stack) |

## Actions

| Key | Action |
|---|---|
| `/` | Filter current view (substring or `kind=class` / `conf>=0.9`) |
| `e` | Explain — why was this gap flagged? Pops a peek modal with the rule, conformer examples, and the divergence. Does **not** navigate. Press `s` inside the modal to chain straight into Suppress for the same gap. |
| `s` | Suppress selected gap with a reason. Also works inside the `e` modal — no need to close it first. |
| `x` | Export scan results to disk. Opens a modal listing the six formats (Markdown / HTML / Text / JSON / CSV / SARIF — same as the CLI's post-check prompt); pick one with a number key. Writes to `<default_export_path>/docs/absentia/<corpus>/gaps-<UTC-ts>.<ext>`. The default path comes from `~/.absentia/settings.json`; set it once via the CLI's export prompt before using `x` in the TUI. |
| `Ctrl+R` | Rescan now |
| `w` | Toggle watch mode (auto-rescan every 2s) |

`e` vs `f`: `e` is a peek (modal opens, modal closes, you keep your spot).
`f` is a drill (navigates to the rule view; `Esc` walks back through the
breadcrumb).

## Global

| Key | Action |
|---|---|
| `?` | Help (this reference) |
| `q` | Quit |
