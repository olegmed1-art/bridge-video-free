#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Root-owned bounded Universal Video administrative entrypoint for OCI Run Command.
# Installed as /usr/local/sbin/universal-video-oci-admin and exposed to ocarun
# only through exact sudoers commands. No arbitrary shell, path, commit, file ID,
# folder ID, service name, or command argument is accepted.

readonly UV_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/${UV_RUNTIME_COMMIT}"
readonly ACTIVATION_PATH='ops/oracle_universal_video_run_command.sh'
readonly ACTIVATION_BLOB='bbf4dc5779726fca415f641b90d017a802daaabf'
readonly PRODUCTIONIZE_PATH='ops/oracle_universal_video_productionize.sh'
readonly PRODUCTIONIZE_BLOB='9a76e06ed1cb7ecc92102e5c16cf215c18f9159d'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly EVIDENCE_REQUEST_DIR='/var/lib/bridge-school/universal-video'
readonly EVIDENCE_REQUEST_PATH="$EVIDENCE_REQUEST_DIR/evidence-export-request.json"
readonly EVIDENCE_STATUS_DIR='/run/bridge-school'
readonly EVIDENCE_STATUS_PATH="$EVIDENCE_STATUS_DIR/universal-video-status.json"
readonly EVIDENCE_EXPORTER_PIN='/etc/bridge-school/universal-video-admin-source-commit'
readonly MAX_EVIDENCE_REQUEST_BYTES=16384
readonly MAX_EVIDENCE_RECEIPT_BYTES=32768
readonly DRIVE_PROBE_FILE_ID='1RKrDWP6IOfVyuDWRMIsiUT62vpmVW9VS'
readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'
readonly OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly OAUTH_ENV="$BASE_DIR/universal-video-secrets.env"
readonly SAFE_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

CURRENT_STAGE='startup'
fail(){ echo "ERROR: $*" >&2; exit 1; }
on_error(){ local rc=$?; echo "UNIVERSAL_VIDEO_OCI_ADMIN_ERROR stage=${CURRENT_STAGE} rc=${rc}" >&2; exit "$rc"; }
trap on_error ERR
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
state(){ systemctl is-active "$1" 2>/dev/null || true; }

verify_dds3(){
  local ready
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
  READY="$ready" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
assert x.get('position_solver') == 'ready', x
PY
}

verify_protected_services(){
  local s
  for s in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
    systemctl is-active --quiet "$s" || fail "$s is not active"
  done
}

verify_sidecar(){
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service is not active'
  [[ "$(systemctl is-enabled universal-video.service 2>/dev/null || true)" == enabled ]] || fail 'universal-video.service is not enabled'
}

secret_state(){
  if [[ ! -f "$OAUTH_FILE" || -L "$OAUTH_FILE" || ! -f "$OAUTH_ENV" ]]; then
    echo NOT_CONFIGURED
    return
  fi
  local owner group mode path_line
  owner="$(stat -c '%U' "$OAUTH_FILE")"
  group="$(stat -c '%G' "$OAUTH_FILE")"
  mode="$(stat -c '%a' "$OAUTH_FILE")"
  path_line="$(sed -n 's/^GOOGLE_DRIVE_OAUTH_JSON_FILE=//p' "$OAUTH_ENV" | head -n1)"
  if [[ "$owner" != root || "$group" != universal-video || "$mode" != 640 || "$path_line" != "$OAUTH_FILE" ]]; then
    echo NOT_CONFIGURED
    return
  fi
  if DRIVE_OAUTH_FILE="$OAUTH_FILE" python3 - <<'PY'
import json, os
from pathlib import Path
try:
    x=json.loads(Path(os.environ['DRIVE_OAUTH_FILE']).read_text(encoding='utf-8'))
    ok=isinstance(x,dict) and all(isinstance(x.get(k),str) and x[k].strip() for k in ('client_id','client_secret','refresh_token'))
except Exception:
    ok=False
raise SystemExit(0 if ok else 1)
PY
  then
    echo CONFIGURED
  else
    echo NOT_CONFIGURED
  fi
}

