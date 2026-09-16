#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${BRIDGE_SERVER_RELEASE_DIR:?}"
: "${BRIDGE_SERVER_VENV:?}"
: "${BRIDGE_SERVER_DRIVE_OAUTH_FILE:?}"
: "${BRIDGE_JOB_ID:?}"
: "${BRIDGE_ORIGINAL_SOURCE_DRIVE_ID:?}"

cd "$BRIDGE_SERVER_RELEASE_DIR"
PY="$BRIDGE_SERVER_VENV/bin/python"
[[ -x "$PY" ]] || { echo 'SERVER_PROD_VENV_MISSING' >&2; exit 78; }
[[ -f "$BRIDGE_SERVER_DRIVE_OAUTH_FILE" && ! -L "$BRIDGE_SERVER_DRIVE_OAUTH_FILE" ]] || { echo 'SERVER_PROD_DRIVE_SECRET_INVALID' >&2; exit 78; }

REVISION="$($PY - <<'PYREV'
import bridge_runtime_hardening_r26 as runtime
print(runtime.REVISION)
PYREV
)"
[[ "$REVISION" =~ ^3\.1-free-r[0-9]+([.][0-9]+)?$ ]] || { echo 'SERVER_PROD_REVISION_INVALID' >&2; exit 78; }
export BRIDGE_REQUESTED_ALGORITHM_REVISION="$REVISION"
export GOOGLE_DRIVE_OAUTH_JSON="$(cat "$BRIDGE_SERVER_DRIVE_OAUTH_FILE")"
if [[ -n "${BRIDGE_SERVER_WORKER_DB_FILE:-}" && -f "$BRIDGE_SERVER_WORKER_DB_FILE" && ! -L "$BRIDGE_SERVER_WORKER_DB_FILE" ]]; then
  export BRIDGE_WORKER_DATABASE_URL="$(cat "$BRIDGE_SERVER_WORKER_DB_FILE")"
fi

expected_job="$($PY - <<'PYID'
import os
from bridge_worker_3_1_free import stable_job_id
print(stable_job_id('drive', os.environ['BRIDGE_ORIGINAL_SOURCE_DRIVE_ID']))
PYID
)"
[[ "$expected_job" == "$BRIDGE_JOB_ID" ]] || { echo 'SERVER_PROD_IDENTITY_MISMATCH' >&2; exit 78; }

echo "SERVER_PROD_PREFLIGHT_PASS job=$BRIDGE_JOB_ID revision=$REVISION commit=${BRIDGE_SERVER_RUNTIME_COMMIT:-unknown} canary=${BRIDGE_SERVER_CANARY:-false}"
preflight="$($PY check_completed_job.py)"
printf '%s\n' "$preflight"
if grep -Fq '"status": "ALREADY_COMPLETED"' <<<"$preflight"; then
  echo "SERVER_PROD_ALREADY_COMPLETED job=$BRIDGE_JOB_ID revision=$REVISION"
  exit 0
fi

checkpoint(){
  if [[ -n "${BRIDGE_WORKER_DATABASE_URL:-}" ]]; then
    "$PY" database/run_checkpoint_persistence.py "$@"
  fi
}
checkpoint --stage worker_start --state started
"$PY" bridge_worker_3_1_free.py

(
  while true; do
    sleep 300
    checkpoint --stage process_job --state progress --details-json '{"heartbeat":true,"compute":"oracle_legacy_server"}' || true
  done
) &
HEARTBEAT_PID=$!
cleanup_heartbeat(){
  kill "$HEARTBEAT_PID" 2>/dev/null || true
  wait "$HEARTBEAT_PID" 2>/dev/null || true
}
trap cleanup_heartbeat EXIT

checkpoint --stage process_job --state started
set +e
"$PY" run_drive_3_1_free_oidc.py
RC=$?
set -e
cleanup_heartbeat
trap - EXIT
if (( RC != 0 )); then
  checkpoint --stage process_job --state failed --error-class PRIMARY_PROCESS_FAILURE --details-json "{\"exit_code\":$RC,\"compute\":\"oracle_legacy_server\"}" || true
  checkpoint --stage workflow_final --state failed --error-class PRIMARY_PROCESS_FAILURE --details-json "{\"process_outcome\":\"failure\",\"compute\":\"oracle_legacy_server\"}" || true
  exit "$RC"
fi
checkpoint --stage process_job --state completed

if [[ -n "${BRIDGE_OUTPUT_FOLDER_ID:-}" ]]; then
  "$PY" route_drive_job_outputs.py
fi
if [[ -n "${BRIDGE_OUTPUT_FOLDER_ID:-}" && -n "${BRIDGE_LESSON_NUMBER:-}" ]]; then
  "$PY" diana_longitudinal_postprocess_v3.py
fi
if [[ "${BRIDGE_PERSIST_DATABASE:-false}" == true ]]; then
  "$PY" - <<'PYDB'
from run_drive_3_1_free_oidc import user_oauth_token
from bridge_neon_persistence import persist_completed_drive_job
token=user_oauth_token()
if not token: raise SystemExit('BLOCKED_ACCESS: Drive OAuth unavailable')
if persist_completed_drive_job(token) is None: raise SystemExit('DATABASE_PERSIST_NOT_CONFIGURED')
print('DATABASE_PERSIST_COMPLETE')
PYDB
fi
checkpoint --stage workflow_final --state completed --details-json '{"process_outcome":"success","compute":"oracle_legacy_server"}'
echo "SERVER_PROD_RUN_COMPLETE job=$BRIDGE_JOB_ID revision=$REVISION compute=oracle_legacy_server"
