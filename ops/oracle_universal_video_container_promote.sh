#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Promote one already-attested image on the Oracle host. The shared GitHub
# concurrency group prevents job submission while this reversible switch runs.
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly OLD_SERVICE='universal-video.service'
readonly NEW_SERVICE='universal-video-container.service'
readonly NEW_SERVICE_UNIT='/etc/systemd/system/universal-video-container.service'
readonly STATUS='/run/bridge-school/universal-video-status.json'
readonly WORKLOAD_LOCK="$BASE_DIR/spool/.workload.lock"
readonly OPERATOR_TARGET='/usr/local/sbin/universal-video'
readonly OPERATOR_SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'
EXPECTED_COMMIT="${UNIVERSAL_VIDEO_EXPECTED_COMMIT:-}"
EXPECTED_DIGEST="${UNIVERSAL_VIDEO_EXPECTED_IMAGE_DIGEST:-}"
switch_started=0
workload_lock_held=0
operator_snapshot_ready=0
operator_existed=0
operator_sudoers_existed=0
operator_backup_root=''
old_enabled_before=''
old_active_before=''
new_enabled_before=''
new_active_before=''
CURRENT_STAGE='validation'

cleanup(){
  if [[ -n "$operator_backup_root" && -d "$operator_backup_root" ]]; then
    rm -rf -- "$operator_backup_root"
  fi
}

release_workload_fence(){
  if (( workload_lock_held == 1 )); then
    flock --unlock 9 >/dev/null 2>&1 || true
    exec 9<&-
    workload_lock_held=0
  fi
}

acquire_workload_fence(){
  if (( workload_lock_held == 1 )); then
    return 0
  fi
  exec 9<"$WORKLOAD_LOCK" || return 1
  if ! flock --exclusive --nonblock 9; then
    exec 9<&-
    return 1
  fi
  workload_lock_held=1
}

service_state_is_known(){
  local enabled_state="$1" active_state="$2"
  case "$enabled_state" in
    enabled|disabled|static|indirect|masked|masked-runtime|not-found) ;;
    *) return 1 ;;
  esac
  case "$active_state" in
    active|inactive|failed) ;;
    *) return 1 ;;
  esac
}

