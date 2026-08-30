#!/usr/bin/env bash
set -Eeuo pipefail

# Build and attest the isolated container runtime.  Activation is deliberately
# opt-in: no running sidecar is replaced unless the caller sets ..._ACTIVATE=1.

BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"
GROUP_NAME="${UNIVERSAL_VIDEO_UNIX_GROUP:-universal-video}"
SERVICE_NAME="universal-video-container.service"
OLD_SERVICE="${UNIVERSAL_VIDEO_SERVICE_NAME:-universal-video.service}"
ACTIVATE="${UNIVERSAL_VIDEO_CONTAINER_ACTIVATE:-0}"
BUILD_IMAGE="${UNIVERSAL_VIDEO_CONTAINER_BUILD:-1}"
MIN_FREE_KB="${UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB:-8388608}"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"
STATUS_DIR="${UNIVERSAL_VIDEO_STATUS_DIR:-/run/bridge-school}"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
runtime_fail(){ printf '{"error_code":"%s","status":"FAILED"}\n' "$1" >&2; exit 1; }
service_exec_status(){
  local property="$1" prefix="$2" status index=0
  while IFS= read -r status; do
    [[ "$status" =~ ^[0-9]+$ ]] || continue
    printf '%sStatus%s=%s\n' "$prefix" "$index" "$status"
    index=$((index + 1))
  done < <(
    systemctl show "$SERVICE_NAME" --no-pager --value --property="$property" 2>/dev/null \
      | grep -oE 'status=[0-9]+' | cut -d= -f2 || true
  )
}
service_status(){
  systemctl show "$SERVICE_NAME" --no-pager \
    -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts \
    | sed -nE '/^(Result|ExecMainCode|ExecMainStatus|NRestarts)=/p'
  service_exec_status ExecStartPre ExecStartPre
  service_exec_status ExecStart ExecStart
}

[[ "$(id -u)" -eq 0 ]] || die 'run as root on the Oracle host'
[[ "$ACTIVATE" =~ ^[01]$ ]] || die 'UNIVERSAL_VIDEO_CONTAINER_ACTIVATE must be 0 or 1'
[[ "$BUILD_IMAGE" =~ ^[01]$ ]] || die 'UNIVERSAL_VIDEO_CONTAINER_BUILD must be 0 or 1'
[[ "$MIN_FREE_KB" =~ ^[0-9]+$ && "$MIN_FREE_KB" -gt 0 ]] || die 'UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB must be a positive integer'
[[ -d "$SOURCE_DIR/.git" ]] || die 'isolated source checkout missing'
[[ -f "$SOURCE_DIR/deploy/oracle-universal-video/Dockerfile" ]] || die 'container Dockerfile missing'
[[ -f "$SOURCE_DIR/deploy/oracle-universal-video/$SERVICE_NAME" ]] || die 'container service unit missing'
command -v docker >/dev/null || die 'docker is unavailable; install and attest Docker separately'
docker info >/dev/null || die 'docker daemon is unavailable'
id "$USER_NAME" >/dev/null || die 'universal-video Unix user missing'

commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die 'cannot resolve source commit'
image="$IMAGE_REPO:$commit"
for dir in spool output media model-cache secrets; do
  [[ -d "$BASE_DIR/$dir" && ! -L "$BASE_DIR/$dir" ]] || die "unsafe or missing mount: $BASE_DIR/$dir"
done
[[ ! -L "$STATUS_DIR" ]] || die "unsafe or missing mount: $STATUS_DIR"
install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 0750 "$STATUS_DIR"
if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  die 'a video job is running; refusing container rollout'
fi

disk_available_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $4}')"
[[ "$disk_available_kb" =~ ^[0-9]+$ ]] || die 'container disk capacity unavailable'
if (( disk_available_kb < MIN_FREE_KB )); then
  log 'Reclaim unused Universal Video build cache before image build'
  docker builder prune --all --force >/dev/null 2>&1 || true
  docker image prune --all --force >/dev/null 2>&1 || true
  mapfile -t old_image_ids < <(docker image ls --filter "reference=$IMAGE_REPO:*" --format '{{.ID}}' | sort -u)
  for old_image_id in "${old_image_ids[@]}"; do
    if [[ -z "$(docker ps -aq --filter "ancestor=$old_image_id")" ]]; then
      docker image rm "$old_image_id" >/dev/null 2>&1 || true
    fi
  done
  disk_available_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $4}')"
  root_cache=/root/.cache
  if (( disk_available_kb < MIN_FREE_KB )) && [[ -d "$root_cache" && ! -L "$root_cache" ]]; then
    stale_cache_files="$(find "$root_cache" -xdev -type f -mtime +14 -print | wc -l)"
    disk_before_kb="$disk_available_kb"
    find "$root_cache" -xdev -type f -mtime +14 -delete
    find "$root_cache" -xdev -depth -type d -empty -delete
    disk_available_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $4}')"
    disk_freed_kb=$(( disk_available_kb - disk_before_kb ))
    printf 'UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=root-cache age_days=14 files=%s freed_kb=%s\n' "$stale_cache_files" "$disk_freed_kb"
  fi
