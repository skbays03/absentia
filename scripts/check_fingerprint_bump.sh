#!/usr/bin/env bash
# Verify EXTRACTOR_FINGERPRINT was bumped when extractor source changed.
#
# Compares the current branch's diff against a base commit (default:
# origin/main). Fails if any file under src/absentia/extractors/ changed
# in the diff but EXTRACTOR_FINGERPRINT in extractors/__init__.py
# didn't.
#
# Used by:
#   - .github/workflows/ci.yml (pull_request + push)
#   - Optional local pre-push if you want belt-and-suspenders
#
# Override: include "[no-fingerprint-bump]" in any commit message in
# the diff range when the extractor change is a pure refactor / typo
# fix / comment update that doesn't affect emitted entity or feature
# shape. The check looks at every commit message in the range.
#
# Usage:
#   bash scripts/check_fingerprint_bump.sh                    (vs origin/main)
#   bash scripts/check_fingerprint_bump.sh <base-commit>      (vs explicit base)

set -euo pipefail

BASE_REF="${1:-origin/main}"

# Resolve base — if origin/main isn't fetched (some CI setups), fall
# back to HEAD~1 (best-effort one-commit window). Better than refusing
# to run.
if ! git rev-parse "$BASE_REF" >/dev/null 2>&1; then
    BASE_REF="HEAD~1"
    if ! git rev-parse "$BASE_REF" >/dev/null 2>&1; then
        echo "fingerprint-check: no base ref to diff against; skipping."
        exit 0
    fi
fi

EXTRACTOR_DIR="src/absentia/extractors/"
FINGERPRINT_FILE="src/absentia/extractors/__init__.py"
FINGERPRINT_REGEX='^[+-]EXTRACTOR_FINGERPRINT'

# Did any extractor source change?
extractor_changed=$(git diff --name-only "$BASE_REF" -- "$EXTRACTOR_DIR" | wc -l | tr -d ' ')
if [[ "$extractor_changed" == "0" ]]; then
    echo "fingerprint-check: no extractor changes since $BASE_REF — pass."
    exit 0
fi

# Did the fingerprint line itself change?
if git diff "$BASE_REF" -- "$FINGERPRINT_FILE" | grep -E "$FINGERPRINT_REGEX" >/dev/null 2>&1; then
    echo "fingerprint-check: extractor changed AND fingerprint bumped — pass."
    exit 0
fi

# Override: did any commit in the range opt out via the marker?
if git log --format=%B "$BASE_REF..HEAD" | grep -q "\[no-fingerprint-bump\]"; then
    echo "fingerprint-check: extractor changed but [no-fingerprint-bump] marker present — pass."
    echo "  (verify the change really doesn't affect emitted entities or feature shapes)"
    exit 0
fi

# Failure path: list which files triggered + tell the dev exactly what to do.
cat <<EOF
fingerprint-check: ✗ FAIL

Extractor source changed since $BASE_REF:
$(git diff --name-only "$BASE_REF" -- "$EXTRACTOR_DIR" | sed 's/^/  /')

…but EXTRACTOR_FINGERPRINT in $FINGERPRINT_FILE was not bumped.

If the change affects extractor output (new feature_kind, new entity kind,
fixed bug that changes emitted shape, new built-in extractor language):

  1. Bump EXTRACTOR_FINGERPRINT in $FINGERPRINT_FILE
     (e.g. "v2" → "v3"). Add a one-line comment in the bump-history
     section naming what changed.
  2. Re-run the corpora regression: $(grep -l 'absentia-self' tests/fixtures/corpora.toml >/dev/null && echo "tests/test_corpus_regression.py")
     and update corpora.toml if any counts drifted.
  3. Commit both changes together.

If the change is a pure refactor / typo / comment update that does NOT
affect emitted entities or features, override this check by including
the literal string

    [no-fingerprint-bump]

in any commit message in the diff range. That documents the exemption
in the git history for future reviewers.

EOF
exit 1
