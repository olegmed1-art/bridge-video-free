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
chmod -R a+rX,u-w,g-w,o-w "$source_dir/repo"

speaker_cache="$BASE_DIR/model-cache/speaker"
install -d -o universal-video -g universal-video -m 0750 "$speaker_cache"
PYTHONPATH="$source_dir/repo" SPEAKER_CACHE="$speaker_cache" MODEL_QUARANTINE_ROOT="$RECOVERY_ROOT" python3 - <<'PY'
import hashlib, os, time
from pathlib import Path
from bridge_speaker_diarization_v3 import _ensure_embedding, _ensure_segmentation

root=Path(os.environ['SPEAKER_CACHE'])
expected={
    root/'pyannote-segmentation-3.0.onnx': '220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079',
    root/'3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx': '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b',
}
for path,digest in expected.items():
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        quarantine=Path(os.environ['MODEL_QUARANTINE_ROOT'])/f'{path.name}.{int(time.time())}.invalid'
        path.replace(quarantine)
seg=_ensure_segmentation(root)
emb=_ensure_embedding(root, '3dspeaker')
for path,digest in expected.items():
    observed=hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != digest:
        raise SystemExit(f'model digest mismatch: {path.name}')
PY
chown universal-video:universal-video \
  "$speaker_cache/pyannote-segmentation-3.0.onnx" \
  "$speaker_cache/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
chmod 0640 \
  "$speaker_cache/pyannote-segmentation-3.0.onnx" \
  "$speaker_cache/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

image_tag="bridge-school/universal-video:$EXPECTED_SHA"
build=1
if [[ "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_tag" 2>/dev/null || true)" == "$EXPECTED_SHA" ]]; then
  build=0
fi
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
