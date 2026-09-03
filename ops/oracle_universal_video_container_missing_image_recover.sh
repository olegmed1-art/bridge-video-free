#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly EXPECTED_SHA="${UNIVERSAL_VIDEO_EXPECTED_SHA:?missing exact runtime SHA}"
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SERVICE='universal-video-container.service'
readonly LOCK_FILE="$BASE_DIR/spool/.workload.lock"
readonly ENV_FILE="$BASE_DIR/universal-video-container.env"
readonly CANDIDATE_ENV="$BASE_DIR/universal-video-container-candidate.env"
readonly REPO_URL='https://github.com/olegmed1-art/bridge-video-free.git'
readonly RECOVERY_ROOT='/var/lib/bridge-school/universal-video-container-recovery'

die(){ printf 'UV_CONTAINER_RECOVERY_FAIL=%s\n' "$1" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || die MUST_RUN_AS_ROOT
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || die INVALID_SHA
[[ "$(systemctl is-active assistant-lab.service 2>/dev/null || true)" == active ]] || die PROTECTED_SERVICE
[[ -d "$BASE_DIR/spool/running" && ! -L "$BASE_DIR/spool/running" ]] || die SPOOL_UNSAFE
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die ENV_UNSAFE
install -d -o root -g root -m 0700 "$RECOVERY_ROOT"

exec 9>"$LOCK_FILE"
flock --exclusive --nonblock 9 || die WORKLOAD_LOCKED
find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q . && die JOB_RUNNING

source_dir="$(mktemp -d /opt/bridge-school/.uv-container-recovery-src.XXXXXX)"
backup="$RECOVERY_ROOT/container-env.$(date -u +%Y%m%dT%H%M%SZ).backup"
activated=0
cleanup(){
  rc=$?
  trap - EXIT
  rm -rf -- "$source_dir"
  if (( rc != 0 && activated == 1 )); then
    install -o root -g root -m 0640 "$backup" "$ENV_FILE"
    systemctl restart "$SERVICE" || true
    echo 'rollback=attempted' >&2
  fi
  exit "$rc"
}
trap cleanup EXIT

git clone --quiet --no-tags --filter=blob:none "$REPO_URL" "$source_dir/repo"
git -C "$source_dir/repo" fetch --quiet origin "$EXPECTED_SHA"
git -C "$source_dir/repo" checkout --quiet --detach FETCH_HEAD
[[ "$(git -C "$source_dir/repo" rev-parse HEAD)" == "$EXPECTED_SHA" ]] || die SOURCE_MISMATCH
[[ -z "$(git -C "$source_dir/repo" status --porcelain)" ]] || die SOURCE_DIRTY

image_tag="bridge-school/universal-video:$EXPECTED_SHA"
build=1
docker image inspect "$image_tag" >/dev/null 2>&1 && build=0
UNIVERSAL_VIDEO_SOURCE_DIR="$source_dir/repo" \
UNIVERSAL_VIDEO_DIR="$BASE_DIR" \
UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0 \
UNIVERSAL_VIDEO_CONTAINER_BUILD="$build" \
UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB=5242880 \
  bash "$source_dir/repo/ops/oracle_universal_video_container_install.sh"

[[ -f "$CANDIDATE_ENV" && ! -L "$CANDIDATE_ENV" ]] || die CANDIDATE_ENV_MISSING
mapfile -t image_lines < <(grep -E '^UNIVERSAL_VIDEO_IMAGE=sha256:[0-9a-f]{64}$' "$CANDIDATE_ENV" || true)
[[ ${#image_lines[@]} -eq 1 ]] || die CANDIDATE_IMAGE_AMBIGUOUS
candidate_image="${image_lines[0]#UNIVERSAL_VIDEO_IMAGE=}"
docker image inspect "$candidate_image" >/dev/null || die CANDIDATE_IMAGE_MISSING
[[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$candidate_image")" == "$EXPECTED_SHA" ]] || die IMAGE_REVISION_MISMATCH
find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q . && die JOB_APPEARED

install -o root -g root -m 0600 "$ENV_FILE" "$backup"
systemctl stop "$SERVICE"
install -o root -g root -m 0640 "$CANDIDATE_ENV" "$ENV_FILE"
rm -f -- "$CANDIDATE_ENV"
activated=1
systemctl restart "$SERVICE"
for _ in $(seq 1 20); do
  [[ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" == active ]] && break
  sleep 2
done
[[ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" == active ]] || die SERVICE_NOT_ACTIVE
sleep 8
[[ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" == active ]] || die SERVICE_UNSTABLE
[[ "$(docker inspect --format '{{.Image}}' universal-video-container 2>/dev/null || true)" == "$candidate_image" ]] || die CONTAINER_IMAGE_MISMATCH
activated=0
printf 'UV_CONTAINER_RECOVERY_PASS runtime_sha=%s backup=%s image_digest=%s\n' "$EXPECTED_SHA" "$backup" "$candidate_image"
