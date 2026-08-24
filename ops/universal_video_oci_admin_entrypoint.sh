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
readonly DRIVE_PROBE_FILE_ID='1RKrDWP6IOfVyuDWRMIsiUT62vpmVW9VS'
readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'
readonly OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly OAUTH_ENV="$BASE_DIR/universal-video-secrets.env"
readonly SAFE_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

fail(){ echo "ERROR: $*" >&2; exit 1; }
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
  verify_protected_services
  verify_dds3
  verify_sidecar
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
  verify_protected_services
  verify_dds3
  verify_sidecar
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
  download_verified "$ACTIVATION_PATH" "$ACTIVATION_BLOB" "$activation"
  download_verified "$PRODUCTIONIZE_PATH" "$PRODUCTIONIZE_BLOB" "$production"

  echo "runtime_commit=$UV_RUNTIME_COMMIT"
  echo 'UNIVERSAL_VIDEO_OCI_ADMIN_PIN_PASS'

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
  /usr/bin/env -i \
    PATH="$SAFE_PATH" HOME=/root LANG=C.UTF-8 \
    UNIVERSAL_VIDEO_SOURCE_DIR="$SOURCE_DIR" \
    UNIVERSAL_VIDEO_DIR="$BASE_DIR" \
    UNIVERSAL_VIDEO_DRIVE_PROBE_FILE_ID="$DRIVE_PROBE_FILE_ID" \
    UNIVERSAL_VIDEO_DRIVE_RESULTS_FOLDER_ID="$DRIVE_RESULTS_FOLDER_ID" \
    UNIVERSAL_VIDEO_MAX_SOURCE_BYTES=17179869184 \
    UNIVERSAL_VIDEO_MAX_DURATION_SECONDS=43200 \
    nice -n 10 bash "$production" | tee "$log_file"

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

  [[ "$(state assistant-lab.service)" == "$before_assistant" ]] || fail 'assistant-lab state changed'
  [[ "$(state assistant-lab-observer.service)" == "$before_observer" ]] || fail 'observer state changed'
  [[ "$(state assistant-lab-control.service)" == "$before_control" ]] || fail 'control state changed'
  [[ "$(state assistant-lab-control-bridge.service)" == "$before_bridge" ]] || fail 'control bridge state changed'
  verify_dds3
  verify_sidecar
  rm -rf "$work"
  trap - EXIT INT TERM
  echo UNIVERSAL_VIDEO_OCI_ADMIN_PRODUCTIONIZE_PASS
}

need_root
[[ $# -eq 1 ]] || fail 'usage: universal-video-oci-admin audit|productionize'
case "$1" in
  audit) audit ;;
  productionize) productionize ;;
  *) fail 'unsupported operation' ;;
esac
