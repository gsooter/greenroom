#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_venv.sh
source "${SCRIPT_DIR}/_venv.sh"

cd "${REPO_ROOT}/backend"
exec "${VENV_BIN}/pytest" --ignore=tests/data --ignore=tests/api -q
