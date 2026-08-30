#!/usr/bin/env bash
set -Eeuo pipefail

# Build and attest the isolated container runtime.  Activation is deliberately
# opt-in: no running sidecar is replaced unless the caller sets ..._ACTIVATE=1.

BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"
SERVICE_NAME="universal-video-container.service"
OLD_SERVICE="${UNIVERSAL_VIDEO_SERVICE_NAME:-universal-video.service}"
ACTIVATE="${UNIVERSAL_VIDEO_CONTAINER_ACTIVATE:-0}"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[[ "$(id -u)" -eq 0 ]] || die 'run as root on the Oracle host'
[[ "$ACTIVATE" =~ ^[01]$ ]] || die 'UNIVERSAL_VIDEO_CONTAINER_ACTIVATE must be 0 or 1'
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
if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  die 'a video job is running; refusing container rollout'
fi

log "Build immutable container image for $commit"
docker build --pull --build-arg "UNIVERSAL_VIDEO_SOURCE_COMMIT=$commit" --tag "$image" -f "$SOURCE_DIR/deploy/oracle-universal-video/Dockerfile" "$SOURCE_DIR"
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
  --mount "type=bind,src=$BASE_DIR/secrets,dst=/run/secrets,readonly" "$image" true

install -m 0644 -o root -g root "$SOURCE_DIR/deploy/oracle-universal-video/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
systemd-analyze verify "/etc/systemd/system/$SERVICE_NAME" >/dev/null
if [[ "$ACTIVATE" == 1 ]]; then
  systemctl is-active --quiet "$OLD_SERVICE" && systemctl stop "$OLD_SERVICE"
  systemctl enable --now "$SERVICE_NAME"
  systemctl is-active --quiet "$SERVICE_NAME" || die 'container service did not become active'
fi
printf 'UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS commit=%s image_digest=%s activated=%s\n' "$commit" "$image_id" "$ACTIVATE"
