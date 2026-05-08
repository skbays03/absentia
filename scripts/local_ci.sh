#!/usr/bin/env bash
# Run the same checks GitHub Actions runs (.github/workflows/ci.yml),
# locally, in the same order. Used by:
#
#   - The pre-push git hook (.githooks/pre-push) — catches CI fails
#     before they hit the remote.
#   - Manual invocation: `bash scripts/local_ci.sh` — useful between
#     edits, before `git push`.
#
# Order is intentional: cheapest checks first so an iteration that
# breaks lint dies in <1 s instead of 60 s. The script exits on the
# first failing check (set -e).
#
# Skip via `git push --no-verify` (built-in) or by passing `--skip`
# as the first arg when invoking this script directly.

set -euo pipefail

if [[ "${1:-}" == "--skip" ]]; then
    echo "local_ci: skipped via --skip"
    exit 0
fi

# Prefer the project venv's tools — this works without `source .venv/bin/activate`.
# Falls back to PATH-resolved binaries when the venv isn't present (e.g. CI).
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_BIN="$REPO_ROOT/.venv/bin"
if [[ -x "$VENV_BIN/python" ]]; then
    PY="$VENV_BIN/python"
    RUFF="$VENV_BIN/ruff"
    MYPY="$VENV_BIN/mypy"
    MKDOCS="$VENV_BIN/mkdocs"
else
    PY="python"
    RUFF="ruff"
    MYPY="mypy"
    MKDOCS="mkdocs"
fi

cd "$REPO_ROOT"

start_total=$(date +%s)

run_step() {
    # run_step "label" cmd args...
    local label="$1"; shift
    local start
    start=$(date +%s)
    printf "▶ %-22s " "$label"
    if "$@" >/tmp/local_ci.last 2>&1; then
        local elapsed=$(( $(date +%s) - start ))
        printf "✓ (%ds)\n" "$elapsed"
    else
        local elapsed=$(( $(date +%s) - start ))
        printf "✗ (%ds)\n\n" "$elapsed"
        echo "── output ──"
        cat /tmp/local_ci.last
        echo "──"
        echo
        echo "local_ci: \"$label\" failed. Fix the issue, then retry"
        echo "  bash scripts/local_ci.sh"
        echo "or skip this once with"
        echo "  git push --no-verify"
        exit 1
    fi
}

# Order: ruff (fastest) → mypy → pytest+cov → mkdocs --strict.
# Mirrors the CI workflow jobs; coverage gate (fail_under=73) lives
# in pyproject.toml [tool.coverage.report] and fires automatically
# via the --cov flag.
run_step "ruff (lint)"             "$RUFF" check .
run_step "mypy (type check)"       "$MYPY" src/absentia
run_step "pytest + coverage gate"  "$PY" -m pytest --cov --cov-report=term-missing -q
run_step "mkdocs --strict (docs)"  "$MKDOCS" build --strict --quiet

elapsed_total=$(( $(date +%s) - start_total ))
echo
echo "local_ci: all checks passed (${elapsed_total}s total)"