audit(){
  CURRENT_STAGE='audit_protected_services'
  verify_protected_services
  CURRENT_STAGE='audit_dds3'
  verify_dds3
  CURRENT_STAGE='audit_sidecar'
  verify_sidecar
  CURRENT_STAGE='audit_collect_state'
  local memory_high memory_max timer_active timer_enabled drive_state source_head
  memory_high="$(systemctl show universal-video.service -p MemoryHigh --value 2>/dev/null || true)"
  memory_max="$(systemctl show universal-video.service -p MemoryMax --value 2>/dev/null || true)"
  timer_active="$(state universal-video-maintenance.timer)"
  timer_enabled="$(systemctl is-enabled universal-video-maintenance.timer 2>/dev/null || true)"
  drive_state="$(secret_state)"
  source_head='unavailable'
  if [[ -d "$SOURCE_DIR/.git" ]]; then
    source_head="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo unavailable)"
  fi
  printf 'universal_video=active_enabled\n'
  printf 'source_head=%s\n' "$source_head"
  printf 'memory_high=%s\n' "${memory_high:-unset}"
  printf 'memory_max=%s\n' "${memory_max:-unset}"
  printf 'maintenance_timer_active=%s\n' "${timer_active:-inactive}"
  printf 'maintenance_timer_enabled=%s\n' "${timer_enabled:-disabled}"
  printf 'drive_oauth=%s\n' "$drive_state"
  echo 'protected_services=active'
  echo 'dds3=ready_real_no_fallback'
  echo UNIVERSAL_VIDEO_OCI_ADMIN_AUDIT_PASS
}

download_verified(){
  local remote_path="$1" expected_blob="$2" destination="$3"
  curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 \
    "$RAW_BASE/$remote_path" -o "$destination"
  [[ "$(git hash-object "$destination")" == "$expected_blob" ]] || fail "pinned blob mismatch for $remote_path"
  bash -n "$destination"
  chmod 0400 "$destination"
}

