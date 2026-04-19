#!/usr/bin/env bash
# Shared venv-discovery helper for pre-push hooks.
# Exports VENV_BIN to the directory containing mypy/pytest/ruff.
# Fails with a clear, actionable message if the venv is missing.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_BIN="${REPO_ROOT}/.venv/bin"

if [[ ! -x "${VENV_BIN}/python" ]]; then
  echo "pre-push hook: missing ${VENV_BIN}/python"
  echo "  run this once to set up the venv:"
  echo "    python -m venv .venv && .venv/bin/pip install -e backend[dev]"
  exit 1
fi

export VENV_BIN REPO_ROOT
