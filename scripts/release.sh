#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# absentia Release Script
#
# Bumps the version in pyproject.toml, promotes the CHANGELOG's
# [Unreleased] section, commits, annotated-tags, and pushes — which
# triggers the heavy CI gates in release-checks.yml (full pytest matrix
# across Python 3.13 / 3.14, mypy, mkdocs --strict) plus the wheels
# build in wheels.yml.
#
# The cheap gates (lint + fingerprint-bump) live in ci.yml and run on
# every push to main / PR — see .github/workflows/ci.yml.
#
# ── Interactive Mode (default) ───────────────────────────────
#
#   bash scripts/release.sh
#
#   Walks you through:
#     1. Bump type (validate / patch / minor / major / set manually / cancel)
#     2. Confirmation
#     3. Commit + tag + push
#
# ── Non-Interactive Mode (flags) ─────────────────────────────
#
#   bash scripts/release.sh --patch          Bug fix:        0.1.0 → 0.1.1
#   bash scripts/release.sh --hotfix         Alias for --patch
#   bash scripts/release.sh --minor          New feature:    0.1.1 → 0.2.0
#   bash scripts/release.sh --major          Breaking:       0.2.0 → 1.0.0
#   bash scripts/release.sh --set=1.0.0      Set explicitly: any → 1.0.0
#
# ── Validation Mode ──────────────────────────────────────────
#
#   bash scripts/release.sh --validate
#
#   Triggers release-checks.yml via workflow_dispatch on the CURRENT
#   BRANCH. Does NOT bump pyproject.toml, edit CHANGELOG, commit, tag,
#   or push. Use this to verify the heavy CI matrix passes before
#   cutting a real tag — useful when you've changed something the
#   pre-push hook can't catch (e.g. a Python-version-specific bug that
#   only fires on 3.14).
#
# ── Options ──────────────────────────────────────────────────
#
#   --no-verify    Skip git hooks (commit-msg + pre-push).
#                  Use sparingly — pre-push catches what CI catches,
#                  faster.
#   --help, -h     Show usage and exit.
#
# ── What happens (real release, not validate) ────────────────
#
#   1. Bump version in pyproject.toml
#   2. Promote CHANGELOG.md [Unreleased] → [X.Y.Z] - YYYY-MM-DD
#      (and insert a fresh empty [Unreleased] skeleton above it)
#   3. Commit ("release: vX.Y.Z (bump-type)")
#   4. Annotated git tag (vX.Y.Z)
#   5. Push commit + tag to origin
#   6. Tag push triggers release-checks.yml + wheels.yml
#
#   Each step rolls back on failure (commit / tag / push).
#
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PYPROJECT="pyproject.toml"
CHANGELOG="CHANGELOG.md"
REPO="skbays03/absentia"

# ── Colors ───────────────────────────────────────────────────

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

# ── Read current version from pyproject.toml ────────────────

if [ ! -f "$PYPROJECT" ]; then
    echo -e "${RED}ERROR: $PYPROJECT not found (run from repo root, or via bash scripts/release.sh)${NC}" >&2
    exit 1
fi

