#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${MEETING_COPILOT_PORT:-8765}"
export MEETING_COPILOT_DATA_DIR="${MEETING_COPILOT_DATA_DIR:-$ROOT_DIR/data/local_runtime/web_mvp}"
PROVIDER_MODE="${MEETING_COPILOT_PROVIDER_MODE:-inherit}"

python3 "$ROOT_DIR/tools/workbench_server.py" start \
  --port "$PORT" \
  --data-dir "$MEETING_COPILOT_DATA_DIR" \
  --provider-mode "$PROVIDER_MODE"

echo "Workbench: http://127.0.0.1:$PORT/workbench"
echo "Data: $MEETING_COPILOT_DATA_DIR"
