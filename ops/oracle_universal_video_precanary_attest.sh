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
isolated_peer_service=""
isolated_peer_pid=""
isolated_peer_start_ticks=""
restored_source_pid=""
restored_source_start_ticks=""
restored_container_pid=""
restored_container_start_ticks=""
declare -a added_runtime_masks=()
declare -a restore_failures=()
declare -a prestop_frozen_services=()
declare -a prestop_frozen_pids=()
declare -a prestop_frozen_start_ticks=()

bounded_systemctl(){
  timeout --foreground --signal=TERM --kill-after=5s 30s systemctl "$@"
}

bounded_docker(){
  timeout --foreground --signal=TERM --kill-after=5s 15s docker "$@"
}

bounded_systemctl_query(){
  timeout --foreground --signal=TERM --kill-after=2s 5s systemctl "$@"
}

bounded_docker_query(){
  timeout --foreground --signal=TERM --kill-after=2s 5s docker "$@"
}

bounded_filesystem(){
  timeout --foreground --signal=TERM --kill-after=5s 30s "$@"
}

service_state(){
  bounded_systemctl_query show "$1" --property=ActiveState --value 2>/dev/null || true
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
    root_pid="$(bounded_systemctl_query show "$service" --property=MainPID --value 2>/dev/null || true)"
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    root_pid="$(bounded_docker_query inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)"
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
  local service="$1" target_state="$2" expected_worker_pid="${3:-}" expected_start_ticks="${4:-}" worker_pid
  [[ "$(service_state "$service")" == "$target_state" ]] || return 1
  [[ "$target_state" == inactive ]] && return 0
  worker_pid="$(resident_worker_pid "$service")" || return 1
  [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$expected_worker_pid" =~ ^[1-9][0-9]*$ \
        && "$expected_start_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$worker_pid" == "$expected_worker_pid" ]] || return 1
  exact_process_signal "$worker_pid" "$expected_start_ticks" CHECK
}

resident_expected_commit(){
  local service="$1" image_id
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    image_id="$(bounded_docker_query inspect --format '{{.Image}}' universal-video-container 2>/dev/null || true)"
    if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$resident_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      image_id="$resident_image_id"
    fi
    bounded_docker_query image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_id" 2>/dev/null || true
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

process_state(){
  local process_id="$1"
  [[ "$process_id" =~ ^[1-9][0-9]*$ ]] || return 1
  awk '$1 == "State:" {print substr($2,1,1)}' "/proc/$process_id/status" 2>/dev/null
}

process_is_runnable(){
  local process_id="$1" state
  state="$(process_state "$process_id" 2>/dev/null || true)"
  [[ -n "$state" && "$state" != T && "$state" != t \
        && "$state" != Z && "$state" != X && "$state" != x ]]
}

# Open a pidfd first, re-read the expected boot-relative start ticks, and send
# the signal through that descriptor. If the numeric PID was recycled at any
# point, the signal is delivered only to the process referenced by the pidfd
# (or fails with ESRCH), never to the replacement process.
exact_process_signal(){
  local process_id="$1" expected_ticks="$2" signal_name="$3"
  [[ "$process_id" =~ ^[1-9][0-9]*$ && "$expected_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$signal_name" == STOP || "$signal_name" == CONT \
        || "$signal_name" == TERM || "$signal_name" == CHECK ]] || return 1
  PROCESS_ID="$process_id" EXPECTED_START_TICKS="$expected_ticks" SIGNAL_NAME="$signal_name" \
    python3 - <<'PY' >/dev/null 2>&1
import os
import signal
from pathlib import Path

pid = int(os.environ["PROCESS_ID"])
expected_ticks = int(os.environ["EXPECTED_START_TICKS"])
signal_value = {
    "STOP": signal.SIGSTOP,
    "CONT": signal.SIGCONT,
    "TERM": signal.SIGTERM,
    "CHECK": 0,
}[os.environ["SIGNAL_NAME"]]

pidfd = os.pidfd_open(pid, 0)
try:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    tail = raw.rsplit(")", 1)
    if len(tail) != 2:
        raise SystemExit(1)
    fields = tail[1].split()
    if len(fields) <= 19 or int(fields[19]) != expected_ticks:
        raise SystemExit(1)
    if os.environ["SIGNAL_NAME"] == "CHECK" and fields[0] in {"T", "t", "Z", "X", "x"}:
        raise SystemExit(1)
    signal.pidfd_send_signal(pidfd, signal_value, None, 0)
finally:
    os.close(pidfd)
PY
}

resume_prestop_frozen(){
  local mode="${1:-preserve}" index service process_id expected_ticks current_ticks state attempt rc=0
  local -a remaining_services=() remaining_pids=() remaining_start_ticks=()
  [[ "$mode" == preserve || "$mode" == stopping ]] || return 1
  if [[ "$mode" == stopping ]]; then
    # A cancellation can arrive before stop_frozen_residents queues anything.
    # Queue the unit stop and TERM every exact pidfd identity before any CONT,
    # so cleanup can never resume a legacy claimant into the idle-proof gap.
    bounded_systemctl stop --no-block "$SOURCE_SERVICE" "$CONTAINER_SERVICE" \
      >/dev/null 2>&1 || rc=1
    for index in "${!prestop_frozen_pids[@]}"; do
      process_id="${prestop_frozen_pids[$index]}"
      expected_ticks="${prestop_frozen_start_ticks[$index]}"
      current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
      if [[ "$current_ticks" != "$expected_ticks" ]]; then
        # The exact frozen identity is already gone. Never signal a recycled
        # numeric PID; there is no legacy claimant left to resume.
        continue
      fi
      state="$(process_state "$process_id" 2>/dev/null || true)"
      if [[ -z "$state" || "$state" == Z || "$state" == X || "$state" == x ]]; then
        rc=1
        continue
      fi
      if ! exact_process_signal "$process_id" "$expected_ticks" TERM; then
        # ESRCH after pidfd_open is safe only if the exact identity vanished.
        # Otherwise retain every worker frozen and let bounded systemd stop
        # finish or fail closed; never enter the CONT phase.
        current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
        [[ "$current_ticks" != "$expected_ticks" ]] || rc=1
      fi
    done
    (( rc == 0 )) || return "$rc"
  fi
  for index in "${!prestop_frozen_pids[@]}"; do
    service="${prestop_frozen_services[$index]}"
    process_id="${prestop_frozen_pids[$index]}"
    expected_ticks="${prestop_frozen_start_ticks[$index]}"
    current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
    if [[ -z "$current_ticks" && "$mode" == stopping ]]; then
      continue
    fi
    if [[ "$current_ticks" != "$expected_ticks" ]]; then
      [[ "$mode" == stopping ]] || rc=1
      continue
    fi
    if [[ "$mode" == stopping ]]; then
      state="$(process_state "$process_id" 2>/dev/null || true)"
      if [[ "$state" == T || "$state" == t ]]; then
        if ! exact_process_signal "$process_id" "$expected_ticks" CONT; then
          current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
          [[ "$current_ticks" != "$expected_ticks" ]] || rc=1
        fi
      fi
      current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
      if [[ "$current_ticks" == "$expected_ticks" ]]; then
        # CONT only lets systemd finish the already queued TERM/stop. Keep the
        # exact identity until a post-stop proof sees it disappear;
        # successfully signalling the process is not proof that it exited.
        remaining_services+=("$service")
        remaining_pids+=("$process_id")
        remaining_start_ticks+=("$expected_ticks")
      fi
      continue
    fi
    if ! exact_process_signal "$process_id" "$expected_ticks" CONT; then
      current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
      if [[ "$current_ticks" == "$expected_ticks" ]]; then
        remaining_services+=("$service")
        remaining_pids+=("$process_id")
        remaining_start_ticks+=("$expected_ticks")
        rc=1
      fi
      continue
    fi
    if [[ "$mode" == preserve ]]; then
      for (( attempt=0; attempt<50; attempt++ )); do
        state="$(process_state "$process_id" 2>/dev/null || true)"
        if process_is_runnable "$process_id" \
              && "$(resident_worker_pid "$service" 2>/dev/null || true)" == "$process_id" \
              && "$(process_start_ticks "$process_id" 2>/dev/null || true)" == "$expected_ticks" ]]; then
          break
        fi
        sleep 0.1
      done
      state="$(process_state "$process_id" 2>/dev/null || true)"
      if ! process_is_runnable "$process_id" \
        || [[ "$(resident_worker_pid "$service" 2>/dev/null || true)" != "$process_id" ]] \
        || [[ "$(process_start_ticks "$process_id" 2>/dev/null || true)" != "$expected_ticks" ]]; then
        rc=1
        current_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
        if [[ "$current_ticks" == "$expected_ticks" ]]; then
          remaining_services+=("$service")
          remaining_pids+=("$process_id")
          remaining_start_ticks+=("$expected_ticks")
        fi
      fi
    fi
  done
  # Retain every exact identity that still exists but could not be resumed.
  # Cleanup must retry it or refuse restoration; it may never forget a frozen
  # worker merely because a pidfd CONT failed.
  prestop_frozen_services=("${remaining_services[@]}")
  prestop_frozen_pids=("${remaining_pids[@]}")
  prestop_frozen_start_ticks=("${remaining_start_ticks[@]}")
  return "$rc"
}