productionize(){
  CURRENT_STAGE='productionize_protected_services'
  verify_protected_services
  CURRENT_STAGE='productionize_dds3_before'
  verify_dds3
  CURRENT_STAGE='productionize_sidecar_before'
  verify_sidecar
  CURRENT_STAGE='productionize_running_job_guard'
  [[ ! -e "$BASE_DIR/spool/running" || -z "$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null)" ]] \
    || fail 'universal-video has a running job'

  local before_assistant before_observer before_control before_bridge
  before_assistant="$(state assistant-lab.service)"
  before_observer="$(state assistant-lab-observer.service)"
  before_control="$(state assistant-lab-control.service)"
  before_bridge="$(state assistant-lab-control-bridge.service)"

  local work activation production log_file
  work="$(mktemp -d -t uv-oci-admin.XXXXXX)"
  trap 'rm -rf "${work:-}"' EXIT INT TERM
  activation="$work/activate.sh"
  production="$work/productionize.sh"
  log_file="$work/productionize.log"
  CURRENT_STAGE='productionize_download_activation'
  download_verified "$ACTIVATION_PATH" "$ACTIVATION_BLOB" "$activation"
  CURRENT_STAGE='productionize_download_script'
  download_verified "$PRODUCTIONIZE_PATH" "$PRODUCTIONIZE_BLOB" "$production"

  echo "runtime_commit=$UV_RUNTIME_COMMIT"
  echo 'UNIVERSAL_VIDEO_OCI_ADMIN_PIN_PASS'

  CURRENT_STAGE='productionize_activation'
  /usr/bin/env -i \
    PATH="$SAFE_PATH" HOME=/root LANG=C.UTF-8 \
    UNIVERSAL_VIDEO_GIT_REF="$UV_RUNTIME_COMMIT" \
    UNIVERSAL_VIDEO_RUN_SMOKE=0 \
    UNIVERSAL_VIDEO_ACTIVATE=1 \
    UNIVERSAL_VIDEO_PREWARM_MODEL=0 \
    nice -n 10 bash "$activation" | tee "$log_file"
  grep -Fx 'UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS' "$log_file" >/dev/null || fail 'activation completion marker missing'
  grep -Fx "source_commit=$UV_RUNTIME_COMMIT" "$log_file" >/dev/null || fail 'activation source pin mismatch'

  : > "$log_file"
  CURRENT_STAGE='productionize_script'
  /usr/bin/env -i \
    PATH="$SAFE_PATH" HOME=/root LANG=C.UTF-8 \
    UNIVERSAL_VIDEO_SOURCE_DIR="$SOURCE_DIR" \
    UNIVERSAL_VIDEO_DIR="$BASE_DIR" \
    UNIVERSAL_VIDEO_DRIVE_PROBE_FILE_ID="$DRIVE_PROBE_FILE_ID" \
    UNIVERSAL_VIDEO_DRIVE_RESULTS_FOLDER_ID="$DRIVE_RESULTS_FOLDER_ID" \
    UNIVERSAL_VIDEO_MAX_SOURCE_BYTES=17179869184 \
    UNIVERSAL_VIDEO_MAX_DURATION_SECONDS=43200 \
    nice -n 10 bash "$production" | tee "$log_file"

  CURRENT_STAGE='productionize_marker_validation'
  for marker in \
    UNIVERSAL_VIDEO_MEMORY_LIMITS_PASS \
    UNIVERSAL_VIDEO_RETENTION_PASS \
    UNIVERSAL_VIDEO_RESULT_ROUTING_PASS \
    UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS \
    UNIVERSAL_VIDEO_DDS3_NONREGRESSION_PASS; do
    grep -F "$marker" "$log_file" >/dev/null || fail "missing productionization marker: $marker"
  done
  grep -F 'UNIVERSAL_VIDEO_DRIVE_OAUTH=CONFIGURED' "$log_file" >/dev/null || fail 'Drive OAuth gate did not pass'
  grep -F 'UNIVERSAL_VIDEO_PRODUCTIONIZE_PASS' "$log_file" | grep -F 'asr_started=0' >/dev/null \
    || fail 'final no-ASR productionization marker missing'

  CURRENT_STAGE='productionize_service_state_validation'
  [[ "$(state assistant-lab.service)" == "$before_assistant" ]] || fail 'assistant-lab state changed'
  [[ "$(state assistant-lab-observer.service)" == "$before_observer" ]] || fail 'observer state changed'
  [[ "$(state assistant-lab-control.service)" == "$before_control" ]] || fail 'control state changed'
  [[ "$(state assistant-lab-control-bridge.service)" == "$before_bridge" ]] || fail 'control bridge state changed'
  CURRENT_STAGE='productionize_dds3_after'
  verify_dds3
  CURRENT_STAGE='productionize_sidecar_after'
  verify_sidecar
  rm -rf "$work"
  trap - EXIT INT TERM
  echo UNIVERSAL_VIDEO_OCI_ADMIN_PRODUCTIONIZE_PASS
}