CURRENT_VERSION=$(grep -E '^version\s*=' "$PYPROJECT" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')

if ! echo "$CURRENT_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo -e "${RED}ERROR: could not parse version from $PYPROJECT (got: '$CURRENT_VERSION')${NC}" >&2
    exit 1
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# ── Parse flags ──────────────────────────────────────────────

BUMP_TYPE=""
SET_VERSION=""
INTERACTIVE=true
NO_VERIFY=false
VALIDATE_MODE=false

for arg in "$@"; do
    case "$arg" in
        --patch|--hotfix) BUMP_TYPE="patch"; INTERACTIVE=false ;;
        --minor)          BUMP_TYPE="minor"; INTERACTIVE=false ;;
        --major)          BUMP_TYPE="major"; INTERACTIVE=false ;;
        --set=*)          SET_VERSION="${arg#--set=}"; INTERACTIVE=false ;;
        --no-verify)      NO_VERIFY=true ;;
        --validate)       VALIDATE_MODE=true; INTERACTIVE=false ;;
        --help|-h)
            echo ""
            echo -e "${BOLD}absentia Release Script${NC}"
            echo ""
            echo -e "${BOLD}Interactive mode:${NC}"
            echo "  bash scripts/release.sh"
            echo ""
            echo -e "${BOLD}Non-interactive mode:${NC}"
            echo "  bash scripts/release.sh --patch        Bug fix       (0.1.0 → 0.1.1)"
            echo "  bash scripts/release.sh --hotfix       Alias for --patch"
            echo "  bash scripts/release.sh --minor        New feature   (0.1.1 → 0.2.0)"
            echo "  bash scripts/release.sh --major        Breaking      (0.2.0 → 1.0.0)"
            echo "  bash scripts/release.sh --set=X.Y.Z    Set manually  (any → X.Y.Z)"
            echo ""
            echo -e "${BOLD}Validation mode (no bump, no commit, no tag, no push):${NC}"
            echo "  bash scripts/release.sh --validate"
            echo ""
            echo "  Triggers release-checks.yml via workflow_dispatch on the"
            echo "  current branch. Useful for catching Python-3.14-specific"
            echo "  failures the local pre-push hook can't reproduce."
            echo ""
            echo -e "${BOLD}Options:${NC}"
            echo "  --no-verify             Skip git hooks (commit-msg, pre-push)"
            echo ""
            echo -e "  Current version: ${CYAN}$CURRENT_VERSION${NC}"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown flag: $arg${NC}" >&2
            echo "Usage: bash scripts/release.sh [--patch|--minor|--major|--hotfix|--set=X.Y.Z|--validate]" >&2
            echo "Run bash scripts/release.sh --help for full details." >&2
            exit 1
            ;;
    esac
done

# ── Interactive mode ─────────────────────────────────────────

if [ "$INTERACTIVE" = true ]; then
    echo ""
    echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
    echo -e "  ${BOLD}  absentia Release CLI${NC}"
    echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Current version: ${CYAN}$CURRENT_VERSION${NC}"
    echo ""

    # ── Bump type ────────────────────────────────────────────
    echo -e "  ${BOLD}Bump type${NC}"
    echo ""
    echo "    0) validate — CI verification run  (no bump, no commit, current branch)"
    echo "    1) patch    — Bug fix / security fix  ($MAJOR.$MINOR.$((PATCH + 1)))"
    echo "    2) minor    — New feature             ($MAJOR.$((MINOR + 1)).0)"
    echo "    3) major    — Breaking / phase change  ($((MAJOR + 1)).0.0)"
    echo "    4) set      — Set version manually"
    echo "    5) cancel   — Exit without changes"
    echo ""
    read -rp "  Select [0-5]: " bump_choice

    case "$bump_choice" in
        0) VALIDATE_MODE=true ;;
        1) BUMP_TYPE="patch" ;;
        2) BUMP_TYPE="minor" ;;
        3) BUMP_TYPE="major" ;;
        4)
            read -rp "  Enter version (X.Y.Z): " SET_VERSION
            if ! echo "$SET_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
                echo -e "  ${RED}Invalid version format. Use X.Y.Z (e.g. 1.0.0)${NC}"
                exit 1
            fi
            ;;
        5|"")
            echo ""
            echo "  Cancelled."
            exit 0
            ;;
        *)
            echo -e "  ${RED}Invalid selection.${NC}"
            exit 1
            ;;
    esac

    if [ "$VALIDATE_MODE" = false ]; then
        if [ -n "$SET_VERSION" ]; then
            NEW_VERSION="$SET_VERSION"
            BUMP_TYPE="set"
        else
            case "$BUMP_TYPE" in
                patch) NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))" ;;
                minor) NEW_VERSION="$MAJOR.$((MINOR + 1)).0" ;;
                major) NEW_VERSION="$((MAJOR + 1)).0.0" ;;
            esac
        fi

        echo ""
        echo -e "  Version: ${YELLOW}$CURRENT_VERSION${NC} → ${GREEN}$NEW_VERSION${NC} ($BUMP_TYPE)"
        echo ""
        echo -e "  ${BOLD}Confirm${NC}"
        echo ""
        echo -e "    Action: bump pyproject.toml + promote CHANGELOG, commit,"
        echo -e "            tag v$NEW_VERSION, push to origin (triggers"
        echo -e "            release-checks.yml + wheels.yml)"
        echo ""
        read -rp "  Proceed? [Y/n]: " confirm
        case "$confirm" in
            n|N|no|No)
                echo ""
                echo "  Cancelled."
                exit 0
                ;;
        esac
        echo ""
    fi

