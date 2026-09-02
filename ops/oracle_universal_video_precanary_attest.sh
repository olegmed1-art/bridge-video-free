#!/usr/bin/env bash
set -Eeuo pipefail
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
STATUS_DIR="${UNIVERSAL_VIDEO_STATUS_DIR:-/run/bridge-school}"
IMAGE_REPO="${UNIVERSAL_VIDEO_IMAGE_REPO:-bridge-school/universal-video}"
FILE_ID="${UNIVERSAL_VIDEO_CANARY_FILE_ID:?missing exact canary file id}"
NAME="${UNIVERSAL_VIDEO_CANARY_NAME:?missing exact canary name}"
MIME="${UNIVERSAL_VIDEO_CANARY_MIME:?missing exact canary MIME}"
SIZE="${UNIVERSAL_VIDEO_CANARY_SIZE:?missing exact canary size}"
PARENT="${UNIVERSAL_VIDEO_CANARY_PARENT:?missing exact canary parent}"
[[ "$(id -u)" -eq 0 && "$SIZE" =~ ^[0-9]+$ && "$SIZE" -gt 0 ]]
commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"; [[ "$commit" =~ ^[0-9a-f]{40}$ ]]
image="$IMAGE_REPO:$commit"; image_id="$(docker image inspect --format '{{.Id}}' "$image")"; [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then echo 'active video job; refuse attestation' >&2; exit 1; fi
uid="$(id -u universal-video)"; gid="$(id -g universal-video)"
[[ -f "$BASE_DIR/secrets/google-drive-oauth.json" && ! -L "$BASE_DIR/secrets/google-drive-oauth.json" ]]
run_image(){ docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g --user="$uid:$gid" --env-file "$BASE_DIR/universal-video-container.env" --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" --mount "type=bind,src=$STATUS_DIR,dst=/run/bridge-school" --mount "type=bind,src=$BASE_DIR/secrets,dst=/run/secrets,readonly" "$image" "$@"; }
printf 'UNIVERSAL_VIDEO_PRECANARY_RUNTIME commit=%s image_digest=%s\n' "$commit" "$image_id"
printf 'UNIVERSAL_VIDEO_PRECANARY_STATE source_service=%s container_service=%s running_jobs=0\n' "$(systemctl is-active universal-video.service 2>/dev/null || true)" "$(systemctl is-active universal-video-container.service 2>/dev/null || true)"
run_image python -m universal_video.precanary imports
run_image python -m universal_video.precanary synthetic-result-contract
run_image python -m universal_video.precanary source-identity --file-id "$FILE_ID" --name "$NAME" --mime-type "$MIME" --size "$SIZE" --parent "$PARENT"
printf 'UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit=%s image_digest=%s video_job_submitted=false drive_write_performed=false canonical_promotion_allowed=false publication_state=NOT_PUBLISHED\n' "$commit" "$image_id"
