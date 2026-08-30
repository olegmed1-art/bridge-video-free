#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Promote one already-attested image on the Oracle host. The shared GitHub
# concurrency group prevents job submission while this reversible switch runs.
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly OLD_SERVICE='universal-video.service'
readonly NEW_SERVICE='universal-video-container.service'
readonly STATUS='/run/bridge-school/universal-video-status.json'
readonly OPERATOR_TARGET='/usr/local/sbin/universal-video'
readonly OPERATOR_SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'
EXPECTED_COMMIT="${UNIVERSAL_VIDEO_EXPECTED_COMMIT:-}"
EXPECTED_DIGEST="${UNIVERSAL_VIDEO_EXPECTED_IMAGE_DIGEST:-}"
switch_started=0
operator_snapshot_ready=0
operator_existed=0
operator_sudoers_existed=0
operator_backup_root=''
old_enabled_before=''
old_active_before=''
CURRENT_STAGE='validation'

cleanup(){
  if [[ -n "$operator_backup_root" && -d "$operator_backup_root" ]]; then
    rm -rf -- "$operator_backup_root"
  fi
}

fail(){
  local code="$1"
  printf 'UNIVERSAL_VIDEO_CONTAINER_PROMOTION_FAILED code=%s\n' "$code" >&2
  if (( switch_started == 1 )); then
    rollback 1
  fi
  exit 1
}
has_running_job(){ find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; }
emit_runtime_code(){
  local since="@${started_unix:-0}"
  journalctl -u "$NEW_SERVICE" --since "$since" --no-pager -o cat 2>/dev/null | python3 -c '
import json,re,sys
last=None
fallback=None
for line in sys.stdin:
    text=line.lower()
    try: value=json.loads(line)
    except (TypeError,ValueError): value=None
    if (isinstance(value,dict) and set(value)=={"error_code","status"}
            and value.get("status")=="FAILED"
            and re.fullmatch(r"UV_CONTAINER_[A-Z0-9_]+",str(value.get("error_code","")))):
        last=json.dumps(value,separators=(",",":"),sort_keys=True)
    elif "permission denied" in text:
        fallback="UV_CONTAINER_STARTUP_PERMISSION_DENIED"
    elif "no space left on device" in text:
        fallback="UV_CONTAINER_STARTUP_DISK_FULL"
    elif "cannot allocate memory" in text or "out of memory" in text:
        fallback="UV_CONTAINER_STARTUP_MEMORY_UNAVAILABLE"
    elif "container name" in text and "already in use" in text:
        fallback="UV_CONTAINER_STARTUP_NAME_CONFLICT"
    elif "error response from daemon" in text:
        fallback="UV_CONTAINER_DOCKER_RUN_FAILED"
    elif "traceback (most recent call last)" in text:
        fallback="UV_CONTAINER_WORKER_STARTUP_EXCEPTION"
if last:
    print(last)
elif fallback:
    print(json.dumps({"error_code":fallback,"status":"FAILED"},separators=(",",":"),sort_keys=True))
' || true
}
rollback(){
  local rc="${1:-$?}"
  trap - ERR
  emit_runtime_code
  if (( switch_started == 1 )) && ! has_running_job; then
    systemctl disable --now "$NEW_SERVICE" >/dev/null 2>&1 || true
    if [[ "$old_enabled_before" == enabled ]]; then
      systemctl enable "$OLD_SERVICE" >/dev/null 2>&1 || true
    else
      systemctl disable "$OLD_SERVICE" >/dev/null 2>&1 || true
    fi
    if [[ "$old_active_before" == active ]]; then
      systemctl start "$OLD_SERVICE" >/dev/null 2>&1 || true
    else
      systemctl stop "$OLD_SERVICE" >/dev/null 2>&1 || true
    fi
    if (( operator_snapshot_ready == 1 )); then
      if (( operator_existed == 1 )); then
        install -o root -g root -m 0755 "$operator_backup_root/operator" "$OPERATOR_TARGET" || true
      else
        rm -f -- "$OPERATOR_TARGET" || true
      fi
      if (( operator_sudoers_existed == 1 )); then
        install -o root -g root -m 0440 "$operator_backup_root/sudoers" "$OPERATOR_SUDOERS" || true
      else
        rm -f -- "$OPERATOR_SUDOERS" || true
      fi
      visudo -cf /etc/sudoers >/dev/null 2>&1 || true
    fi
  fi
  printf 'UNIVERSAL_VIDEO_CONTAINER_PROMOTION_FAILED code=UV_CONTAINER_PROMOTION_ROLLED_BACK stage=%s rc=%s\n' "$CURRENT_STAGE" "$rc" >&2
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

[[ $(id -u) -eq 0 ]] || fail UV_CONTAINER_PROMOTION_NOT_ROOT
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail UV_CONTAINER_PROMOTION_COMMIT_INVALID
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail UV_CONTAINER_PROMOTION_DIGEST_INVALID
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)" == "$EXPECTED_COMMIT" ]] || fail UV_CONTAINER_PROMOTION_SOURCE_MISMATCH
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail UV_CONTAINER_PROMOTION_SOURCE_DIRTY
[[ -d "$BASE_DIR/spool/running" && ! -L "$BASE_DIR/spool/running" ]] || fail UV_CONTAINER_PROMOTION_SPOOL_UNAVAILABLE
has_running_job && fail UV_CONTAINER_PROMOTION_JOB_RUNNING