confirm_prestop_identities_exited(){
  local index process_id expected_ticks rc=0
  local -a remaining_services=() remaining_pids=() remaining_start_ticks=()
  for index in "${!prestop_frozen_pids[@]}"; do
    process_id="${prestop_frozen_pids[$index]}"
    expected_ticks="${prestop_frozen_start_ticks[$index]}"
    if [[ "$(process_start_ticks "$process_id" 2>/dev/null || true)" == "$expected_ticks" ]]; then
      remaining_services+=("${prestop_frozen_services[$index]}")
      remaining_pids+=("$process_id")
      remaining_start_ticks+=("$expected_ticks")
      rc=1
    fi
  done
  prestop_frozen_services=("${remaining_services[@]}")
  prestop_frozen_pids=("${remaining_pids[@]}")
  prestop_frozen_start_ticks=("${remaining_start_ticks[@]}")
  return "$rc"
}

residents_are_quiescent(){
  local source_state container_state workers containers running_file pgrep_rc
  source_state="$(service_state "$SOURCE_SERVICE")"
  container_state="$(service_state "$CONTAINER_SERVICE")"
  case "$source_state" in inactive|failed) ;; *) return 1 ;; esac
  case "$container_state" in inactive|failed) ;; *) return 1 ;; esac
  if workers="$(pgrep -fa '[u]niversal_video[.]spool_worker' 2>/dev/null)"; then
    return 1
  else
    pgrep_rc=$?
    [[ "$pgrep_rc" == 1 ]] || return 1
  fi
  containers="$(bounded_docker ps --filter 'name=^/universal-video-container$' --format '{{.ID}}')" \
    || return 1
  [[ -z "$containers" ]] || return 1
  running_file="$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit)" \
    || return 1
  [[ -z "$running_file" ]]
}

