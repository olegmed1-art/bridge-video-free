#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Fixed, read-only diagnostics for the Universal Video container resident.
# No service, Docker, media, queue, source, or filesystem mutation is allowed.
readonly SERVICE='universal-video-container.service'
readonly CONTAINER='universal-video-container'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'

fail(){ printf 'UV_CONTAINER_DIAGNOSTIC_FAIL=%s\n' "$1" >&2; exit 1; }
[[ $# -eq 0 ]] || fail USAGE
[[ $(id -u) -eq 0 ]] || fail MUST_RUN_AS_ROOT

systemctl show "$SERVICE" --no-pager \
  -p LoadState -p ActiveState -p SubState -p Result \
  -p ExecMainCode -p ExecMainStatus -p NRestarts -p MainPID -p ControlPID \
  | sed -nE '/^(LoadState|ActiveState|SubState|Result|ExecMainCode|ExecMainStatus|NRestarts|MainPID|ControlPID)=/p'

for property in ExecStartPre ExecStart; do
  index=0
  while IFS= read -r status; do
    [[ "$status" =~ ^[0-9]+$ ]] || continue
    printf '%sStatus%s=%s\n' "$property" "$index" "$status"
    index=$((index + 1))
  done < <(systemctl show "$SERVICE" --no-pager --value --property="$property" 2>/dev/null \
    | grep -oE 'status=[0-9]+' | cut -d= -f2 || true)
done

printf 'service_enabled=%s\n' "$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
printf 'service_job=%s\n' "$(systemctl list-jobs "$SERVICE" --no-legend --no-pager 2>/dev/null | awk 'NR==1 {print $2":"$3":"$4}' | tr -cd 'A-Za-z0-9:_-')"

control_pid="$(systemctl show "$SERVICE" -p ControlPID --value 2>/dev/null || true)"
if [[ "$control_pid" =~ ^[1-9][0-9]*$ && -r "/proc/$control_pid/comm" ]]; then
  control_comm="$(tr -cd 'A-Za-z0-9._-\n' < "/proc/$control_pid/comm" | head -n1)"
  printf 'control_process=%s\n' "${control_comm:-unknown}"
else
  echo 'control_process=none'
fi

if command -v docker >/dev/null 2>&1; then
  docker inspect --type container --format \
    'container_present=true container_status={{.State.Status}} container_running={{.State.Running}} container_restarting={{.State.Restarting}} container_exit={{.State.ExitCode}} container_oom={{.State.OOMKilled}}' \
    "$CONTAINER" 2>/dev/null || echo 'container_present=false'

  readonly ENV_FILE='/opt/bridge-school/universal-video/universal-video-container.env'
  mapfile -t image_lines < <(grep -E '^UNIVERSAL_VIDEO_IMAGE=sha256:[0-9a-f]{64}$' "$ENV_FILE" 2>/dev/null || true)
  if [[ ${#image_lines[@]} -eq 1 ]]; then
    echo 'resident_image_configured=true'
    image_id="${image_lines[0]#UNIVERSAL_VIDEO_IMAGE=}"
    if docker image inspect "$image_id" >/dev/null 2>&1; then
      echo 'resident_image_present=true'
    else
      echo 'resident_image_present=false'
    fi
  else
    echo 'resident_image_configured=false'
    echo 'resident_image_present=unknown'
  fi
  readonly CANDIDATE_ENV_FILE='/opt/bridge-school/universal-video/universal-video-container-candidate.env'
  mapfile -t candidate_lines < <(grep -E '^UNIVERSAL_VIDEO_IMAGE=sha256:[0-9a-f]{64}$' "$CANDIDATE_ENV_FILE" 2>/dev/null || true)
  if [[ ${#candidate_lines[@]} -eq 1 ]]; then
    echo 'candidate_image_configured=true'
    candidate_id="${candidate_lines[0]#UNIVERSAL_VIDEO_IMAGE=}"
    if docker image inspect "$candidate_id" >/dev/null 2>&1; then
      echo 'candidate_image_present=true'
    else
      echo 'candidate_image_present=false'
    fi
  else
    echo 'candidate_image_configured=false'
    echo 'candidate_image_present=unknown'
  fi
else
  echo 'docker_available=false'
fi

source_head="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)"
if [[ "$source_head" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'source_head=%s\n' "$source_head"
else
  echo 'source_head=unknown'
fi

# Report only numeric process identity and ancestry metadata. Command lines and
# environments are intentionally excluded so the diagnostic remains secret-safe.
readonly SOURCE_SERVICE='universal-video.service'
source_root_pid="$(systemctl show "$SOURCE_SERVICE" -p MainPID --value 2>/dev/null || true)"
container_root_pid=''
if command -v docker >/dev/null 2>&1; then
  container_root_pid="$(docker inspect --type container --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || true)"
fi
printf 'source_service_state=%s source_root_pid=%s container_root_pid=%s\n' \
  "$(systemctl is-active "$SOURCE_SERVICE" 2>/dev/null || true)" \
  "${source_root_pid:-unknown}" "${container_root_pid:-unknown}"

descends_from(){
  local child="$1" root="$2" parent
  [[ "$child" =~ ^[1-9][0-9]*$ && "$root" =~ ^[1-9][0-9]*$ ]] || return 1
  while [[ "$child" =~ ^[1-9][0-9]*$ ]]; do
    [[ "$child" == "$root" ]] && return 0
    parent="$(awk '/^PPid:/ {print $2}' "/proc/$child/status" 2>/dev/null || true)"
    [[ "$parent" =~ ^[1-9][0-9]*$ && "$parent" != "$child" ]] || return 1
    child="$parent"
  done
  return 1
}

mapfile -t worker_pids < <(pgrep -f '[u]niversal_video[.]spool_worker' 2>/dev/null || true)
printf 'resident_worker_count=%s\n' "${#worker_pids[@]}"
for worker_pid in "${worker_pids[@]}"; do
  worker_state="$(awk '/^State:/ {print $2}' "/proc/$worker_pid/status" 2>/dev/null || true)"
  source_descendant=false
  container_descendant=false
  descends_from "$worker_pid" "$source_root_pid" && source_descendant=true
  descends_from "$worker_pid" "$container_root_pid" && container_descendant=true
  printf 'resident_worker pid=%s state=%s source_descendant=%s container_descendant=%s\n' \
    "$worker_pid" "${worker_state:-unknown}" "$source_descendant" "$container_descendant"
done

echo 'real_media_canary_run=false'
echo 'service_mutation=false'
echo 'docker_mutation=false'
echo 'UV_CONTAINER_DIAGNOSTIC_PASS'
