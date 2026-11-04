#!/usr/bin/env bash
set -euo pipefail

# Portfolio Universal Local Validation Contract
# Repository: ha-herohealth
# Gate: 95% component statement coverage (offline/mock-only: -m "not live")

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -n $(git ls-files --others --exclude-standard) ]]; then
    echo "Error: move or commit untracked files before validation" >&2
    exit 1
fi

# Compare both index and working-tree changes, including untracked paths.
snapshot_dir=$(mktemp -d)
snapshot() {
    git status --porcelain=v1 --untracked-files=all > "$1/status"
    git diff --binary > "$1/worktree"
    git diff --cached --binary > "$1/index"
}
mkdir "$snapshot_dir/before" "$snapshot_dir/after"
snapshot "$snapshot_dir/before"
check_changes() {
    snapshot "$snapshot_dir/after"
    if ! diff -qr "$snapshot_dir/before" "$snapshot_dir/after" >/dev/null; then
        echo "Error: validation changed the working tree or index:" >&2
        git status --short >&2
        return 1
    fi
}
finish() {
    local result=$?
    check_changes || result=1
    rm -rf "$snapshot_dir"
    return "$result"
}
trap finish EXIT

echo "==> [1/5] Verifying toolchain and interpreter"
command -v uv >/dev/null 2>&1 || { echo "Error: uv is required but not installed" >&2; exit 1; }
if [[ ! -x .venv/bin/python ]]; then
    uv venv --python 3.14 .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 14, 2), "Python 3.14.2 or newer is required"; print(sys.version)'
uv pip sync --python .venv/bin/python requirements_test.lock
export PATH="$REPO_ROOT/.venv/bin:$PATH"

echo "==> [2/5] Running pre-commit hooks on all files"
pre-commit run --all-files

echo "==> [3/5] Running Ruff lint and formatting checks"
ruff check .
ruff format --check .

echo "==> [4/5] Running Python test suite with offline safety isolation (>=95% coverage, not live)"
pytest --disable-socket --allow-unix-socket

echo "==> [5/5] Checking for changes made by validation"
check_changes

echo "==> Local validation passed successfully!"