freeze_residents_for_idle_snapshot(){
  local service target_state process_id start_ticks index attempt state all_frozen
  for service in "$SOURCE_SERVICE" "$CONTAINER_SERVICE"; do
    if [[ "$service" == "$SOURCE_SERVICE" ]]; then
      target_state="$source_state_before"
    else
      target_state="$container_state_before"
    fi
    [[ "$target_state" == active ]] || continue
    process_id="$(resident_worker_pid "$service" 2>/dev/null || true)"
    start_ticks="$(process_start_ticks "$process_id" 2>/dev/null || true)"
    if [[ ! "$process_id" =~ ^[1-9][0-9]*$ || ! "$start_ticks" =~ ^[1-9][0-9]*$ ]]; then
      if ! resume_prestop_frozen preserve >/dev/null 2>&1; then
        # Even when the failed preserve attempt has emptied the identity
        # arrays, cleanup must stop both units and prove quiescence before it
        # recreates the pre-window resident state.
        services_stop_attempted=1
      fi
      return 1
    fi
    prestop_frozen_services+=("$service")
    prestop_frozen_pids+=("$process_id")
    prestop_frozen_start_ticks+=("$start_ticks")
    if [[ "$service" == "$SOURCE_SERVICE" ]]; then
      restored_source_pid="$process_id"
      restored_source_start_ticks="$start_ticks"
    else
      restored_container_pid="$process_id"
      restored_container_start_ticks="$start_ticks"
    fi
    if ! exact_process_signal "$process_id" "$start_ticks" STOP; then
      if ! resume_prestop_frozen preserve >/dev/null 2>&1; then
        services_stop_attempted=1
      fi
      return 1
    fi
  done
  for (( attempt=0; attempt<50; attempt++ )); do
    all_frozen=1
    for index in "${!prestop_frozen_pids[@]}"; do
      service="${prestop_frozen_services[$index]}"
      process_id="${prestop_frozen_pids[$index]}"
      start_ticks="${prestop_frozen_start_ticks[$index]}"
      state="$(process_state "$process_id" 2>/dev/null || true)"
      if [[ "$state" != T && "$state" != t ]] \
        || [[ "$(resident_worker_pid "$service" 2>/dev/null || true)" != "$process_id" ]] \
        || [[ "$(process_start_ticks "$process_id" 2>/dev/null || true)" != "$start_ticks" ]]; then
        all_frozen=0
        break
      fi
    done
    if [[ "$all_frozen" == 1 ]]; then
      printf 'UNIVERSAL_VIDEO_PRECANARY_PRESTOP_FREEZE residents=%s result=PASS\n' \
        "${#prestop_frozen_pids[@]}"
      return 0
    fi
    sleep 0.1
  done
  if ! resume_prestop_frozen preserve >/dev/null 2>&1; then
    services_stop_attempted=1
  fi
  return 1
}