service_matches_captured_state(){
  local service="$1" expected_enabled="$2" expected_active="$3"
  local observed_enabled observed_active
  observed_enabled="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  observed_active="$(systemctl is-active "$service" 2>/dev/null || true)"
  service_state_is_known "$observed_enabled" "$observed_active" || return 1
  [[ "$observed_enabled" == "$expected_enabled" ]] || return 1
  [[ "$observed_active" == "$expected_active" ]] || return 1
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
pid_descends_from(){
  local child="$1" ancestor="$2" parent hops=0
  [[ "$child" =~ ^[1-9][0-9]*$ && "$ancestor" =~ ^[1-9][0-9]*$ ]] || return 1
  while (( child > 1 && hops < 64 )); do
    [[ "$child" == "$ancestor" ]] && return 0
    parent="$(ps -o ppid= -p "$child" 2>/dev/null | tr -d '[:space:]')"
    [[ "$parent" =~ ^[0-9]+$ && "$parent" != "$child" ]] || return 1
    child="$parent"
    ((hops += 1))
  done
  [[ "$child" == "$ancestor" ]]
}
process_start_ticks(){
  local process_id="$1"
  [[ "$process_id" =~ ^[1-9][0-9]*$ ]] || return 1
  PROCESS_STAT="/proc/$process_id/stat" python3 - <<'PY'
import os
from pathlib import Path

raw = Path(os.environ["PROCESS_STAT"]).read_text(encoding="utf-8")
tail = raw.rsplit(")", 1)
if len(tail) != 2:
    raise SystemExit(1)
fields = tail[1].split()
if len(fields) <= 19:
    raise SystemExit(1)
value = int(fields[19])
if value <= 0:
    raise SystemExit(1)
print(value)
PY
}
resident_worker_pid(){
  local container_root_pid="$1" candidate
  local -a matches=()
  while read -r candidate; do
    if pid_descends_from "$candidate" "$container_root_pid"; then
      matches+=("$candidate")
    fi
  done < <(pgrep -f '[u]niversal_video[.]spool_worker' || true)
  [[ "${#matches[@]}" -eq 1 ]] || return 1
  printf '%s\n' "${matches[0]}"
}
resident_status_ready(){
  local container_root_pid worker_pid expected_process_id expected_process_start_ticks
  [[ -f "$STATUS" && ! -L "$STATUS" ]] || return 1
  container_root_pid="$(docker inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)"
  [[ "$container_root_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  worker_pid="$(resident_worker_pid "$container_root_pid" 2>/dev/null || true)"
  [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  expected_process_id="$(awk '$1 == "NSpid:" {print $NF}' "/proc/$worker_pid/status" 2>/dev/null || true)"
  expected_process_start_ticks="$(process_start_ticks "$worker_pid" 2>/dev/null || true)"
  [[ "$expected_process_id" =~ ^[1-9][0-9]*$ && "$expected_process_start_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  if ! STATUS_PATH="$STATUS" EXPECTED_COMMIT="$EXPECTED_COMMIT" STARTED_UNIX="$started_unix" \
      EXPECTED_PROCESS_ID="$expected_process_id" \
      EXPECTED_PROCESS_START_TICKS="$expected_process_start_ticks" \
      python3 - <<'PY'
import json, os, re
from pathlib import Path

path = Path(os.environ['STATUS_PATH'])
if path.stat().st_size > 1024 * 1024:
    raise SystemExit(1)
x = json.loads(path.read_text(encoding='utf-8'))
assert x.get('schema') == 'universal-video-resident-status-v2'
assert x.get('instance_state') == 'RUNNING'
assert x.get('installed_runtime_commit') == os.environ['EXPECTED_COMMIT']
assert x.get('active_jobs') == []
assert float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])
assert x.get('resident_id') == 'container'
assert type(x.get('process_id')) is int
assert x['process_id'] == int(os.environ['EXPECTED_PROCESS_ID'])
assert float(x.get('process_started_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])
assert float(x.get('process_started_at_unix') or 0) <= float(x.get('observed_at_unix') or 0)
assert type(x.get('process_start_ticks')) is int
assert x['process_start_ticks'] == int(os.environ['EXPECTED_PROCESS_START_TICKS'])
assert isinstance(x.get('process_nonce'), str) and re.fullmatch(r'[0-9a-f]{32}', x['process_nonce'])
PY
  then
    return 1
  fi
  [[ "$(docker inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)" == "$container_root_pid" ]] || return 1
  pid_descends_from "$worker_pid" "$container_root_pid" || return 1
  [[ "$(awk '$1 == "NSpid:" {print $NF}' "/proc/$worker_pid/status" 2>/dev/null || true)" == "$expected_process_id" ]] || return 1
  [[ "$(process_start_ticks "$worker_pid" 2>/dev/null || true)" == "$expected_process_start_ticks" ]] || return 1
}
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
  local rollback_failed=0
  trap - ERR
  emit_runtime_code
  if (( switch_started == 1 )); then
    if has_running_job || ! acquire_workload_fence; then
      rollback_failed=1
    else
    # Recheck local work after acquiring the same fence used by Neon claims.
    # Stop the replacement under that fence, then release before starting a
    # restored resident, whose startup recovery also needs the fence.
    if has_running_job; then
      rollback_failed=1
      release_workload_fence
    else
    systemctl disable --now "$NEW_SERVICE" >/dev/null 2>&1 || rollback_failed=1
    release_workload_fence
    if [[ "$new_enabled_before" == not-found ]]; then
      rm -f -- "$NEW_SERVICE_UNIT" || rollback_failed=1
      systemctl daemon-reload >/dev/null 2>&1 || rollback_failed=1
    elif [[ "$new_enabled_before" == enabled ]]; then
      systemctl enable "$NEW_SERVICE" >/dev/null 2>&1 || true
    else
      systemctl disable "$NEW_SERVICE" >/dev/null 2>&1 || true
    fi
    if [[ "$new_enabled_before" == not-found ]]; then
      :
    elif [[ "$new_active_before" == active ]]; then
      systemctl start "$NEW_SERVICE" >/dev/null 2>&1 || true
    else
      systemctl stop "$NEW_SERVICE" >/dev/null 2>&1 || true
    fi
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
    service_matches_captured_state "$NEW_SERVICE" "$new_enabled_before" "$new_active_before" \
      || rollback_failed=1
    service_matches_captured_state "$OLD_SERVICE" "$old_enabled_before" "$old_active_before" \
      || rollback_failed=1
    fi
    fi
  fi
  if (( rollback_failed == 1 )); then
    printf 'UNIVERSAL_VIDEO_CONTAINER_PROMOTION_FAILED code=UV_CONTAINER_PROMOTION_ROLLBACK_FAILED stage=%s rc=%s\n' "$CURRENT_STAGE" "$rc" >&2
    exit 1
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
[[ -f "$WORKLOAD_LOCK" && ! -L "$WORKLOAD_LOCK" ]] \
  || fail UV_CONTAINER_PROMOTION_WORKLOAD_LOCK_INVALID
[[ "$(stat -c '%U:%G:%a:%h' "$WORKLOAD_LOCK" 2>/dev/null || true)" == 'root:universal-video:640:1' ]] \
  || fail UV_CONTAINER_PROMOTION_WORKLOAD_LOCK_INVALID
CURRENT_STAGE='workload-fence'
exec 9<"$WORKLOAD_LOCK"
flock --exclusive --nonblock 9 || fail UV_CONTAINER_PROMOTION_WORKLOAD_BUSY
workload_lock_held=1
has_running_job && fail UV_CONTAINER_PROMOTION_JOB_RUNNING

CURRENT_STAGE='queue-credential-preflight'
queue_dsn_file="$BASE_DIR/secrets/video-queue-dsn"
[[ -f "$queue_dsn_file" && ! -L "$queue_dsn_file" ]] \
  || fail UV_CONTAINER_PROMOTION_QUEUE_CREDENTIAL_MISSING
queue_dsn_meta="$(stat -c '%U:%G:%a:%s' "$queue_dsn_file" 2>/dev/null || true)"
[[ "$queue_dsn_meta" =~ ^root:universal-video:640:([1-9][0-9]{0,3})$ ]] \
  || fail UV_CONTAINER_PROMOTION_QUEUE_CREDENTIAL_METADATA_INVALID
(( BASH_REMATCH[1] <= 4096 )) \
  || fail UV_CONTAINER_PROMOTION_QUEUE_CREDENTIAL_OVERSIZED
python3 "$SOURCE_DIR/ops/validate_video_queue_dsn.py" "$queue_dsn_file" >/dev/null \
  || fail UV_CONTAINER_PROMOTION_QUEUE_CREDENTIAL_INVALID

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
import json,os,re
x=json.loads(os.environ['BEFORE_DDS'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
PY

CURRENT_STAGE='image-preflight'
observed="$(docker image inspect --format '{{.Id}}' "bridge-school/universal-video:$EXPECTED_COMMIT")"
[[ "$observed" == "$EXPECTED_DIGEST" ]] || fail UV_CONTAINER_PROMOTION_IMAGE_MISMATCH
CURRENT_STAGE='speaker-model-preflight'
runtime_uid="$(id -u universal-video)"
runtime_gid="$(id -g universal-video)"
[[ -d "$BASE_DIR/model-cache/speaker" && ! -L "$BASE_DIR/model-cache/speaker" ]] \
  || fail UV_CONTAINER_PROMOTION_SPEAKER_CACHE_UNAVAILABLE
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  --user="$runtime_uid:$runtime_gid" \
  --env "UNIVERSAL_VIDEO_SOURCE_COMMIT=$EXPECTED_COMMIT" \
  --env UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool \
  --env UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output \
  --env UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media \
  --env UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE=/var/lib/universal-video/model-cache/speaker \
  --env HF_HOME=/var/lib/universal-video/model-cache \
  --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" \
  --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" \
  --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" \
  --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" \
  "$EXPECTED_DIGEST" true \
  || fail UV_CONTAINER_PROMOTION_SPEAKER_MODEL_INVALID
started_unix="$(date +%s)"
old_enabled_before="$(systemctl is-enabled "$OLD_SERVICE" 2>/dev/null || true)"
old_active_before="$(systemctl is-active "$OLD_SERVICE" 2>/dev/null || true)"
new_enabled_before="$(systemctl is-enabled "$NEW_SERVICE" 2>/dev/null || true)"
new_active_before="$(systemctl is-active "$NEW_SERVICE" 2>/dev/null || true)"
service_state_is_known "$old_enabled_before" "$old_active_before" \
  || fail UV_CONTAINER_PROMOTION_LEGACY_STATE_UNKNOWN
service_state_is_known "$new_enabled_before" "$new_active_before" \
  || fail UV_CONTAINER_PROMOTION_CONTAINER_STATE_UNKNOWN
[[ "$old_active_before" != active || "$new_active_before" != active ]] \
  || fail UV_CONTAINER_PROMOTION_DUAL_RESIDENT
switch_started=1
CURRENT_STAGE='legacy-quiesce'
systemctl disable --now "$OLD_SERVICE" || fail UV_CONTAINER_PROMOTION_LEGACY_QUIESCE_FAILED
CURRENT_STAGE='container-quiesce'
if [[ "$new_active_before" == active || "$new_enabled_before" == enabled ]]; then
  systemctl disable --now "$NEW_SERVICE" || fail UV_CONTAINER_PROMOTION_CONTAINER_QUIESCE_FAILED
fi
CURRENT_STAGE='workload-handoff'
if ! flock --unlock 9; then
  fail UV_CONTAINER_PROMOTION_WORKLOAD_UNLOCK_FAILED
fi
exec 9<&-
workload_lock_held=0
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
  if resident_status_ready; then
    fresh_status=1
    break
  fi
  sleep 2
done
if (( fresh_status != 1 )); then
  [[ -f "$STATUS" && ! -L "$STATUS" ]] || fail UV_CONTAINER_PROMOTION_STATUS_MISSING
  fail UV_CONTAINER_PROMOTION_STATUS_STALE
fi

CURRENT_STAGE='operator-install'
if ! operator_install_output="$(
  SOURCE_FILE="$SOURCE_DIR/ops/universal_video_operator.sh" \
  EXPECTED_RUNTIME_COMMIT="$EXPECTED_COMMIT" \
    bash "$SOURCE_DIR/ops/install_universal_video_operator.sh" 2>&1
)"; then
  case "$operator_install_output" in
    *'operator source must be regular'*) code=UV_CONTAINER_PROMOTION_OPERATOR_SOURCE_UNSAFE ;;
    *'invalid runtime commit'*) code=UV_CONTAINER_PROMOTION_OPERATOR_COMMIT_INVALID ;;
    *'runtime commit mismatch'*) code=UV_CONTAINER_PROMOTION_OPERATOR_COMMIT_MISMATCH ;;
    *'runtime checkout is dirty'*) code=UV_CONTAINER_PROMOTION_OPERATOR_SOURCE_DIRTY ;;
    *'operator does not match checkout'*) code=UV_CONTAINER_PROMOTION_OPERATOR_SOURCE_MISMATCH ;;
    *'staging directory install failed'*) code=UV_CONTAINER_PROMOTION_OPERATOR_STAGING_FAILED ;;
    *'temporary sudoers file unavailable'*) code=UV_CONTAINER_PROMOTION_OPERATOR_TEMP_UNAVAILABLE ;;
    *'temporary sudoers mode failed'*) code=UV_CONTAINER_PROMOTION_OPERATOR_TEMP_MODE_FAILED ;;
    *'operator sudoers validation failed'*) code=UV_CONTAINER_PROMOTION_OPERATOR_SUDOERS_INVALID ;;
    *'operator target install failed'*) code=UV_CONTAINER_PROMOTION_OPERATOR_TARGET_INSTALL_FAILED ;;
    *'operator sudoers install failed'*) code=UV_CONTAINER_PROMOTION_OPERATOR_SUDOERS_INSTALL_FAILED ;;
    *'system sudoers validation failed'*) code=UV_CONTAINER_PROMOTION_SYSTEM_SUDOERS_INVALID ;;
    *'post-retirement sudoers validation failed'*) code=UV_CONTAINER_PROMOTION_POST_RETIREMENT_SUDOERS_INVALID ;;
    *'unsafe obsolete ingress target:'*) code=UV_CONTAINER_PROMOTION_OPERATOR_OBSOLETE_UNSAFE ;;
    *) code=UV_CONTAINER_PROMOTION_OPERATOR_INSTALL_FAILED ;;
  esac
  fail "$code"