evidence_export(){
  CURRENT_STAGE='evidence_export_protected_services'
  verify_protected_services
  CURRENT_STAGE='evidence_export_dds3_before'
  verify_dds3
  CURRENT_STAGE='evidence_export_sidecar'
  verify_sidecar
  CURRENT_STAGE='evidence_export_runtime_pin'
  [[ -d "$SOURCE_DIR/.git" ]] || fail 'universal-video source checkout missing'
  [[ -f "$EVIDENCE_EXPORTER_PIN" && ! -L "$EVIDENCE_EXPORTER_PIN" ]] || fail 'evidence exporter pin missing'
  local exporter_pin
  exporter_pin="$(tr -d '\n' < "$EVIDENCE_EXPORTER_PIN")"
  [[ "$exporter_pin" =~ ^[0-9a-f]{40}$ ]] || fail 'evidence exporter pin invalid'
  [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)" == "$exporter_pin" ]] \
    || fail 'evidence exporter source pin mismatch'
  CURRENT_STAGE='evidence_export_running_job_guard'
  [[ ! -e "$BASE_DIR/spool/running" || -z "$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null)" ]] \
    || fail 'universal-video has a running job'

  install -d -m 0750 -o root -g universal-video "$EVIDENCE_REQUEST_DIR" "$EVIDENCE_STATUS_DIR"
  local work request_tmp status_tmp receipt receipt_bytes
  work="$(mktemp -d -p "$EVIDENCE_STATUS_DIR" .uv-evidence-export.XXXXXX)"
  chown root:universal-video "$work"
  chmod 0750 "$work"
  trap 'rm -rf "${work:-}"' EXIT INT TERM
  request_tmp="$work/request.json"
  status_tmp="$work/status.json"

  CURRENT_STAGE='evidence_export_request_read'
  /usr/bin/head -c "$((MAX_EVIDENCE_REQUEST_BYTES + 1))" > "$request_tmp"
  chown root:universal-video "$request_tmp"
  chmod 0640 "$request_tmp"
  [[ -s "$request_tmp" && "$(stat -c '%s' "$request_tmp")" -le "$MAX_EVIDENCE_REQUEST_BYTES" ]] \
    || fail 'invalid evidence export request size'
  CURRENT_STAGE='evidence_export_request_validate'
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$BASE_DIR/.venv/bin/python" - "$request_tmp" <<'PY'
import sys
from pathlib import Path
from universal_video.evidence_export import MAX_REQUEST_BYTES, _read_regular_json, _validate_request
_validate_request(_read_regular_json(Path(sys.argv[1]), max_bytes=MAX_REQUEST_BYTES))
PY
  install -m 0640 -o root -g universal-video "$request_tmp" "$EVIDENCE_REQUEST_PATH"

  CURRENT_STAGE='evidence_export_status_snapshot'
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$BASE_DIR/.venv/bin/python" -m universal_video.status_attestation > "$status_tmp"
  install -m 0640 -o root -g universal-video "$status_tmp" "$EVIDENCE_STATUS_PATH"
  CURRENT_STAGE='evidence_export_second_running_job_guard'
  [[ -z "$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null)" ]] \
    || fail 'universal-video accepted a job during evidence export'

  CURRENT_STAGE='evidence_export_execute'
  receipt="$(runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$BASE_DIR/.venv/bin/python" "$SOURCE_DIR/ops/universal_video_resident_evidence_export.py")"
  receipt_bytes="$(printf '%s' "$receipt" | wc -c)"
  [[ "$receipt_bytes" -gt 0 && "$receipt_bytes" -le "$MAX_EVIDENCE_RECEIPT_BYTES" ]] \
    || fail 'evidence export receipt size mismatch'
  RECEIPT="$receipt" python3 - <<'PY'
import json, os
x=json.loads(os.environ['RECEIPT'])
assert x.get('schema')=='universal-video-evidence-export-v1', x
assert x.get('state') in {'PASS','INCONCLUSIVE'}, x
assert x.get('publication_state')=='NOT_PUBLISHED', x
assert x.get('school_canon_changed') is False, x
PY
  CURRENT_STAGE='evidence_export_dds3_after'
  verify_dds3
  CURRENT_STAGE='evidence_export_sidecar_after'
  verify_sidecar
  rm -rf "$work"
  trap - EXIT INT TERM
  printf '%s\n' "$receipt"
}

CURRENT_STAGE='argument_validation'
need_root
[[ $# -eq 1 ]] || fail 'usage: universal-video-oci-admin audit|productionize|evidence-export'
case "$1" in
  audit) audit ;;
  productionize) productionize ;;
  evidence-export) evidence_export ;;
  *) fail 'unsupported operation' ;;
esac