CURRENT_STAGE='operator-snapshot'
operator_backup_root="$(mktemp -d)"
if [[ -e "$OPERATOR_TARGET" || -L "$OPERATOR_TARGET" ]]; then
  [[ -f "$OPERATOR_TARGET" && ! -L "$OPERATOR_TARGET" ]] || fail UV_CONTAINER_PROMOTION_OPERATOR_UNSAFE
  install -o root -g root -m 0600 "$OPERATOR_TARGET" "$operator_backup_root/operator"
  operator_existed=1
fi
if [[ -e "$OPERATOR_SUDOERS" || -L "$OPERATOR_SUDOERS" ]]; then
  [[ -f "$OPERATOR_SUDOERS" && ! -L "$OPERATOR_SUDOERS" ]] || fail UV_CONTAINER_PROMOTION_OPERATOR_SUDOERS_UNSAFE
  install -o root -g root -m 0600 "$OPERATOR_SUDOERS" "$operator_backup_root/sudoers"
  operator_sudoers_existed=1
fi
operator_snapshot_ready=1

CURRENT_STAGE='protected-preflight'
before_assistant="$(systemctl is-active assistant-lab.service)"
before_dds="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
BEFORE_DDS="$before_dds" python3 - <<'PY'
import json,os
x=json.loads(os.environ['BEFORE_DDS'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
PY

CURRENT_STAGE='image-preflight'
observed="$(docker image inspect --format '{{.Id}}' "bridge-school/universal-video:$EXPECTED_COMMIT")"
[[ "$observed" == "$EXPECTED_DIGEST" ]] || fail UV_CONTAINER_PROMOTION_IMAGE_MISMATCH
started_unix="$(date +%s)"
old_enabled_before="$(systemctl is-enabled "$OLD_SERVICE" 2>/dev/null || true)"
old_active_before="$(systemctl is-active "$OLD_SERVICE" 2>/dev/null || true)"
switch_started=1
CURRENT_STAGE='legacy-quiesce'
systemctl disable --now "$OLD_SERVICE" || fail UV_CONTAINER_PROMOTION_LEGACY_QUIESCE_FAILED
CURRENT_STAGE='installer-activation'
UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=1 UNIVERSAL_VIDEO_CONTAINER_BUILD=0 bash "$SOURCE_DIR/ops/oracle_universal_video_container_install.sh"
CURRENT_STAGE='service-verification'
service_deadline=$((SECONDS + 30))
service_ready=0
while (( SECONDS < service_deadline )); do
  if systemctl is-active --quiet "$NEW_SERVICE"; then
    service_ready=1
    break
  fi
  sleep 1
done
(( service_ready == 1 )) || fail UV_CONTAINER_PROMOTION_SERVICE_INACTIVE
[[ "$(systemctl is-enabled "$NEW_SERVICE")" == enabled ]] || fail UV_CONTAINER_PROMOTION_SERVICE_DISABLED
systemctl is-active --quiet "$OLD_SERVICE" && fail UV_CONTAINER_PROMOTION_LEGACY_ACTIVE
[[ "$(systemctl is-enabled "$OLD_SERVICE" 2>/dev/null || true)" == disabled ]] || fail UV_CONTAINER_PROMOTION_LEGACY_ENABLED
process_deadline=$((SECONDS + 30))
process_ready=0
while (( SECONDS < process_deadline )); do
  if [[ "$(docker inspect --format '{{.State.Running}}' universal-video-container 2>/dev/null || true)" == true ]]; then
    process_ready=1
    break
  fi
  systemctl is-active --quiet "$NEW_SERVICE" || break
  sleep 1
done
(( process_ready == 1 )) || fail UV_CONTAINER_PROMOTION_PROCESS_INACTIVE
[[ "$(docker inspect --format '{{.Image}}' universal-video-container)" == "$EXPECTED_DIGEST" ]] || fail UV_CONTAINER_PROMOTION_RUNNING_IMAGE_MISMATCH

CURRENT_STAGE='resident-status'
deadline=$((SECONDS + 45))
fresh_status=0
while (( SECONDS < deadline )); do
  if [[ -f "$STATUS" && ! -L "$STATUS" ]] && STATUS_PATH="$STATUS" EXPECTED_COMMIT="$EXPECTED_COMMIT" STARTED_UNIX="$started_unix" python3 - <<'PY'
import json,os
x=json.load(open(os.environ['STATUS_PATH'],encoding='utf-8'))
assert x.get('schema') == 'universal-video-resident-status-v2'
assert x.get('instance_state') == 'RUNNING'
assert x.get('installed_runtime_commit') == os.environ['EXPECTED_COMMIT']
assert x.get('active_jobs') == []
assert float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])
PY
  then
    fresh_status=1
    break
  fi
  sleep 2
