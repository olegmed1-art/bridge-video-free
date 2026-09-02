#!/usr/bin/env bash
set -Eeuo pipefail
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
STATUS_DIR="${UNIVERSAL_VIDEO_STATUS_DIR:-/run/bridge-school}"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"
SOURCE_SERVICE="universal-video.service"
CONTAINER_SERVICE="universal-video-container.service"
WORKLOAD_LOCK="$BASE_DIR/spool/.workload.lock"
FILE_ID="${UNIVERSAL_VIDEO_CANARY_FILE_ID:?missing exact canary file id}"
NAME="${UNIVERSAL_VIDEO_CANARY_NAME:?missing exact canary name}"
MIME="${UNIVERSAL_VIDEO_CANARY_MIME:?missing exact canary MIME}"
SIZE="${UNIVERSAL_VIDEO_CANARY_SIZE:?missing exact canary size}"
PARENT="${UNIVERSAL_VIDEO_CANARY_PARENT:?missing exact canary parent}"
[[ "$(id -u)" -eq 0 && "$SIZE" =~ ^[0-9]+$ && "$SIZE" -gt 0 ]]

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

source_state_before=""
container_state_before=""
source_was_active=0
container_was_active=0
window_started=0
lock_held=0
declare -a added_runtime_masks=()

service_state(){
  systemctl show "$1" --property=ActiveState --value 2>/dev/null || true
}

restore_service(){
  local service="$1" should_be_active="$2"
  if [[ "$should_be_active" == 1 ]]; then
    systemctl start "$service" >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "$service" || return 1
  fi
}

cleanup(){
  local rc=$? service cleanup_failed=0
  trap - EXIT INT TERM
  if [[ "$window_started" == 1 ]]; then
    # Keep the exclusive workload fence while forcing both claim paths quiet.
    systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE" >/dev/null 2>&1 || cleanup_failed=1
    for service in "${added_runtime_masks[@]}"; do
      systemctl unmask --runtime "$service" >/dev/null 2>&1 || cleanup_failed=1
    done
    if [[ "$lock_held" == 1 ]]; then
      flock --unlock 9 >/dev/null 2>&1 || cleanup_failed=1
      exec 9>&-
      lock_held=0
    fi
    restore_service "$SOURCE_SERVICE" "$source_was_active" || cleanup_failed=1
    restore_service "$CONTAINER_SERVICE" "$container_was_active" || cleanup_failed=1
  fi
  if (( cleanup_failed != 0 && rc == 0 )); then
    printf 'ERROR: failed to restore pre-attestation resident state\n' >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

assert_known_state(){
  local service="$1" state="$2"
  case "$state" in
    active|inactive|failed) ;;
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

mask_service_for_window(){
  local service="$1" enabled_state
  enabled_state="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  case "$enabled_state" in
    masked|masked-runtime) return ;;
  esac
  systemctl mask --runtime "$service" >/dev/null
  added_runtime_masks+=("$service")
}

commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die 'source commit is unavailable'
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

command -v flock >/dev/null || die 'flock is unavailable'
[[ -d "$BASE_DIR/spool" && ! -L "$BASE_DIR/spool" ]] || die 'unsafe or missing spool mount'
[[ -d "$BASE_DIR/spool/running" && ! -L "$BASE_DIR/spool/running" ]] || die 'unsafe or missing running spool'
if [[ -L "$WORKLOAD_LOCK" || ( -e "$WORKLOAD_LOCK" && ! -f "$WORKLOAD_LOCK" ) ]]; then
  die 'unsafe workload lock'
fi
uid="$(id -u universal-video)"; gid="$(id -g universal-video)"
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
window_started=1

# The exclusive lock was acquired before service mutation. An idle resident can
# therefore be stopped safely, while an active job would have kept the shared
# lock and caused the nonblocking acquisition above to fail closed.
mask_service_for_window "$SOURCE_SERVICE"
mask_service_for_window "$CONTAINER_SERVICE"
systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE" >/dev/null
assert_quiescent

[[ -f "$BASE_DIR/secrets/google-drive-oauth.json" && ! -L "$BASE_DIR/secrets/google-drive-oauth.json" ]]
run_image(){
  verify_image_identity
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --user="$uid:$gid" --env-file "$BASE_DIR/universal-video-container.env" --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" --mount "type=bind,src=$STATUS_DIR,dst=/run/bridge-school" --mount "type=bind,src=$BASE_DIR/secrets,dst=/run/secrets,readonly" "$image_id" "$@"
}
verify_image_identity
printf 'UNIVERSAL_VIDEO_PRECANARY_RUNTIME commit=%s image_digest=%s\n' "$commit" "$image_id"
printf 'UNIVERSAL_VIDEO_PRECANARY_STATE source_service_before=%s container_service_before=%s source_service=inactive container_service=inactive running_jobs=0 workload_fence=exclusive restore_on_exit=true\n' "$source_state_before" "$container_state_before"
run_image python -m universal_video.precanary imports
run_image python -m universal_video.precanary synthetic-result-contract
run_image python -m universal_video.precanary source-identity --file-id "$FILE_ID" --name "$NAME" --mime-type "$MIME" --size "$SIZE" --parent "$PARENT"
verify_image_identity
assert_quiescent
printf 'UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit=%s image_digest=%s video_job_submitted=false drive_write_performed=false canonical_promotion_allowed=false publication_state=NOT_PUBLISHED\n' "$commit" "$image_id"
