#!/usr/bin/env bash
set -euo pipefail

# Portfolio Universal Local Validation Contract
# Repository: ha-herohealth
# Gate: 95% component statement coverage (offline/mock-only: -m "not live")

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f ".venv/bin/activate" ]]; then
    PYTHON=".venv/bin/python"
    PRE_COMMIT=".venv/bin/pre-commit"
    RUFF=".venv/bin/ruff"
    PYTEST=".venv/bin/pytest"
else
    PYTHON="python"
    PRE_COMMIT="pre-commit"
    RUFF="ruff"
    PYTEST="pytest"
fi

echo "==> [1/5] Verifying toolchain and interpreter"
$PYTHON --version

echo "==> [2/5] Running pre-commit hooks on all files"
$PRE_COMMIT run --all-files

echo "==> [3/5] Running Ruff lint and formatting checks"
$RUFF check .
$RUFF format --check .

echo "==> [4/5] Running Python test suite with offline safety isolation (>=95% coverage, not live)"
$PYTEST

echo "==> [5/5] Verifying clean git working tree"
git diff --exit-code

echo "==> Local validation passed successfully!"
