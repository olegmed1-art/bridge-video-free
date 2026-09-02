#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
STATUS_DIR="${UNIVERSAL_VIDEO_STATUS_DIR:-/run/bridge-school}"
STATUS_FILE="$STATUS_DIR/universal-video-status.json"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"
SOURCE_SERVICE="${UNIVERSAL_VIDEO_SERVICE_NAME:-universal-video.service}"
CONTAINER_SERVICE="universal-video-container.service"
WORKLOAD_LOCK="$BASE_DIR/spool/.workload.lock"
EXPECTED_SHA="${UNIVERSAL_VIDEO_EXPECTED_SHA:-}"
PREPARE_SCRIPT="${UNIVERSAL_VIDEO_PREPARE_SCRIPT:-}"
BUILD_IMAGE="${UNIVERSAL_VIDEO_PRECANARY_BUILD_IMAGE:-0}"
MIN_FREE_KB="${UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB:-5242880}"
RECLAIM_ROOT_CACHE="${UNIVERSAL_VIDEO_RECLAIM_ROOT_CACHE:-0}"
RECOVER_CONTAINER_FROM_RUN="${UNIVERSAL_VIDEO_RECOVER_CONTAINER_ACTIVE_FROM_RUN:-}"
RECOVERY_EVIDENCE_FILE="${UNIVERSAL_VIDEO_RECOVERY_EVIDENCE_FILE:-}"
RECOVERY_EVIDENCE_SHA256="${UNIVERSAL_VIDEO_RECOVERY_EVIDENCE_SHA256:-}"
RESTORE_TIMEOUT_SECONDS="${UNIVERSAL_VIDEO_RESTORE_TIMEOUT_SECONDS:-45}"
RESTORE_STABLE_SECONDS="${UNIVERSAL_VIDEO_RESTORE_STABLE_SECONDS:-5}"
QUEUE_DSN_FILE="${UNIVERSAL_VIDEO_QUEUE_DSN_FILE:-$BASE_DIR/secrets/video-queue-dsn}"
QUEUE_PYTHON="${UNIVERSAL_VIDEO_QUEUE_PYTHON:-$BASE_DIR/.venv/bin/python}"
ENV_FILE="${UNIVERSAL_VIDEO_CONTAINER_ENV_FILE:-}"
PERSISTENT_ENV_FILE="$BASE_DIR/universal-video-container.env"
if [[ -z "$ENV_FILE" ]]; then
  if [[ "$BUILD_IMAGE" == 1 ]]; then
    ENV_FILE="$BASE_DIR/universal-video-container-candidate.env"
  else
    ENV_FILE="$BASE_DIR/universal-video-container.env"
  fi
fi
FILE_ID="${UNIVERSAL_VIDEO_CANARY_FILE_ID:?missing exact canary file id}"
NAME="${UNIVERSAL_VIDEO_CANARY_NAME:?missing exact canary name}"
MIME="${UNIVERSAL_VIDEO_CANARY_MIME:?missing exact canary MIME}"
SIZE="${UNIVERSAL_VIDEO_CANARY_SIZE:?missing exact canary size}"
PARENT="${UNIVERSAL_VIDEO_CANARY_PARENT:?missing exact canary parent}"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die 'run as root on the Oracle host'
[[ "$SIZE" =~ ^[0-9]+$ && "$SIZE" -gt 0 ]] || die 'invalid source size'
[[ "$BUILD_IMAGE" =~ ^[01]$ ]] || die 'UNIVERSAL_VIDEO_PRECANARY_BUILD_IMAGE must be 0 or 1'
[[ "$RECLAIM_ROOT_CACHE" =~ ^[01]$ ]] || die 'UNIVERSAL_VIDEO_RECLAIM_ROOT_CACHE must be 0 or 1'
[[ "$MIN_FREE_KB" =~ ^[0-9]+$ && "$MIN_FREE_KB" -gt 0 ]] || die 'invalid build free-space threshold'
[[ "$RESTORE_TIMEOUT_SECONDS" =~ ^[0-9]+$ && "$RESTORE_TIMEOUT_SECONDS" -ge 5 && "$RESTORE_TIMEOUT_SECONDS" -le 180 ]] \
  || die 'invalid resident restore timeout'