fi
printf 'UNIVERSAL_VIDEO_CONTAINER_RESOURCE disk_available_kb=%s disk_required_kb=%s\n' "$disk_available_kb" "$MIN_FREE_KB"
if (( disk_available_kb < MIN_FREE_KB )); then
  for storage_area in spool output media model-cache; do
    storage_used_kb="$(du -skx "$BASE_DIR/$storage_area" | awk '{print $1}')"
    printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=%s used_kb=%s\n' "$storage_area" "$storage_used_kb"
  done
  if [[ -d /var/lib/docker && ! -L /var/lib/docker ]]; then
    storage_used_kb="$(du -skx /var/lib/docker | awk '{print $1}')"
    printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=docker used_kb=%s\n' "$storage_used_kb"
  fi
  for storage_spec in \
    "source:$SOURCE_DIR" \
    "bridge-school:/opt/bridge-school" \
    "var-lib:/var/lib" \
    "var-log:/var/log" \
    "home:/home" \
    "tmp:/tmp" \
    "root:/root" \
    "video-venv:$BASE_DIR/.venv" \
    "var-bridge:/var/lib/bridge-school" \
    "containerd:/var/lib/containerd" \
    "snapd:/var/lib/snapd" \
    "apt:/var/lib/apt" \
    "postgresql:/var/lib/postgresql" \
    "root-cache:/root/.cache" \
    "root-local:/root/.local" \
    "root-npm:/root/.npm" \
    "root-cargo:/root/.cargo" \
    "root-rustup:/root/.rustup" \
    "pip-cache:/root/.cache/pip" \
    "hf-cache:/root/.cache/huggingface" \
    "uv-cache:/root/.cache/uv" \
    "torch-cache:/root/.cache/torch" \
    "whisper-cache:/root/.cache/whisper" \
    "playwright-cache:/root/.cache/ms-playwright" \
    "containerd-content:/var/lib/containerd/io.containerd.content.v1.content" \
    "containerd-snapshots:/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs"; do
    storage_area="${storage_spec%%:*}"
    storage_path="${storage_spec#*:}"
    if [[ -d "$storage_path" && ! -L "$storage_path" ]]; then
      storage_used_kb="$(du -skx "$storage_path" | awk '{print $1}')"
      printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=%s used_kb=%s\n' "$storage_area" "$storage_used_kb"
    fi
  done
  storage_used_kb="$(df -Pk "$BASE_DIR" | awk 'NR==2 {print $3}')"
  printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=rootfs used_kb=%s\n' "$storage_used_kb"
  printf '{"error_code":"UV_CONTAINER_DISK_INSUFFICIENT","status":"FAILED"}\n' >&2
  exit 78
fi

log "Build immutable container image for $commit"
if [[ "$BUILD_IMAGE" == 1 ]]; then
  docker build --pull --build-arg "UNIVERSAL_VIDEO_SOURCE_COMMIT=$commit" --tag "$image" -f "$SOURCE_DIR/deploy/oracle-universal-video/Dockerfile" "$SOURCE_DIR" || die 'container image build failed'
else
  docker image inspect "$image" >/dev/null || die 'attested container image unavailable'
fi
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die 'container image digest unavailable'

uid="$(id -u "$USER_NAME")"
gid="$(id -g "$USER_NAME")"
oauth_file="$BASE_DIR/secrets/google-drive-oauth.json"
[[ -f "$oauth_file" && ! -L "$oauth_file" ]] || die 'protected Google Drive OAuth file missing'
cat >"$BASE_DIR/universal-video-container.env" <<EOF
UNIVERSAL_VIDEO_SOURCE_COMMIT=$commit
UNIVERSAL_VIDEO_CONTAINER_UID=$uid
UNIVERSAL_VIDEO_CONTAINER_GID=$gid
UNIVERSAL_VIDEO_IMAGE=$image
UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool
UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output
UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media
UNIVERSAL_VIDEO_STATUS_PATH=/run/bridge-school/universal-video-status.json
HF_HOME=/var/lib/universal-video/model-cache
GOOGLE_DRIVE_OAUTH_JSON_FILE=/run/secrets/google-drive-oauth.json
UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE=1
UNIVERSAL_VIDEO_WHISPER_MODEL=${UNIVERSAL_VIDEO_WHISPER_MODEL:-small}
UNIVERSAL_VIDEO_ASR_THREADS=${UNIVERSAL_VIDEO_ASR_THREADS:-6}
UNIVERSAL_VIDEO_POLL_SECONDS=${UNIVERSAL_VIDEO_POLL_SECONDS:-2}
EOF
chown root:root "$BASE_DIR/universal-video-container.env"
chmod 0640 "$BASE_DIR/universal-video-container.env"

log 'Run container-only readiness gate; no job is submitted'
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --user="$uid:$gid" \
  --env-file "$BASE_DIR/universal-video-container.env" \
  --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" \
  --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" \
  --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" \
  --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" \
  --mount "type=bind,src=$STATUS_DIR,dst=/run/bridge-school" \
  --mount "type=bind,src=$BASE_DIR/secrets,dst=/run/secrets,readonly" "$image" true

install -m 0644 -o root -g root "$SOURCE_DIR/deploy/oracle-universal-video/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
systemd-analyze verify "/etc/systemd/system/$SERVICE_NAME" >/dev/null
if [[ "$ACTIVATE" == 1 ]]; then
  systemctl is-active --quiet "$OLD_SERVICE" && systemctl stop "$OLD_SERVICE"
  if ! systemctl enable --now "$SERVICE_NAME"; then
    service_status
    runtime_fail UV_CONTAINER_SERVICE_ACTIVATION_FAILED
  fi
  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    service_status
    runtime_fail UV_CONTAINER_SERVICE_INACTIVE
  fi
fi
printf 'UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS commit=%s image_digest=%s activated=%s\n' "$commit" "$image_id" "$ACTIVATE"
