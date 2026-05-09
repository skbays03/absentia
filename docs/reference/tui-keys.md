# TUI Keybindings

The same reference is available in-app via `?`.

## Views

| Key | Action |
|---|---|
| `1` | Gaps — what to fix |
| `2` | Rules — what conventions exist |
| `3` | Groups — every formed group + its members |
| `4` | Stats — one-screen scan summary |
| `5` | Suppressions — every active suppression (local DB + project `[[suppress]]` blocks from `absentia.toml`, marked read-only). Press `r` on a row to remove. |

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
| `S` | Cycle sort key for the current view. Gaps: `conf↓ → conf↑ → file → entity`; Rules: `conf↓ → support↓ → group`; Groups: `members↓ → members↑ → name`. The active sort appears in the subtitle. |
| `Space` | Toggle multi-select on the cursor row (Gaps + Suppressions views). Selected rows show a yellow `▣`. Subsequent `s` (gaps) / `r` (suppressions) operate on the whole selection if non-empty, else the cursor row. |
| `r` | Remove the suppression(s) on the cursor row or selection in the Suppressions view, then rescan so the unsuppressed gaps reappear. Project-source suppressions are read-only — edit `absentia.toml` via `,` → `e` instead. |
| `x` | Export scan results to disk. Opens a modal listing the six formats (Markdown / HTML / Text / JSON / CSV / SARIF — same as the CLI's post-check prompt); pick one with a number key, then a Custom / Default location, then either type a path or use the saved default. Writes to `<base>/docs/absentia/<corpus>/gaps-<UTC-ts>.<ext>`. |
| `,` | Open the settings panel: edit `jobs_default`, `default_export_path`, reset the first-run intro hint, or hand off to `$EDITOR` for this project's `absentia.toml`. Persists to `~/.absentia/settings.json`. |
| `Ctrl+R` | Rescan now |
| `w` | Toggle watch mode (auto-rescan every 2s) |

`e` vs `f`: `e` is a peek (modal opens, modal closes, you keep your spot).
`f` is a drill (navigates to the rule view; `Esc` walks back through the
breadcrumb).

## Global

| Key | Action |
|---|---|
| `?` | Help (this reference) |
| `Ctrl+P` | Command palette — fuzzy-search every TUI action by name. Type to filter, ↑/↓ to navigate, Enter to dispatch. Each entry shows its native keystroke so the palette doubles as a discoverability surface for unfamiliar shortcuts. |
| `q` | Quit |
