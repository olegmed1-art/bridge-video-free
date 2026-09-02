#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only Oracle power-idle classifier. It NEVER performs STOP.
# Output contract:
#   one bounded JSON verdict/evidence line;
#   ORACLE_IDLE_REASON=...;
#   ORACLE_IDLE_STATE=BUSY|IDLE|UNKNOWN.
# Only the separate STOP consumer may use IDLE, and all other states block.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"
COLLECTOR="${ORACLE_IDLE_COLLECTOR:-$SCRIPT_DIR/oracle_idle_collect.py}"
EVALUATOR="${ORACLE_IDLE_EVALUATOR:-$SCRIPT_DIR/oracle_idle_guard.py}"

unknown() {
  local why="$1"
  printf '{"evidence":{},"reason":"%s","schema":"oracle-idle-verdict-v1","state":"UNKNOWN","stop_allowed":false}\n' "$why"
  printf 'ORACLE_IDLE_REASON=%s\n' "$why"
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
json_line="$(printf '%s\n' "$output" | sed -n '1p')"
if [[ "$state_count" != 1 || "$reason_count" != 1 || -z "$json_line" ]]; then
  unknown invalid_idle_evaluator_output
fi
if ! printf '%s\n' "$json_line" | "$PYTHON" -c 'import json,sys; v=json.load(sys.stdin); assert v.get("schema")=="oracle-idle-verdict-v1"; assert v.get("state") in {"BUSY","IDLE","UNKNOWN"}; assert v.get("stop_allowed") is (v.get("state")=="IDLE"); assert isinstance(v.get("evidence"),dict)' >/dev/null 2>&1; then
  unknown invalid_idle_evidence_output
fi
printf '%s\n' "$json_line"
printf '%s\n' "$output" | grep -E '^ORACLE_IDLE_(REASON|STATE)='