# ── Non-interactive mode ─────────────────────────────────────
else
    if [ "$VALIDATE_MODE" = false ] && [ -z "$BUMP_TYPE" ] && [ -z "$SET_VERSION" ]; then
        echo "Specify a bump type: --patch, --minor, --major, --set=X.Y.Z, or --validate" >&2
        exit 1
    fi

    if [ "$VALIDATE_MODE" = false ]; then
        if [ -n "$SET_VERSION" ]; then
            if ! echo "$SET_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
                echo -e "${RED}ERROR: Invalid version format '$SET_VERSION'. Use X.Y.Z (e.g. 1.0.0)${NC}" >&2
                exit 1
            fi
            NEW_VERSION="$SET_VERSION"
            BUMP_TYPE="set"
        else
            case "$BUMP_TYPE" in
                patch) NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))" ;;
                minor) NEW_VERSION="$MAJOR.$((MINOR + 1)).0" ;;
                major) NEW_VERSION="$((MAJOR + 1)).0.0" ;;
            esac
        fi

        echo ""
        echo "  Version: $CURRENT_VERSION → $NEW_VERSION ($BUMP_TYPE)"
        echo ""
    fi
fi

# ── Validation mode — dispatch release-checks.yml on current branch ──

if [ "$VALIDATE_MODE" = true ]; then
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
    if [ -z "$CURRENT_BRANCH" ]; then
        echo -e "${RED}✗ Could not determine current branch (detached HEAD?).${NC}" >&2
        exit 1
    fi

    echo ""
    echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
    echo -e "  ${BOLD}  VALIDATION RUN${NC}"
    echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
    echo ""
    echo "  This does not bump pyproject.toml, edit CHANGELOG, commit, or tag."
    echo "  It dispatches release-checks.yml on the current branch."
    echo ""
    echo -e "  Branch:  ${CYAN}${CURRENT_BRANCH}${NC}"
    echo -e "  Repo:    ${CYAN}${REPO}${NC}"
    echo ""

    if ! command -v gh &> /dev/null; then
        echo -e "  ${RED}✗ gh CLI not found — cannot dispatch workflow.${NC}" >&2
        echo "    Install gh CLI: https://cli.github.com" >&2
        exit 1
    fi

    if gh workflow run release-checks.yml \
            --repo "$REPO" \
            --ref "$CURRENT_BRANCH" \
            -f "ref=${CURRENT_BRANCH}"; then
        echo ""
        echo "  ✓ release-checks.yml dispatched on $CURRENT_BRANCH."
        echo ""
        echo "  Watch:   gh run watch --repo ${REPO}"
        echo "  Inspect: https://github.com/${REPO}/actions/workflows/release-checks.yml"
        echo ""
    else
        echo ""
        echo -e "  ${RED}✗ Could not dispatch workflow.${NC}" >&2
        echo "    Check that gh is authenticated: gh auth status" >&2
        exit 1
    fi

    exit 0
fi

# ── Bump version in pyproject.toml ───────────────────────────

# Match the *first* `version = "..."` line under [project] (not other
# version entries elsewhere in the file). The constraint that
# CURRENT_VERSION is uniquely the value of that line is enforced by
# the parse step above.
if ! sed -i.release-bak \
        -E "0,/^version\s*=/s/^version\s*=\s*\"$CURRENT_VERSION\"/version = \"$NEW_VERSION\"/" \
        "$PYPROJECT" 2>/dev/null; then
    # macOS sed doesn't accept the 0,/.../ address form. Fall back to
    # awk for cross-platform safety.
    awk -v old="$CURRENT_VERSION" -v new="$NEW_VERSION" '
        !done && /^version[[:space:]]*=/ {
            sub("\"" old "\"", "\"" new "\"")
            done = 1
        }
        { print }
    ' "$PYPROJECT" > "${PYPROJECT}.release-bak" && mv "${PYPROJECT}.release-bak" "$PYPROJECT"
fi
rm -f "${PYPROJECT}.release-bak"

# Verify the bump landed.
if ! grep -qE "^version\s*=\s*\"$NEW_VERSION\"" "$PYPROJECT"; then
    echo -e "${RED}✗ Version bump in $PYPROJECT did not stick — aborting.${NC}" >&2
    git checkout -- "$PYPROJECT" 2>/dev/null || true
    exit 1
