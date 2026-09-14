#!/usr/bin/env bash
set -Eeuo pipefail

# Build and import-check an exact Universal Video image without replacing the
# resident checkout, writing a systemd unit, or controlling any service.

SOURCE_DIR="${UNIVERSAL_VIDEO_BUILD_SOURCE_DIR:?UNIVERSAL_VIDEO_BUILD_SOURCE_DIR is required}"
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
EXPECTED_COMMIT="${UNIVERSAL_VIDEO_EXPECTED_COMMIT:?UNIVERSAL_VIDEO_EXPECTED_COMMIT is required}"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"
USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || die 'run as root on the Oracle host'
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die 'expected commit is invalid'
[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || die 'isolated build checkout missing'
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_COMMIT" ]] || die 'isolated build checkout is stale'
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || die 'isolated build checkout is dirty'
command -v docker >/dev/null || die 'docker is unavailable'
docker info >/dev/null || die 'docker daemon is unavailable'
id "$USER_NAME" >/dev/null || die 'universal-video Unix user missing'
if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  die 'a video job is running; refusing image build'
fi

image="$IMAGE_REPO:$EXPECTED_COMMIT"
timeout --foreground --signal=TERM --kill-after=30s 1200 \
  docker build --pull --build-arg "UNIVERSAL_VIDEO_SOURCE_COMMIT=$EXPECTED_COMMIT" \
  --tag "$image" -f "$SOURCE_DIR/deploy/oracle-universal-video/Dockerfile" "$SOURCE_DIR"
digest="$(docker image inspect --format '{{.Id}}' "$image")"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die 'image digest unavailable'

uid="$(id -u "$USER_NAME")"
gid="$(id -g "$USER_NAME")"
for dir in spool output media model-cache; do
  [[ -d "$BASE_DIR/$dir" && ! -L "$BASE_DIR/$dir" ]] || die "unsafe or missing mount: $BASE_DIR/$dir"
done
status_dir="$(mktemp -d /run/bridge-school/uv-image-preflight.XXXXXX)"
trap 'rm -rf -- "$status_dir"' EXIT
chown "$uid:$gid" "$status_dir"
chmod 0750 "$status_dir"

docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --user="$uid:$gid" \
  -e "UNIVERSAL_VIDEO_SOURCE_COMMIT=$EXPECTED_COMMIT" \
  -e UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool \
  -e UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output \
  -e UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media \
  -e UNIVERSAL_VIDEO_STATUS_PATH=/run/bridge-school/universal-video-status.json \
  -e HF_HOME=/var/lib/universal-video/model-cache \
  --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" \
  --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" \
  --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" \
  --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" \
  --mount "type=bind,src=$status_dir,dst=/run/bridge-school" \
  "$image" true

printf 'UNIVERSAL_VIDEO_CONTAINER_BUILD_PREFLIGHT_PASS commit=%s image_digest=%s activated=0 lifecycle_action=0\n' \
  "$EXPECTED_COMMIT" "$digest"
