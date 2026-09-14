#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only gate used before source preparation or resident quiesce. The exact
# candidate image must already exist from the successful evidence run.

readonly BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
readonly EXPECTED_COMMIT="${UNIVERSAL_VIDEO_EXPECTED_COMMIT:-}"
readonly EXPECTED_DIGEST="${UNIVERSAL_VIDEO_EXPECTED_IMAGE_DIGEST:-}"

fail(){ printf 'UNIVERSAL_VIDEO_PREPROMOTION_PREFLIGHT_FAILED code=%s\n' "$1" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail UV_CONTAINER_PREPROMOTION_NOT_ROOT
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail UV_CONTAINER_PREPROMOTION_COMMIT_INVALID
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail UV_CONTAINER_PREPROMOTION_DIGEST_INVALID
command -v docker >/dev/null || fail UV_CONTAINER_PREPROMOTION_DOCKER_UNAVAILABLE
observed="$(docker image inspect --format '{{.Id}}' "bridge-school/universal-video:$EXPECTED_COMMIT" 2>/dev/null || true)"
[[ "$observed" == "$EXPECTED_DIGEST" ]] || fail UV_CONTAINER_PREPROMOTION_IMAGE_MISMATCH

for dir in spool output media model-cache model-cache/speaker; do
  [[ -d "$BASE_DIR/$dir" && ! -L "$BASE_DIR/$dir" ]] \
    || fail UV_CONTAINER_PREPROMOTION_MOUNT_UNAVAILABLE
done
uid="$(id -u universal-video)" || fail UV_CONTAINER_PREPROMOTION_USER_UNAVAILABLE
gid="$(id -g universal-video)" || fail UV_CONTAINER_PREPROMOTION_USER_UNAVAILABLE

# The image entrypoint hashes and loads both pinned speaker ONNX models before
# executing `true`. No source checkout, service, queue, media, or Drive state is
# changed by this gate.
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  --user="$uid:$gid" \
  --env "UNIVERSAL_VIDEO_SOURCE_COMMIT=$EXPECTED_COMMIT" \
  --env UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool \
  --env UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output \
  --env UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media \
  --env UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE=/var/lib/universal-video/model-cache/speaker \
  --env HF_HOME=/var/lib/universal-video/model-cache \
  --mount "type=bind,src=$BASE_DIR/spool,dst=/var/lib/universal-video/spool" \
  --mount "type=bind,src=$BASE_DIR/output,dst=/var/lib/universal-video/output" \
  --mount "type=bind,src=$BASE_DIR/media,dst=/var/lib/universal-video/media" \
  --mount "type=bind,src=$BASE_DIR/model-cache,dst=/var/lib/universal-video/model-cache" \
  "$EXPECTED_DIGEST" true >/dev/null \
  || fail UV_CONTAINER_PREPROMOTION_SPEAKER_MODEL_INVALID

printf 'UNIVERSAL_VIDEO_PREPROMOTION_PREFLIGHT_PASS commit=%s image_digest=%s\n' \
  "$EXPECTED_COMMIT" "$EXPECTED_DIGEST"