done
if (( fresh_status != 1 )); then
  [[ -f "$STATUS" && ! -L "$STATUS" ]] || fail UV_CONTAINER_PROMOTION_STATUS_MISSING
  fail UV_CONTAINER_PROMOTION_STATUS_STALE
fi
STATUS_PATH="$STATUS" EXPECTED_COMMIT="$EXPECTED_COMMIT" STARTED_UNIX="$started_unix" python3 - <<'PY'
import json,os
x=json.load(open(os.environ['STATUS_PATH'],encoding='utf-8'))
assert x.get('schema') == 'universal-video-resident-status-v2'
assert x.get('instance_state') == 'RUNNING'
assert x.get('installed_runtime_commit') == os.environ['EXPECTED_COMMIT']
assert x.get('active_jobs') == []
assert float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])
PY

CURRENT_STAGE='operator-sync'
SOURCE_FILE="$SOURCE_DIR/ops/universal_video_operator.sh" \
EXPECTED_RUNTIME_COMMIT="$EXPECTED_COMMIT" \
  bash "$SOURCE_DIR/ops/install_universal_video_operator.sh"
[[ "$(git hash-object "$OPERATOR_TARGET")" == "$(git -C "$SOURCE_DIR" rev-parse "$EXPECTED_COMMIT:ops/universal_video_operator.sh")" ]] || fail UV_CONTAINER_PROMOTION_OPERATOR_MISMATCH
set +e
operator_smoke="$(sudo -u ocarun sudo -n "$OPERATOR_TARGET" status .. 2>&1)"
operator_smoke_rc=$?
set -e
[[ "$operator_smoke_rc" -eq 1 ]] || fail UV_CONTAINER_PROMOTION_OPERATOR_SMOKE_RC
grep -Fx 'UV_STATE=REJECTED' <<<"$operator_smoke" >/dev/null || fail UV_CONTAINER_PROMOTION_OPERATOR_SMOKE_STATE
grep -Fx 'UV_ERROR=invalid job id' <<<"$operator_smoke" >/dev/null || fail UV_CONTAINER_PROMOTION_OPERATOR_SMOKE_REASON

CURRENT_STAGE='protected-postflight'
[[ "$(systemctl is-active assistant-lab.service)" == "$before_assistant" ]] || fail UV_CONTAINER_PROMOTION_PROTECTED_SERVICE_CHANGED
after_dds="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
BEFORE_DDS="$before_dds" AFTER_DDS="$after_dds" python3 - <<'PY'
import json,os
before=json.loads(os.environ['BEFORE_DDS']); after=json.loads(os.environ['AFTER_DDS'])
for x in (before,after):
    assert x.get('status') == 'ready'
    assert x.get('engine') == 'DDS3'
    assert x.get('fallback_used') is False
PY
switch_started=0
CURRENT_STAGE='complete'
printf 'UNIVERSAL_VIDEO_CONTAINER_PROMOTION_PASS commit=%s image_digest=%s fallback_used=false active_jobs=0\n' "$EXPECTED_COMMIT" "$EXPECTED_DIGEST"