[[ "$RESTORE_STABLE_SECONDS" =~ ^[0-9]+$ && "$RESTORE_STABLE_SECONDS" -ge 3 && "$RESTORE_STABLE_SECONDS" -le 30 ]] \
  || die 'invalid resident stability interval'
[[ -z "$RECOVER_CONTAINER_FROM_RUN" || "$RECOVER_CONTAINER_FROM_RUN" =~ ^[0-9]{8,20}$ ]] \
  || die 'invalid bounded prior-run recovery reference'
if [[ -n "$RECOVER_CONTAINER_FROM_RUN" ]]; then
  [[ "$RECOVERY_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] \
    || die 'immutable prior-run recovery evidence digest is required'
  [[ -f "$RECOVERY_EVIDENCE_FILE" && ! -L "$RECOVERY_EVIDENCE_FILE" ]] \
    || die 'immutable prior-run recovery evidence file is unsafe or missing'
else
  [[ -z "$RECOVERY_EVIDENCE_FILE" && -z "$RECOVERY_EVIDENCE_SHA256" ]] \
    || die 'unrequested prior-run recovery evidence is forbidden'
fi
case "$SOURCE_DIR" in
  /opt/bridge-school/*) ;;
  *) die 'source checkout path is outside the bounded bridge-school root' ;;
esac
[[ "$SOURCE_DIR" != /opt/bridge-school && "$SOURCE_DIR" != /opt/bridge-school/ ]] \
  || die 'source checkout path is too broad'
if [[ -n "$EXPECTED_SHA" ]]; then
  [[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'invalid expected source SHA'
fi
if [[ "$BUILD_IMAGE" == 1 ]]; then
  [[ -n "$EXPECTED_SHA" ]] || die 'exact source SHA required for build'
  [[ -f "$PREPARE_SCRIPT" && ! -L "$PREPARE_SCRIPT" ]] || die 'safe prepare script missing'
fi

source_state_before=""
container_state_before=""
source_was_active=0
container_was_active=0
container_recovery_requested=0
container_target_state=""
window_started=0
services_stop_attempted=0
lock_held=0
source_had_original=0
source_candidate_path_owned=0
source_backup_dir=""
resident_image_id=""
declare -a added_runtime_masks=()
declare -a restore_failures=()

service_state(){
  systemctl show "$1" --property=ActiveState --value 2>/dev/null || true
}

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
  local service="$1" root_pid worker_pid
  local -a matches=()
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    root_pid="$(systemctl show "$service" --property=MainPID --value 2>/dev/null || true)"
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    root_pid="$(docker inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)"
  else
    return 1
  fi
  [[ "$root_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  while read -r worker_pid; do
    if pid_descends_from "$worker_pid" "$root_pid"; then
      matches+=("$worker_pid")
    fi
  done < <(pgrep -f '[u]niversal_video[.]spool_worker' || true)
  [[ "${#matches[@]}" -eq 1 ]] || return 1
  printf '%s\n' "${matches[0]}"
}

restored_service_ready(){
  local service="$1" target_state="$2" worker_pid
  [[ "$(service_state "$service")" == "$target_state" ]] || return 1
  [[ "$target_state" == inactive ]] && return 0
  worker_pid="$(resident_worker_pid "$service")" || return 1
  [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]]
}

resident_expected_commit(){
  local service="$1" image_id
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    image_id="$(docker inspect --format '{{.Image}}' universal-video-container 2>/dev/null || true)"
    docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_id" 2>/dev/null || true
  else
    return 1
  fi
}

clear_restore_status(){
  if [[ -L "$STATUS_FILE" || -f "$STATUS_FILE" ]]; then
    rm -f -- "$STATUS_FILE"
  elif [[ -e "$STATUS_FILE" ]]; then
    return 1
  fi
}

resident_status_ready(){
  local service="$1" started_unix="$2" worker_pid="$3" expected_commit expected_resident expected_process_id expected_process_start_ticks peer_service peer_commit legacy_peer_same_commit=0
  [[ -f "$STATUS_FILE" && ! -L "$STATUS_FILE" ]] || return 1
  [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    expected_resident=source
    expected_process_id="$worker_pid"
    peer_service="$CONTAINER_SERVICE"
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    expected_resident=container
    expected_process_id="$(awk '$1 == "NSpid:" {print $NF}' "/proc/$worker_pid/status" 2>/dev/null || true)"
    peer_service="$SOURCE_SERVICE"
  else
    return 1
  fi
  expected_commit="$(resident_expected_commit "$service")"
  [[ "$expected_process_id" =~ ^[1-9][0-9]*$ ]] || return 1
  expected_process_start_ticks="$(process_start_ticks "$worker_pid" 2>/dev/null || true)"
  [[ "$expected_process_start_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || return 1
  if [[ "$(service_state "$peer_service")" == active ]]; then
    peer_commit="$(resident_expected_commit "$peer_service")"
    legacy_peer_same_commit=1
    if [[ "$peer_commit" =~ ^[0-9a-f]{40}$ && "$peer_commit" != "$expected_commit" ]]; then
      legacy_peer_same_commit=0
    fi
  fi
  STATUS_PATH="$STATUS_FILE" EXPECTED_COMMIT="$expected_commit" \
    EXPECTED_RESIDENT="$expected_resident" EXPECTED_PROCESS_ID="$expected_process_id" \
    EXPECTED_PROCESS_START_TICKS="$expected_process_start_ticks" \
    STARTED_UNIX="$started_unix" LEGACY_PEER_SAME_COMMIT="$legacy_peer_same_commit" \
    python3 - <<'PY' >/dev/null 2>&1
import json
import math
import os
import re
import time
from pathlib import Path

path = Path(os.environ["STATUS_PATH"])
if path.stat().st_size > 1024 * 1024:
    raise SystemExit(1)
value = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(value, dict):
    raise SystemExit(1)
try:
    observed_at = float(value.get("observed_at_unix"))
except (TypeError, ValueError):
    raise SystemExit(1)
if not (
    value.get("schema") == "universal-video-resident-status-v2"
    and value.get("instance_state") == "RUNNING"
    and value.get("active_jobs") == []
    and math.isfinite(observed_at)
    and observed_at >= int(os.environ["STARTED_UNIX"])
    and observed_at <= time.time() + 5
    and value.get("installed_runtime_commit") == os.environ["EXPECTED_COMMIT"]
):
    raise SystemExit(1)

identity_fields = {
    "resident_id", "process_id", "process_started_at_unix",
    "process_start_ticks", "process_nonce",
}
present = identity_fields.intersection(value)
strong = identity_fields
transitional = identity_fields - {"process_start_ticks"}
if not present:
    if os.environ["LEGACY_PEER_SAME_COMMIT"] != "0":
        raise SystemExit(1)
elif present == transitional or present == strong:
    try:
        process_started_at = float(value.get("process_started_at_unix"))
    except (TypeError, ValueError):
        raise SystemExit(1)
    if not (
        value.get("resident_id") == os.environ["EXPECTED_RESIDENT"]
        and type(value.get("process_id")) is int
        and value["process_id"] == int(os.environ["EXPECTED_PROCESS_ID"])
        and math.isfinite(process_started_at)
        and process_started_at >= int(os.environ["STARTED_UNIX"])
        and process_started_at <= observed_at
        and isinstance(value.get("process_nonce"), str)
        and re.fullmatch(r"[0-9a-f]{32}", value["process_nonce"])
    ):
        raise SystemExit(1)
    if present == strong and not (
        type(value.get("process_start_ticks")) is int
        and value["process_start_ticks"] == int(os.environ["EXPECTED_PROCESS_START_TICKS"])
    ):
        raise SystemExit(1)
else:
    raise SystemExit(1)
PY
}

record_restore_failure(){
  restore_failures+=("$1")
}

service_failure_snapshot(){
  local service="$1" state result code status restarts
  state="$(service_state "$service")"
  result="$(systemctl show "$service" --property=Result --value 2>/dev/null || true)"
  code="$(systemctl show "$service" --property=ExecMainCode --value 2>/dev/null || true)"
  status="$(systemctl show "$service" --property=ExecMainStatus --value 2>/dev/null || true)"
  restarts="$(systemctl show "$service" --property=NRestarts --value 2>/dev/null || true)"
  printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE service=%s result=FAILED state=%s unit_result=%s exec_code=%s exec_status=%s restarts=%s\n' \
    "$service" "${state:-unknown}" "${result:-unknown}" "${code:-unknown}" "${status:-unknown}" "${restarts:-unknown}" >&2
}

restore_service(){
  local service="$1" target_state="$2" attempt state worker_pid last_worker_pid="" stable=0 started_unix
  if [[ "$target_state" == inactive ]]; then
    state="$(service_state "$service")"
    case "$state" in
      inactive)
        printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE service=%s target=inactive observed=%s result=PASS\n' "$service" "$state"
        return 0
        ;;
      *)
        service_failure_snapshot "$service"
        return 1
        ;;
    esac
  elif [[ "$target_state" != active ]]; then
    return 1
  fi

  systemctl reset-failed "$service" >/dev/null 2>&1 || true
  if ! clear_restore_status; then
    service_failure_snapshot "$service"
    return 1
  fi
  started_unix="$(date +%s)"
  if ! systemctl start "$service" >/dev/null 2>&1; then
    service_failure_snapshot "$service"
    return 1
  fi
  for (( attempt=0; attempt<RESTORE_TIMEOUT_SECONDS; attempt++ )); do
    state="$(service_state "$service")"
    if [[ "$state" == active ]]; then
      worker_pid="$(resident_worker_pid "$service" 2>/dev/null || true)"
      if [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]]; then
        if [[ "$worker_pid" == "$last_worker_pid" ]]; then
          ((stable += 1))
        else
          if ! clear_restore_status; then
            service_failure_snapshot "$service"
            return 1
          fi
          last_worker_pid="$worker_pid"
          stable=1
        fi
        if (( stable >= RESTORE_STABLE_SECONDS )) && resident_status_ready "$service" "$started_unix" "$worker_pid"; then
          printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE service=%s target=active observed=active worker_pid=%s stable_seconds=%s result=PASS\n' \
            "$service" "$worker_pid" "$stable"
          return 0
        fi
      else
        last_worker_pid=""
        stable=0
      fi
    else
      last_worker_pid=""
      stable=0
    fi
    [[ "$state" == failed ]] && break
    sleep 1
  done
  service_failure_snapshot "$service"
  return 1
}

restore_source_checkout(){
  [[ "$BUILD_IMAGE" == 1 ]] || return 0
  if [[ "$source_candidate_path_owned" == 1 && ( -e "$SOURCE_DIR" || -L "$SOURCE_DIR" ) ]]; then
    rm -rf --one-file-system -- "$SOURCE_DIR" || return 1
  fi
  if [[ "$source_had_original" == 1 ]]; then
    [[ -n "$source_backup_dir" && -e "$source_backup_dir" && ! -L "$source_backup_dir" ]] \
      || return 1
    mv -- "$source_backup_dir" "$SOURCE_DIR" || return 1
    source_backup_dir=""
  fi
  printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE component=source_checkout result=PASS\n'
  return 0
}

cleanup(){
  local rc=$? service source_after container_after
  trap - EXIT INT TERM
  if [[ "$window_started" == 1 ]]; then
    # Keep the exclusive workload fence while claim paths are quiet, candidate
    # files are removed, and the exact pre-existing source tree is restored.
    if [[ "$services_stop_attempted" == 1 ]]; then
      systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE" >/dev/null 2>&1 \
        || record_restore_failure service_stop
    fi
    if [[ "$BUILD_IMAGE" == 1 ]]; then
      if [[ "$ENV_FILE" == "$BASE_DIR/universal-video-container-candidate.env" ]]; then
        rm -f -- "$ENV_FILE" >/dev/null 2>&1 \
          || record_restore_failure candidate_env_remove
      else
        record_restore_failure candidate_env_scope
      fi
      restore_source_checkout || record_restore_failure source_checkout
    fi
    for service in "${added_runtime_masks[@]}"; do
      systemctl unmask --runtime "$service" >/dev/null 2>&1 \
        || record_restore_failure "unmask_${service}"
    done
    systemctl daemon-reload >/dev/null 2>&1 \
      || record_restore_failure daemon_reload
    if [[ "$container_was_active" == 1 && -n "$resident_image_id" ]]; then
      docker image inspect "$resident_image_id" >/dev/null 2>&1 \
        || record_restore_failure resident_image_missing
    fi
    # Release the attestation fence before starting either resident. A current
    # worker performs startup recovery under this same lock and publishes its
    # readiness only after the lock is released; checking it while fd 9 is held
    # would prove only that the process is blocked, not that startup succeeded.
    if [[ "$lock_held" == 1 ]]; then
      if ! flock --unlock 9 >/dev/null 2>&1; then
        record_restore_failure workload_unlock
      fi
      exec 9>&-
      lock_held=0
    fi

    # Start and validate the prior residents sequentially after the handoff.
    # restore_service requires the same descendant worker PID to remain live
    # for the bounded stability interval after startup recovery can run.
    restore_service "$SOURCE_SERVICE" "$source_state_before" \
      || record_restore_failure source_service
    restore_service "$CONTAINER_SERVICE" "$container_target_state" \
      || record_restore_failure container_service

    source_after="$(service_state "$SOURCE_SERVICE")"
    container_after="$(service_state "$CONTAINER_SERVICE")"
    [[ "$source_after" == "$source_state_before" ]] \
      || record_restore_failure source_state_mismatch
    [[ "$container_after" == "$container_target_state" ]] \
      || record_restore_failure container_state_mismatch
    restored_service_ready "$SOURCE_SERVICE" "$source_state_before" \
      || record_restore_failure source_readiness_mismatch
    restored_service_ready "$CONTAINER_SERVICE" "$container_target_state" \
      || record_restore_failure container_readiness_mismatch
    if [[ "${#restore_failures[@]}" -eq 0 ]]; then
      printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS source_service_before=%s source_service=%s container_service_before=%s container_target=%s container_service=%s prior_container_recovery=%s\n' \
        "$source_state_before" "$source_after" "$container_state_before" "$container_target_state" \
        "$container_after" "$container_recovery_requested"
    else
      printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED codes=%s source_service=%s container_service=%s\n' \
        "$(IFS=,; echo "${restore_failures[*]}")" "${source_after:-unknown}" "${container_after:-unknown}" >&2
      if [[ "$rc" == 0 ]]; then rc=1; fi
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

assert_known_state(){
  local service="$1" state="$2"
  case "$state" in
    active|inactive) ;;
    *) die "$service state is unsafe or unknown: ${state:-unknown}" ;;
  esac
}

assert_quiescent(){
  local source_state container_state
  source_state="$(service_state "$SOURCE_SERVICE")"
  container_state="$(service_state "$CONTAINER_SERVICE")"
  case "$source_state" in inactive|failed) ;; *) die "$SOURCE_SERVICE is not quiescent: ${source_state:-unknown}" ;; esac
  case "$container_state" in inactive|failed) ;; *) die "$CONTAINER_SERVICE is not quiescent: ${container_state:-unknown}" ;; esac
  if pgrep -fa '[u]niversal_video[.]spool_worker' >/dev/null; then
    die 'a Universal Video worker process is active'
  fi
  if docker ps --filter 'name=^/universal-video-container$' --format '{{.ID}}' | grep -q .; then
    die 'the Universal Video container is active'
  fi
  if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
    die 'a video job is active'
  fi
}

verify_prior_recovery_evidence(){
  local actual_sha prior_sha
  actual_sha="$(sha256sum "$RECOVERY_EVIDENCE_FILE" | awk '{print $1}')"
  [[ "$actual_sha" == "$RECOVERY_EVIDENCE_SHA256" ]] \
    || die 'immutable prior-run recovery evidence digest mismatch'
  mapfile -t prior_sha_lines < <(
    grep -E '^runtime_sha=[0-9a-f]{40}$' "$RECOVERY_EVIDENCE_FILE" || true
  )
  [[ "${#prior_sha_lines[@]}" -eq 1 ]] \
    || die 'immutable prior-run recovery runtime identity is missing or ambiguous'
  prior_sha="${prior_sha_lines[0]#runtime_sha=}"
  grep -Eq '^UNIVERSAL_VIDEO_PRECANARY_WINDOW .*container_service_before=active .*restore_on_exit=true$' \
    "$RECOVERY_EVIDENCE_FILE" \
    || die 'prior-run evidence does not record an active container entry state'
  grep -Eq '^UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED .*container_service=(inactive|failed)$' \
    "$RECOVERY_EVIDENCE_FILE" \
    || die 'prior-run evidence does not record a bounded container restoration failure'
  grep -Fx 'real_media_canary_run=false' "$RECOVERY_EVIDENCE_FILE" >/dev/null \
    || die 'prior-run evidence is not a no-media run'
  printf 'UNIVERSAL_VIDEO_PRECANARY_RECOVERY_EVIDENCE prior_run=%s runtime_sha=%s sha256=%s result=PASS\n' \
    "$RECOVER_CONTAINER_FROM_RUN" "$prior_sha" "$actual_sha"
}

assert_pre_stop_idle(){
  local source_pid container_pid worker_pid dsn_result
  source_pid="$(systemctl show "$SOURCE_SERVICE" --property=MainPID --value 2>/dev/null || true)"
  container_pid="$(docker inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)"
  [[ "$source_state_before" != active || "$source_pid" =~ ^[1-9][0-9]*$ ]] \
    || die 'active source resident has no authoritative worker PID'
  [[ "$container_state_before" != active || "$container_pid" =~ ^[1-9][0-9]*$ ]] \
    || die 'active container resident has no authoritative worker PID'
  [[ "$source_state_before" != inactive || -z "$source_pid" || "$source_pid" == 0 ]] \
    || die 'inactive source resident still has a worker PID'
  [[ "$container_state_before" != inactive || -z "$container_pid" || "$container_pid" == 0 ]] \
    || die 'inactive container resident still has a worker PID'

  while read -r worker_pid; do
    pid_descends_from "$worker_pid" "$source_pid" \
      || pid_descends_from "$worker_pid" "$container_pid" \
      || die 'an unowned Universal Video worker process is active'
  done < <(pgrep -f '[u]niversal_video[.]spool_worker' || true)
  if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
    die 'a video spool job is active before resident stop'
  fi
  [[ -f "$QUEUE_DSN_FILE" && ! -L "$QUEUE_DSN_FILE" ]] \
    || die 'authoritative video queue credential is unsafe or missing'
  [[ -x "$QUEUE_PYTHON" ]] || die 'authoritative video queue Python is unavailable'
  dsn_result="$(runuser -u universal-video -- "$QUEUE_PYTHON" - "$QUEUE_DSN_FILE" <<'PY' 2>/dev/null || true
from pathlib import Path
import sys
try:
    import psycopg
    dsn = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    if not dsn:
        raise RuntimeError
    with psycopg.connect(dsn, connect_timeout=8, application_name="uv-precanary-idle-proof") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM video_queue.precanary_idle_snapshot()")
            row = cur.fetchone()
    print(f"CLAIMABLE:{int(row[0])},LEASED:{int(row[1])}" if row and len(row) == 2 else "UNKNOWN")
except Exception:
    print("UNKNOWN")
PY
)"
  [[ "$dsn_result" == 'CLAIMABLE:0,LEASED:0' ]] \
    || die 'authoritative Neon claimable/LEASED state is busy or unverifiable'
  printf 'UNIVERSAL_VIDEO_PRECANARY_PRESTOP_IDLE source_pid=%s container_pid=%s spool_running=0 neon_claimable=0 neon_leased=0 result=PASS\n' \
    "${source_pid:-0}" "${container_pid:-0}"
}

mask_service_for_window(){
  local service="$1" enabled_state
  enabled_state="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  case "$enabled_state" in
    masked|masked-runtime) return ;;
  esac
  systemctl mask --runtime "$service" >/dev/null
  added_runtime_masks+=("$service")
}

command -v flock >/dev/null || die 'flock is unavailable'
command -v docker >/dev/null || die 'docker is unavailable'
command -v runuser >/dev/null || die 'runuser is unavailable'
command -v sha256sum >/dev/null || die 'sha256sum is unavailable'
[[ -d "$BASE_DIR/spool" && ! -L "$BASE_DIR/spool" ]] || die 'unsafe or missing spool mount'
[[ -d "$BASE_DIR/spool/running" && ! -L "$BASE_DIR/spool/running" ]] || die 'unsafe or missing running spool'
if [[ -L "$WORKLOAD_LOCK" || ( -e "$WORKLOAD_LOCK" && ! -f "$WORKLOAD_LOCK" ) ]]; then
  die 'unsafe workload lock'
fi
uid="$(id -u universal-video)"
gid="$(id -g universal-video)"
if [[ ! -e "$WORKLOAD_LOCK" ]]; then
  install -o universal-video -g universal-video -m 0640 /dev/null "$WORKLOAD_LOCK"
fi
chown universal-video:universal-video "$WORKLOAD_LOCK"
chmod 0640 "$WORKLOAD_LOCK"
exec 9<>"$WORKLOAD_LOCK"
flock --exclusive --nonblock 9 || die 'a worker holds the workload claim fence'
lock_held=1

source_state_before="$(service_state "$SOURCE_SERVICE")"
container_state_before="$(service_state "$CONTAINER_SERVICE")"
assert_known_state "$SOURCE_SERVICE" "$source_state_before"
assert_known_state "$CONTAINER_SERVICE" "$container_state_before"
[[ "$source_state_before" == active ]] && source_was_active=1
[[ "$container_state_before" == active ]] && container_was_active=1
container_target_state="$container_state_before"
if [[ -n "$RECOVER_CONTAINER_FROM_RUN" && "$container_state_before" != active ]]; then
  # A prior exact external run recorded container_service_before=active and
  # then failed only while restoring that state. This bounded one-shot input
  # asks the next exact run to restore the recorded state after all gates.
  verify_prior_recovery_evidence
  container_was_active=1
  container_target_state=active
  container_recovery_requested=1
  printf 'UNIVERSAL_VIDEO_PRECANARY_RECOVERY prior_run=%s observed_container=%s target_container=active\n' \
    "$RECOVER_CONTAINER_FROM_RUN" "$container_state_before"
fi
if [[ "$container_was_active" == 1 ]]; then
  [[ -f "$PERSISTENT_ENV_FILE" && ! -L "$PERSISTENT_ENV_FILE" ]] \
    || die 'active container resident environment is unsafe or missing'
  mapfile -t resident_image_lines < <(grep -E '^UNIVERSAL_VIDEO_IMAGE=sha256:[0-9a-f]{64}$' "$PERSISTENT_ENV_FILE" || true)
  [[ "${#resident_image_lines[@]}" -eq 1 ]] \
    || die 'active container resident image is missing or ambiguous'
  resident_image_id="${resident_image_lines[0]#UNIVERSAL_VIDEO_IMAGE=}"
  docker image inspect "$resident_image_id" >/dev/null \
    || die 'active container resident image is unavailable'
fi
window_started=1

# Acquire the exclusive fence before source checkout, cache reclamation, image
# build, import preflight, or metadata reads. An active job would hold the
# shared lock and make this operation fail closed before any mutation.
mask_service_for_window "$SOURCE_SERVICE"
mask_service_for_window "$CONTAINER_SERVICE"
assert_pre_stop_idle
services_stop_attempted=1
systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE" >/dev/null
assert_quiescent
printf 'UNIVERSAL_VIDEO_PRECANARY_WINDOW source_service_before=%s container_service_before=%s workload_fence=exclusive services_quiescent=true restore_on_exit=true\n' \
  "$source_state_before" "$container_state_before"

if [[ "$BUILD_IMAGE" == 1 ]]; then
  # Move the complete prior tree aside atomically. The preparation script sees
  # an empty candidate path, so it cannot delete or rewrite the resident tree.
  # The EXIT trap restores this exact directory before either service restarts.
  if [[ -e "$SOURCE_DIR" || -L "$SOURCE_DIR" ]]; then
    [[ -d "$SOURCE_DIR" && ! -L "$SOURCE_DIR" ]] || die 'existing source checkout is unsafe'
    source_backup_dir="${SOURCE_DIR}.precanary-backup.${EXPECTED_SHA:0:12}.$$"
    [[ ! -e "$source_backup_dir" && ! -L "$source_backup_dir" ]] \
      || die 'source backup destination already exists'
    mv -- "$SOURCE_DIR" "$source_backup_dir"
    source_had_original=1
    printf 'UNIVERSAL_VIDEO_SOURCE_CHECKOUT mode=ephemeral prior_tree=preserved restore_on_exit=true\n'
  else
    printf 'UNIVERSAL_VIDEO_SOURCE_CHECKOUT mode=ephemeral prior_tree=absent restore_on_exit=true\n'
  fi
  # From this point onward only the candidate path may be removed by cleanup.
  # Unsafe pre-existing paths fail above before ownership is recorded.
  source_candidate_path_owned=1

  env \
    UNIVERSAL_VIDEO_GIT_REF="$EXPECTED_SHA" \
    UNIVERSAL_VIDEO_ACTIVATE=0 \
    UNIVERSAL_VIDEO_PREWARM_MODEL=0 \
    UNIVERSAL_VIDEO_RUN_SMOKE=0 \
    UNIVERSAL_VIDEO_SOURCE_ONLY=1 \
    bash "$PREPARE_SCRIPT"
  [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_SHA" ]] \
    || die 'source preparation did not produce the expected SHA'
  assert_quiescent

  disk_available_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $4}')"
  [[ "$disk_available_kb" =~ ^[0-9]+$ ]] || die 'build capacity unavailable'
  if (( disk_available_kb < MIN_FREE_KB )) && [[ "$RECLAIM_ROOT_CACHE" == 1 ]]; then
    root_cache=/root/.cache
    [[ -d "$root_cache" && ! -L "$root_cache" ]] || die 'root cache is unsafe or missing'
    cache_before_kb="$(du -skx "$root_cache" | awk '{print $1}')"
    # Cache-only reclamation under the exclusive workload fence. No source,
    # model mount, spool, output, media, or database path is touched.
    find "$root_cache" -xdev -mindepth 1 -delete
    cache_after_kb="$(du -skx "$root_cache" | awk '{print $1}')"
    disk_after_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $4}')"
    printf 'UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=root-cache-all before_kb=%s after_kb=%s disk_available_kb=%s\n' \
      "$cache_before_kb" "$cache_after_kb" "$disk_after_kb"
  fi

  env \
    UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0 \
    UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB="$MIN_FREE_KB" \
    UNIVERSAL_VIDEO_CONTAINER_PRESERVE_IMAGE_ID="$resident_image_id" \
    bash "$SOURCE_DIR/ops/oracle_universal_video_container_install.sh"
  assert_quiescent
fi

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die 'safe container environment is unavailable'

commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die 'source commit is unavailable'
if [[ -n "$EXPECTED_SHA" && "$commit" != "$EXPECTED_SHA" ]]; then
  die 'attested source commit does not match expected SHA'
fi
image="$IMAGE_REPO:$commit"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die 'captured image ID is unavailable'

verify_image_identity(){
  local current_id revision
  current_id="$(docker image inspect --format '{{.Id}}' "$image")"
  revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_id")"
  [[ "$current_id" == "$image_id" ]] || die 'mutable image tag changed after capture'
  [[ "$revision" == "$commit" ]] || die 'captured image revision label does not match source commit'
}

[[ -f "$BASE_DIR/secrets/google-drive-oauth.json" && ! -L "$BASE_DIR/secrets/google-drive-oauth.json" ]] \
  || die 'protected Google Drive credential missing'
run_image(){
  verify_image_identity
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g \
    --user="$uid:$gid" \
    --env-file "$ENV_FILE" \
    --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" \
    --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" \
    --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" \
    --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" \
    --mount "type=bind,src=$STATUS_DIR,dst=/run/bridge-school" \
    --mount "type=bind,src=$BASE_DIR/secrets,dst=/run/secrets,readonly" \
    "$image_id" "$@"
}

verify_image_identity
assert_quiescent
printf 'UNIVERSAL_VIDEO_PRECANARY_RUNTIME commit=%s image_digest=%s\n' "$commit" "$image_id"
printf 'UNIVERSAL_VIDEO_PRECANARY_STATE source_service_before=%s container_service_before=%s source_service=inactive container_service=inactive running_jobs=0 workload_fence=exclusive restore_on_exit=true\n' \
  "$source_state_before" "$container_state_before"
run_image python -m universal_video.precanary imports
run_image python -m universal_video.precanary synthetic-result-contract
run_image python -m universal_video.precanary source-identity \
  --file-id "$FILE_ID" --name "$NAME" --mime-type "$MIME" --size "$SIZE" --parent "$PARENT"
verify_image_identity
assert_quiescent
printf 'UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit=%s image_digest=%s video_job_submitted=false drive_write_performed=false canonical_promotion_allowed=false publication_state=NOT_PUBLISHED\n' \
  "$commit" "$image_id"