stop_frozen_residents(){
  local rc=0
  # stopping mode queues the stop plus TERM for every exact frozen identity
  # before it resumes any process. The blocking stop then completes shutdown.
  resume_prestop_frozen stopping || rc=1
  bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE" >/dev/null 2>&1 || rc=1
  confirm_prestop_identities_exited || rc=1
  residents_are_quiescent || rc=1
  return "$rc"
}

resume_isolated_peer(){
  local attempt state worker_pid rc=1
  [[ -n "$isolated_peer_pid" ]] || return 0
  [[ "$isolated_peer_pid" =~ ^[1-9][0-9]*$ \
        && "$isolated_peer_start_ticks" =~ ^[1-9][0-9]*$ \
        && -n "$isolated_peer_service" ]] || return 1
  if [[ "$(process_start_ticks "$isolated_peer_pid" 2>/dev/null || true)" == "$isolated_peer_start_ticks" ]] \
    && exact_process_signal "$isolated_peer_pid" "$isolated_peer_start_ticks" CONT; then
    for (( attempt=0; attempt<50; attempt++ )); do
      state="$(process_state "$isolated_peer_pid" 2>/dev/null || true)"
      worker_pid="$(resident_worker_pid "$isolated_peer_service" 2>/dev/null || true)"
      if process_is_runnable "$isolated_peer_pid" \
            && "$worker_pid" == "$isolated_peer_pid" \
            && "$(process_start_ticks "$isolated_peer_pid" 2>/dev/null || true)" == "$isolated_peer_start_ticks" ]]; then
        rc=0
        break
      fi
      sleep 0.1
    done
  fi
  # A final best-effort CONT prevents an interrupted validation from leaving
  # a still-existing prior resident paused, while the nonzero result keeps the
  # overall restoration receipt fail-closed if identity was lost.
  if [[ "$(process_start_ticks "$isolated_peer_pid" 2>/dev/null || true)" == "$isolated_peer_start_ticks" ]]; then
    exact_process_signal "$isolated_peer_pid" "$isolated_peer_start_ticks" CONT || true
  fi
  if [[ "$rc" == 0 ]]; then
    isolated_peer_service=""
    isolated_peer_pid=""
    isolated_peer_start_ticks=""
  fi
  return "$rc"
}

