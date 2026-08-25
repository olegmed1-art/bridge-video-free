#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact, one-job root helper for UV-DIANA11-DURABLE-002.
# No arbitrary file id, path, folder, shell, profile, command, or payload is accepted.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SPOOL="$BASE_DIR/spool"
readonly BRIDGE_JOB_ID='diana11-durable-002-20260826-01'
readonly BRIDGE_JOB_HASH='e53fa37ce69d97bc9d8c995bc8f416b0e7b5ad42610cda4d75faa2385bcf60fc'
readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'
readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'
readonly OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-002-staging'
readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-002-published'
readonly RECEIPT_READER="$SOURCE_DIR/ops/universal_video_receipt_reader.py"

fail(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
verify_runtime(){
  bash "$SOURCE_DIR/ops/oracle_universal_video_spool_guard.sh" \
    verify "$BASE_DIR" root universal-video universal-video >/dev/null \
    || fail 'unsafe Universal Video spool layout'
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" && -d "$SPOOL/results" ]] || fail 'spool missing'
  [[ -x "$PYTHON" && -f "$SOURCE_DIR/universal_video/result_conformance.py" && -f "$RECEIPT_READER" ]] || fail 'conformance runtime missing'
  id universal-video >/dev/null 2>&1 || fail 'universal-video user missing'
}

verify_school_runtime(){
  local service
  for service in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
    systemctl is-active --quiet "$service" || fail "$service inactive"
  done
  local ready
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
  READY_JSON="$ready" python3 - <<'PY'
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver') == 'ready'
PY
}

verify_root_control_dir(){
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || fail "unsafe root control directory: $path"
  [[ "$(stat -c '%U:%G:%a' "$path")" == 'root:root:700' ]] || fail "unsafe root control ownership/mode: $path"
}

spec_for(){
  case "$1" in
  submit-bridge) submit_for "$BRIDGE_JOB_ID" bridge_lesson 'fresh provenance shadow result UV-DIANA11-DURABLE-002' ;;
  status-bridge) verify_runtime; state_for "$BRIDGE_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_002_STATUS_PASS' ;;
  conform-bridge)
    verify_runtime
    state_for "$BRIDGE_JOB_ID"
    [[ "$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)" == TECHNICAL_CONFORMANT ]] \
      || fail 'bridge result did not pass technical conformance'
    echo 'UNIVERSAL_VIDEO_DIANA11_002_CONFORMANCE_PASS'
    ;;
  publish-bridge) publish_bridge ;;
  *) fail 'unsupported operation' ;;
esac
