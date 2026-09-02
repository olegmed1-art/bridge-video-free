#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only Oracle power-idle classifier.  It NEVER performs STOP.
# Output contract has exactly one terminal state marker:
#   ORACLE_IDLE_STATE=BUSY|IDLE|UNKNOWN
# Only the separate STOP consumer may use IDLE, and all other states block.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"
COLLECTOR="${ORACLE_IDLE_COLLECTOR:-$SCRIPT_DIR/oracle_idle_collect.py}"
EVALUATOR="${ORACLE_IDLE_EVALUATOR:-$SCRIPT_DIR/oracle_idle_guard.py}"

unknown() {
  printf 'ORACLE_IDLE_REASON=%s\n' "$1"
  printf 'ORACLE_IDLE_STATE=UNKNOWN\n'
  exit 0
}

[[ -x "$PYTHON" ]] || unknown assistant_lab_python_missing
[[ -f "$COLLECTOR" && ! -L "$COLLECTOR" ]] || unknown idle_collector_unavailable
[[ -f "$EVALUATOR" && ! -L "$EVALUATOR" ]] || unknown idle_evaluator_unavailable

snapshot="$(mktemp)" || unknown snapshot_tempfile_failed
cleanup(){ rm -f -- "$snapshot"; }
trap cleanup EXIT INT TERM
chmod 0600 "$snapshot" || unknown snapshot_tempfile_permissions_failed

if ! "$PYTHON" "$COLLECTOR" >"$snapshot" 2>/dev/null; then
  unknown telemetry_collection_failed
fi
if [[ ! -s "$snapshot" ]]; then
  unknown telemetry_collection_empty
fi

output="$({ "$PYTHON" "$EVALUATOR" "$snapshot"; } 2>/dev/null || true)"
state_count="$(printf '%s\n' "$output" | grep -Ec '^ORACLE_IDLE_STATE=(BUSY|IDLE|UNKNOWN)$' || true)"
reason_count="$(printf '%s\n' "$output" | grep -Ec '^ORACLE_IDLE_REASON=[A-Za-z0-9_.:-]+$' || true)"
if [[ "$state_count" != 1 || "$reason_count" != 1 ]]; then
  unknown invalid_idle_evaluator_output
fi
printf '%s\n' "$output" | grep -E '^ORACLE_IDLE_(REASON|STATE)='