fi
grep -Fx 'UNIVERSAL_VIDEO_OPERATOR_INSTALL_PASS' <<<"$operator_install_output" >/dev/null || fail UV_CONTAINER_PROMOTION_OPERATOR_INSTALL_ATTESTATION_MISSING
CURRENT_STAGE='operator-blob'
[[ "$(git hash-object "$OPERATOR_TARGET")" == "$(git -C "$SOURCE_DIR" rev-parse "$EXPECTED_COMMIT:ops/universal_video_operator.sh")" ]] || fail UV_CONTAINER_PROMOTION_OPERATOR_MISMATCH
CURRENT_STAGE='operator-smoke'
if operator_smoke="$(sudo -u ocarun sudo -n "$OPERATOR_TARGET" status .. 2>&1)"; then
  operator_smoke_rc=0
else
  operator_smoke_rc=$?
fi
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
CURRENT_STAGE='resident-status-final'
resident_status_ready || fail UV_CONTAINER_PROMOTION_STATUS_STALE
switch_started=0
CURRENT_STAGE='complete'
printf 'UNIVERSAL_VIDEO_CONTAINER_PROMOTION_PASS commit=%s image_digest=%s fallback_used=false active_jobs=0\n' "$EXPECTED_COMMIT" "$EXPECTED_DIGEST"