isolate_ambiguous_legacy_peer(){
  local service="$1" peer_service target_commit peer_commit peer_pid peer_ticks attempt state
  [[ -z "$isolated_peer_pid" ]] || return 1
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    peer_service="$CONTAINER_SERVICE"
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    peer_service="$SOURCE_SERVICE"
  else
    return 1
  fi
  [[ "$(service_state "$peer_service")" == active ]] || return 0
  target_commit="$(resident_expected_commit "$service")"
  peer_commit="$(resident_expected_commit "$peer_service")"
  if [[ "$target_commit" =~ ^[0-9a-f]{40}$ && "$peer_commit" =~ ^[0-9a-f]{40}$ \
        && "$target_commit" != "$peer_commit" ]]; then
    return 0
  fi
  peer_pid="$(resident_worker_pid "$peer_service" 2>/dev/null || true)"
  peer_ticks="$(process_start_ticks "$peer_pid" 2>/dev/null || true)"
  [[ "$peer_pid" =~ ^[1-9][0-9]*$ && "$peer_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  isolated_peer_service="$peer_service"
  isolated_peer_pid="$peer_pid"
  isolated_peer_start_ticks="$peer_ticks"
  if ! exact_process_signal "$peer_pid" "$peer_ticks" STOP; then
    isolated_peer_service=""
    isolated_peer_pid=""
    isolated_peer_start_ticks=""
    return 1
  fi
  for (( attempt=0; attempt<50; attempt++ )); do
    state="$(process_state "$peer_pid" 2>/dev/null || true)"
    if [[ "$state" == T || "$state" == t ]]; then
      [[ "$(resident_worker_pid "$peer_service" 2>/dev/null || true)" == "$peer_pid" ]] || break
      [[ "$(process_start_ticks "$peer_pid" 2>/dev/null || true)" == "$peer_ticks" ]] || break
      printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_ISOLATION peer_service=%s peer_pid=%s target_service=%s result=PASS\n' \
        "$peer_service" "$peer_pid" "$service"
      return 0
    fi
    sleep 0.1
  done
  resume_isolated_peer >/dev/null 2>&1 || true
  return 1
}

isolated_peer_still_quiesced(){
  local peer_service="$1" state
  [[ -n "$peer_service" && "$isolated_peer_service" == "$peer_service" ]] || return 1
  [[ "$isolated_peer_pid" =~ ^[1-9][0-9]*$ \
        && "$isolated_peer_start_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  state="$(process_state "$isolated_peer_pid" 2>/dev/null || true)"
  [[ "$state" == T || "$state" == t ]] || return 1
  [[ "$(resident_worker_pid "$peer_service" 2>/dev/null || true)" == "$isolated_peer_pid" ]] || return 1
  [[ "$(process_start_ticks "$isolated_peer_pid" 2>/dev/null || true)" == "$isolated_peer_start_ticks" ]]
}

resident_status_ready(){
  local service="$1" started_unix="$2" worker_pid="$3" legacy_peer_quiesced="${4:-0}" expected_commit expected_resident expected_process_id expected_process_start_ticks peer_service peer_commit legacy_peer_same_commit=0
  [[ -f "$STATUS_FILE" && ! -L "$STATUS_FILE" ]] || return 1
  [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  if [[ "$service" == "$SOURCE_SERVICE" ]]; then
    expected_resident=source
    expected_process_id="$worker_pid"
    peer_service="$CONTAINER_SERVICE"
  elif [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    expected_resident=container
    peer_service="$SOURCE_SERVICE"
  else
    return 1
  fi
  expected_process_start_ticks="$(process_start_ticks "$worker_pid" 2>/dev/null || true)"
  [[ "$expected_process_start_ticks" =~ ^[1-9][0-9]*$ ]] || return 1
  # Bind the receipt to one exact host process identity before inspecting any
  # status supplied by that resident. A recycled numeric PID cannot satisfy
  # CHECK with the captured boot-relative start ticks.
  exact_process_signal "$worker_pid" "$expected_process_start_ticks" CHECK || return 1
  if [[ "$service" == "$CONTAINER_SERVICE" ]]; then
    expected_process_id="$(awk '$1 == "NSpid:" {print $NF}' "/proc/$worker_pid/status" 2>/dev/null || true)"
  fi
  expected_commit="$(resident_expected_commit "$service")"
  [[ "$expected_process_id" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || return 1
  [[ "$legacy_peer_quiesced" =~ ^[01]$ ]] || return 1
  if [[ "$(service_state "$peer_service")" == active ]]; then
    peer_commit="$(resident_expected_commit "$peer_service")"
    legacy_peer_same_commit=1
    if [[ "$peer_commit" =~ ^[0-9a-f]{40}$ && "$peer_commit" != "$expected_commit" ]]; then
      legacy_peer_same_commit=0
    fi
  fi
  if [[ "$legacy_peer_quiesced" == 1 ]]; then
    isolated_peer_still_quiesced "$peer_service" || return 1
  fi
  if ! STATUS_PATH="$STATUS_FILE" EXPECTED_COMMIT="$expected_commit" \
    EXPECTED_RESIDENT="$expected_resident" EXPECTED_PROCESS_ID="$expected_process_id" \
    EXPECTED_PROCESS_START_TICKS="$expected_process_start_ticks" \
    STARTED_UNIX="$started_unix" LEGACY_PEER_SAME_COMMIT="$legacy_peer_same_commit" \
    LEGACY_PEER_QUIESCED="$legacy_peer_quiesced" \
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
    if (
        os.environ["LEGACY_PEER_SAME_COMMIT"] != "0"
        and os.environ["LEGACY_PEER_QUIESCED"] != "1"
    ):
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
  then
    return 1
  fi
  # Revalidate the same captured PID/start-ticks pair after parsing the
  # receipt. restore_service records these exact ticks and performs its final
  # readiness check against them; it never resamples an unbound replacement.
  exact_process_signal "$worker_pid" "$expected_process_start_ticks" CHECK || return 1
  [[ "$(resident_worker_pid "$service" 2>/dev/null || true)" == "$worker_pid" ]] || return 1
  if [[ "$legacy_peer_quiesced" == 1 ]]; then
    isolated_peer_still_quiesced "$peer_service" || return 1
  fi
  printf '%s\n' "$expected_process_start_ticks"
}

record_restore_failure(){
  restore_failures+=("$1")
}

service_failure_snapshot(){
  local service="$1" state result code status restarts
  state="$(service_state "$service")"
  result="$(bounded_systemctl_query show "$service" --property=Result --value 2>/dev/null || true)"
  code="$(bounded_systemctl_query show "$service" --property=ExecMainCode --value 2>/dev/null || true)"
  status="$(bounded_systemctl_query show "$service" --property=ExecMainStatus --value 2>/dev/null || true)"
  restarts="$(bounded_systemctl_query show "$service" --property=NRestarts --value 2>/dev/null || true)"
  printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE service=%s result=FAILED state=%s unit_result=%s exec_code=%s exec_status=%s restarts=%s\n' \
    "$service" "${state:-unknown}" "${result:-unknown}" "${code:-unknown}" "${status:-unknown}" "${restarts:-unknown}" >&2
}

restore_service(){
  local service="$1" target_state="$2" state worker_pid last_worker_pid="" stable=0 started_unix deadline legacy_peer_quiesced=0 verified_start_ticks
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

  bounded_systemctl reset-failed "$service" >/dev/null 2>&1 || true
  if ! isolate_ambiguous_legacy_peer "$service"; then
    service_failure_snapshot "$service"
    return 1
  fi
  [[ -n "$isolated_peer_pid" ]] && legacy_peer_quiesced=1
  if ! clear_restore_status; then
    resume_isolated_peer >/dev/null 2>&1 || true
    service_failure_snapshot "$service"
    return 1
  fi
  started_unix="$(date +%s)"
  deadline=$((SECONDS + RESTORE_TIMEOUT_SECONDS))
  if ! bounded_systemctl start --no-block "$service" >/dev/null 2>&1; then
    resume_isolated_peer >/dev/null 2>&1 || true
    service_failure_snapshot "$service"
    return 1
  fi
  while (( SECONDS < deadline )); do
    state="$(service_state "$service")"
    if [[ "$state" == active ]]; then
      worker_pid="$(resident_worker_pid "$service" 2>/dev/null || true)"
      if [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]]; then
        if [[ "$worker_pid" == "$last_worker_pid" ]]; then
          ((stable += 1))
        else
          if ! clear_restore_status; then
            resume_isolated_peer >/dev/null 2>&1 || true
            service_failure_snapshot "$service"
            return 1
          fi
          last_worker_pid="$worker_pid"
          stable=1
        fi
        if (( stable >= RESTORE_STABLE_SECONDS )) \
          && verified_start_ticks="$(resident_status_ready "$service" "$started_unix" "$worker_pid" "$legacy_peer_quiesced")"; then
          [[ "$verified_start_ticks" =~ ^[1-9][0-9]*$ ]] || continue
          if ! resume_isolated_peer; then
            service_failure_snapshot "$service"
            return 1
          fi
          if [[ "$(resident_worker_pid "$service" 2>/dev/null || true)" != "$worker_pid" ]] \
            || ! exact_process_signal "$worker_pid" "$verified_start_ticks" CHECK; then
            service_failure_snapshot "$service"
            return 1
          fi
          if [[ "$service" == "$SOURCE_SERVICE" ]]; then
            restored_source_pid="$worker_pid"
            restored_source_start_ticks="$verified_start_ticks"
          else
            restored_container_pid="$worker_pid"
            restored_container_start_ticks="$verified_start_ticks"
          fi
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
  resume_isolated_peer >/dev/null 2>&1 || true
  service_failure_snapshot "$service"
  return 1
}

restore_source_checkout(){
  [[ "$BUILD_IMAGE" == 1 ]] || return 0
  if [[ "$source_candidate_path_owned" == 1 && ( -e "$SOURCE_DIR" || -L "$SOURCE_DIR" ) ]]; then
    bounded_filesystem rm -rf --one-file-system -- "$SOURCE_DIR" || return 1
  fi
  if [[ "$source_had_original" == 1 ]]; then
    [[ -n "$source_backup_dir" && -e "$source_backup_dir" && ! -L "$source_backup_dir" ]] \
      || return 1
    bounded_filesystem mv -- "$source_backup_dir" "$SOURCE_DIR" || return 1
    source_backup_dir=""
  fi
  printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE component=source_checkout result=PASS\n'
  return 0
}

cleanup(){
  local rc=$? service source_after container_after
  trap - EXIT
  # Once cleanup starts, finish the bounded restore instead of allowing a
  # second signal to strand a frozen worker or a runtime-masked service.
  trap '' INT TERM
  if [[ "${#prestop_frozen_pids[@]}" -gt 0 ]]; then
    if [[ "$services_stop_attempted" == 1 ]]; then
      resume_prestop_frozen stopping || record_restore_failure prestop_resume
    else
      if ! resume_prestop_frozen preserve; then
        record_restore_failure prestop_resume
        services_stop_attempted=1
      fi
      if [[ "${#prestop_frozen_pids[@]}" -gt 0 ]]; then
        # A worker that cannot be resumed exactly must not remain SIGSTOPed.
        # Use bounded stop/restart restoration while masks and the workload
        # lock are still held.
        services_stop_attempted=1
        record_restore_failure prestop_preserve_requires_restart
      fi
    fi
  fi
  if [[ "$window_started" == 1 ]]; then
    if [[ "$services_stop_attempted" != 1 ]]; then
      for service in "${added_runtime_masks[@]}"; do
        bounded_systemctl unmask --runtime "$service" >/dev/null 2>&1 \
          || record_restore_failure "unmask_${service}"
      done
      bounded_systemctl daemon-reload >/dev/null 2>&1 \
        || record_restore_failure daemon_reload
      if [[ "$lock_held" == 1 ]]; then
        if ! flock --unlock 9 >/dev/null 2>&1; then
          record_restore_failure workload_unlock
        fi
        exec 9>&-
        lock_held=0
      fi
      source_after="$(service_state "$SOURCE_SERVICE")"
      container_after="$(service_state "$CONTAINER_SERVICE")"
      [[ "$source_after" == "$source_state_before" ]] \
        || record_restore_failure source_state_mismatch
      [[ "$container_after" == "$container_state_before" ]] \
        || record_restore_failure container_state_mismatch
      restored_service_ready "$SOURCE_SERVICE" "$source_state_before" \
        "$restored_source_pid" "$restored_source_start_ticks" \
        || record_restore_failure source_readiness_mismatch
      restored_service_ready "$CONTAINER_SERVICE" "$container_state_before" \
        "$restored_container_pid" "$restored_container_start_ticks" \
        || record_restore_failure container_readiness_mismatch
      if [[ "${#restore_failures[@]}" -eq 0 ]]; then
        printf 'UNIVERSAL_VIDEO_PRECANARY_PRESTOP_ABORT_RESTORE_PASS source_service=%s container_service=%s\n' \
          "$source_after" "$container_after"
      else
        printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED codes=%s source_service=%s container_service=%s\n' \
          "$(IFS=,; echo "${restore_failures[*]}")" "${source_after:-unknown}" "${container_after:-unknown}" >&2
        if [[ "$rc" == 0 ]]; then rc=1; fi
      fi
      trap - INT TERM
      exit "$rc"
    fi
    # Keep the exclusive workload fence while claim paths are quiet, candidate
    # files are removed, and the exact pre-existing source tree is restored.
    if [[ "$services_stop_attempted" == 1 ]]; then
      stop_frozen_residents || record_restore_failure service_stop
      if [[ "${#prestop_frozen_pids[@]}" -gt 0 ]] || ! residents_are_quiescent; then
        record_restore_failure prestop_or_resident_not_quiescent
        printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED codes=%s source_service=%s container_service=%s\n' \
          "$(IFS=,; echo "${restore_failures[*]}")" \
          "$(service_state "$SOURCE_SERVICE")" "$(service_state "$CONTAINER_SERVICE")" >&2
        trap - INT TERM
        exit 1
      fi
    fi
    if [[ "$BUILD_IMAGE" == 1 ]]; then
      if [[ "$ENV_FILE" == "$BASE_DIR/universal-video-container-candidate.env" ]]; then
        rm -f -- "$ENV_FILE" >/dev/null 2>&1 \
          || record_restore_failure candidate_env_remove
      else
        record_restore_failure candidate_env_scope
      fi
      if ! restore_source_checkout; then
        record_restore_failure source_checkout
        printf 'UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED codes=%s source_service=%s container_service=%s\n' \
          "$(IFS=,; echo "${restore_failures[*]}")" \
          "$(service_state "$SOURCE_SERVICE")" "$(service_state "$CONTAINER_SERVICE")" >&2
        trap - INT TERM
        exit 1
      fi
    fi
    for service in "${added_runtime_masks[@]}"; do
      bounded_systemctl unmask --runtime "$service" >/dev/null 2>&1 \
        || record_restore_failure "unmask_${service}"
    done
    bounded_systemctl daemon-reload >/dev/null 2>&1 \
      || record_restore_failure daemon_reload
    if [[ "$container_was_active" == 1 && -n "$resident_image_id" ]]; then
      bounded_docker image inspect "$resident_image_id" >/dev/null 2>&1 \
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
    resume_isolated_peer || record_restore_failure legacy_peer_resume

    source_after="$(service_state "$SOURCE_SERVICE")"
    container_after="$(service_state "$CONTAINER_SERVICE")"
    [[ "$source_after" == "$source_state_before" ]] \
      || record_restore_failure source_state_mismatch
    [[ "$container_after" == "$container_target_state" ]] \
      || record_restore_failure container_state_mismatch
    restored_service_ready "$SOURCE_SERVICE" "$source_state_before" \
      "$restored_source_pid" "$restored_source_start_ticks" \
      || record_restore_failure source_readiness_mismatch
    restored_service_ready "$CONTAINER_SERVICE" "$container_target_state" \
      "$restored_container_pid" "$restored_container_start_ticks" \
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
  trap - INT TERM
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
  residents_are_quiescent || die 'Universal Video residents are not authoritatively quiescent'
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
  source_pid="$(bounded_systemctl_query show "$SOURCE_SERVICE" --property=MainPID --value 2>/dev/null || true)"
  container_pid="$(bounded_docker_query inspect --format '{{.State.Pid}}' universal-video-container 2>/dev/null || true)"
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
  enabled_state="$(bounded_systemctl_query is-enabled "$service" 2>/dev/null || true)"
  case "$enabled_state" in
    masked|masked-runtime) return ;;
  esac
  bounded_systemctl mask --runtime "$service" >/dev/null
  added_runtime_masks+=("$service")
}

command -v flock >/dev/null || die 'flock is unavailable'
command -v docker >/dev/null || die 'docker is unavailable'
command -v runuser >/dev/null || die 'runuser is unavailable'
command -v sha256sum >/dev/null || die 'sha256sum is unavailable'
command -v timeout >/dev/null || die 'timeout is unavailable'
python3 -c 'import os,signal; assert hasattr(os,"pidfd_open") and hasattr(signal,"pidfd_send_signal")' \
  >/dev/null 2>&1 || die 'pidfd signaling is unavailable'
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
  bounded_docker image inspect "$resident_image_id" >/dev/null \
    || die 'active container resident image is unavailable'
fi
window_started=1

# Acquire the exclusive fence before source checkout, cache reclamation, image
# build, import preflight, or metadata reads. An active job would hold the
# shared lock and make this operation fail closed before any mutation.
mask_service_for_window "$SOURCE_SERVICE"
mask_service_for_window "$CONTAINER_SERVICE"
if ! freeze_residents_for_idle_snapshot; then
  record_restore_failure prestop_freeze
  die 'unable to freeze exact resident workers before the idle snapshot'
fi
assert_pre_stop_idle
services_stop_attempted=1
stop_frozen_residents \
  || die 'unable to stop exact frozen resident workers safely'
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
image_id="$(bounded_docker image inspect --format '{{.Id}}' "$image")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die 'captured image ID is unavailable'

verify_image_identity(){
  local current_id revision
  current_id="$(bounded_docker image inspect --format '{{.Id}}' "$image")"
  revision="$(bounded_docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_id")"
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
