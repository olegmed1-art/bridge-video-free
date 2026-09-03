#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Fixed, read-only diagnostics for the Universal Video container resident.
# No service, Docker, media, queue, source, or filesystem mutation is allowed.
readonly SERVICE='universal-video-container.service'
readonly CONTAINER='universal-video-container'

fail(){ printf 'UV_CONTAINER_DIAGNOSTIC_FAIL=%s\n' "$1" >&2; exit 1; }
[[ $# -eq 0 ]] || fail USAGE
[[ $(id -u) -eq 0 ]] || fail MUST_RUN_AS_ROOT

systemctl show "$SERVICE" --no-pager \
  -p LoadState -p ActiveState -p SubState -p Result \
  -p ExecMainCode -p ExecMainStatus -p NRestarts -p MainPID -p ControlPID \
  | sed -nE '/^(LoadState|ActiveState|SubState|Result|ExecMainCode|ExecMainStatus|NRestarts|MainPID|ControlPID)=/p'

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
else
  echo 'docker_available=false'
fi

echo 'real_media_canary_run=false'
echo 'service_mutation=false'
echo 'docker_mutation=false'
echo 'UV_CONTAINER_DIAGNOSTIC_PASS'