fi
echo "  ✓ Bumped pyproject.toml ($CURRENT_VERSION → $NEW_VERSION)"

# ── Promote CHANGELOG [Unreleased] section ───────────────────

# Replace the literal `## [Unreleased]` heading with a fresh empty
# [Unreleased] skeleton followed by the new versioned heading. We use
# python3 because portable sed/awk multiline replacement is painful
# enough to be worse than the dependency.

if [ -f "$CHANGELOG" ] && grep -q '^## \[Unreleased\]' "$CHANGELOG"; then
    if command -v python3 &> /dev/null; then
        TODAY=$(date -u +%Y-%m-%d)
        CHANGELOG_PATH="$CHANGELOG" \
        NEW_VER="$NEW_VERSION" \
        TODAY_DATE="$TODAY" \
        python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["CHANGELOG_PATH"])
new_ver = os.environ["NEW_VER"]
today = os.environ["TODAY_DATE"]

text = path.read_text()
needle = "## [Unreleased]"
if needle not in text:
    raise SystemExit("CHANGELOG missing [Unreleased] heading — already promoted?")

skeleton = f"""## [Unreleased]

### Added

### Changed

### Fixed

## [{new_ver}] - {today}"""

new_text = text.replace(needle, skeleton, 1)
path.write_text(new_text)
PY
        echo "  ✓ Promoted CHANGELOG.md [Unreleased] → [$NEW_VERSION] - $TODAY"
    else
        echo -e "  ${YELLOW}⚠ python3 not found — leaving CHANGELOG.md untouched.${NC}"
        echo "    Manually promote [Unreleased] → [$NEW_VERSION] before tagging."
    fi
else
    echo -e "  ${DIM}ℹ CHANGELOG.md missing or has no [Unreleased] section — skipping promotion.${NC}"
fi

# ── Commit + Tag + Push (with rollback on failure) ──────────

VERIFY_FLAG=""
if [ "$NO_VERIFY" = true ]; then
    VERIFY_FLAG="--no-verify"
fi

echo ""
echo "  Committing release..."
git add "$PYPROJECT"
git add "$CHANGELOG" 2>/dev/null || true

if ! git commit $VERIFY_FLAG -m "release: v$NEW_VERSION ($BUMP_TYPE)"; then
    echo ""
    echo -e "  ${RED}✗ Commit failed — rolling back file changes${NC}" >&2
    git checkout -- "$PYPROJECT" "$CHANGELOG" 2>/dev/null || true
    exit 1
fi
echo "  ✓ Committed"

echo "  Tagging v$NEW_VERSION..."
if ! git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION ($BUMP_TYPE)"; then
    echo ""
    echo -e "  ${RED}✗ Tag failed — rolling back commit${NC}" >&2
    git reset --soft HEAD~1
    git checkout -- "$PYPROJECT" "$CHANGELOG" 2>/dev/null || true
    exit 1
fi
echo "  ✓ Tagged v$NEW_VERSION"

echo "  Pushing commit to origin..."
if ! git push $VERIFY_FLAG origin HEAD; then
    echo ""
    echo -e "  ${RED}✗ Push failed — rolling back commit and tag${NC}" >&2
    git tag -d "v$NEW_VERSION" 2>/dev/null
    git reset --soft HEAD~1
    git checkout -- "$PYPROJECT" "$CHANGELOG" 2>/dev/null || true
    exit 1
fi
echo "  ✓ Pushed commit"

echo "  Pushing tag v$NEW_VERSION to origin..."
if ! git push $VERIFY_FLAG origin "v$NEW_VERSION"; then
    echo ""
    echo -e "  ${RED}✗ Tag push failed — local tag remains, commit is on origin.${NC}" >&2
    echo "    Retry manually:  git push origin v$NEW_VERSION" >&2
    exit 1
fi
echo "  ✓ Pushed tag"

# ── Done ─────────────────────────────────────────────────────

echo ""
echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "  ${BOLD}  Release v$NEW_VERSION triggered${NC}"
echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
echo ""
echo "  release-checks.yml  →  test matrix · mypy · mkdocs --strict"
echo "  wheels.yml          →  cross-platform mypyc wheels"
echo ""
echo "  Monitor:  https://github.com/$REPO/actions"
echo "  Release:  https://github.com/$REPO/releases/tag/v$NEW_VERSION"
echo ""
