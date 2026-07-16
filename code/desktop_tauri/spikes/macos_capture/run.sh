#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="${SCRIPT_DIR}/.build/macos-capture-spike"

if [[ ! -x "${BINARY}" ]]; then
  "${SCRIPT_DIR}/build.sh" >/dev/null
fi

if [[ $# -eq 0 ]]; then
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  set -- \
    --mode probe \
    --no-request-permissions \
    --evidence "${SCRIPT_DIR}/.build/evidence/permission-probe-${STAMP}.json"
fi

exec "${BINARY}" "$@"
