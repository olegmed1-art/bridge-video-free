#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
readonly SERVICE=universal-video-container.service
readonly RUNTIME=/run/bridge-school
readonly CONFIG=/etc/tmpfiles.d/bridge-school-universal-video.conf
readonly RULE='d /run/bridge-school 0750 universal-video universal-video -'
fail(){ printf 'RUNTIME_DIRECTORY_REPAIR_FAIL=%s\n' "$1" >&2; exit 1; }
[[ $# -eq 0 && $(id -u) -eq 0 ]] || fail USAGE
[[ $(hostname) == bridge-school-dds3-frankfurt ]] || fail HOST_IDENTITY
exec 8>/run/lock/oracle-workload-mutation.lock
flock --exclusive --nonblock 8 || fail HOST_BUSY
[[ ! -e /var/lib/bridge-school/issue-881-capacity-lease ]] || fail CAPACITY_FENCE
[[ ! -L "$RUNTIME" && ! -e "$RUNTIME" ]] || fail RUNTIME_ALREADY_PRESENT
[[ -d /etc/tmpfiles.d && ! -L /etc/tmpfiles.d && ! -L "$CONFIG" ]] || fail CONFIG_PATH
[[ $(systemctl is-enabled "$SERVICE") == enabled ]] || fail SERVICE_NOT_ENABLED
[[ $(systemctl show "$SERVICE" -p SubState --value) == auto-restart ]] || fail SERVICE_STATE_CHANGED
systemctl show "$SERVICE" -p ExecStartPre --value | grep -q 'status=226' || fail NAMESPACE_STATE_CHANGED
[[ $(systemctl show universal-video.service -p ActiveState --value) == inactive ]] || fail SOURCE_SERVICE_ACTIVE
docker info --format '{{.ServerVersion}}' >/dev/null || fail DOCKER_UNAVAILABLE
running="$(docker ps --filter name=^/universal-video-container$ --format '{{.ID}}')"
[[ -z "$running" ]] || fail CONTAINER_RUNNING
worker_rc=0
pgrep -f '[u]niversal_video[.]spool_worker' >/dev/null || worker_rc=$?
[[ "$worker_rc" == 1 ]] || fail WORKER_PRESENT_OR_UNKNOWN
uid="$(id -u universal-video)"
gid="$(id -g universal-video)"
[[ "$uid" =~ ^[1-9][0-9]*$ && "$gid" =~ ^[1-9][0-9]*$ ]] || fail SERVICE_IDENTITY
command -v systemd-tmpfiles >/dev/null || fail TMPFILES_UNAVAILABLE
if [[ -e "$CONFIG" ]]; then
  [[ -f "$CONFIG" && $(cat "$CONFIG") == "$RULE" ]] || fail CONFIG_CONFLICT
else
  # Noclobber prevents replacement of an unexpected concurrently created file.
  (set -o noclobber; printf '%s\n' "$RULE" > "$CONFIG") || fail CONFIG_CREATE
  chmod 0644 "$CONFIG"
fi
# The enabled service retries naturally. Do not stop/restart services or Docker.
systemd-tmpfiles --create "$CONFIG" || fail TMPFILES_CREATE
[[ -d "$RUNTIME" && ! -L "$RUNTIME" ]] || fail RUNTIME_CREATE
[[ $(stat -c '%u:%g:%a' "$RUNTIME") == "$uid:$gid:750" ]] || fail RUNTIME_METADATA
echo 'runtime_directory=CREATED_WITH_BOOT_RULE'
echo 'oci_mutation=false'
echo 'media_canary=false'
# Retain the safe directory and boot rule on failure; never remove a path that
# the naturally restarting service may already have mounted.
stable=0
for _ in $(seq 1 30); do
  state="$(systemctl show "$SERVICE" -p ActiveState --value)"
  container="$(docker inspect --type container --format '{{.State.Running}}' universal-video-container 2>/dev/null || true)"
  if [[ "$state" == active && "$container" == true ]]; then
    stable=$((stable + 1))
    if (( stable >= 3 )); then echo 'RUNTIME_DIRECTORY_REPAIR_PASS'; exit 0; fi
  else
    stable=0
  fi
  sleep 2
done
fail SERVICE_NOT_STABLE
