#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot payload for OCI Instance Agent Run Command / root shell.
# It prepares a dedicated source checkout and activates the universal video
# sidecar without updating, stopping, or restarting Assistant Lab or DDS3.

REPO_URL="${UNIVERSAL_VIDEO_REPO_URL:-https://github.com/olegmed1-art/bridge-video-free.git}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
GIT_REF="${UNIVERSAL_VIDEO_GIT_REF:-main}"
ACTIVATE="${UNIVERSAL_VIDEO_ACTIVATE:-1}"
PREWARM="${UNIVERSAL_VIDEO_PREWARM_MODEL:-1}"
RUN_SMOKE="${UNIVERSAL_VIDEO_RUN_SMOKE:-0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle instance"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_ACTIVATE must be 0 or 1"
[[ "$PREWARM" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_PREWARM_MODEL must be 0 or 1"
[[ "$RUN_SMOKE" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_RUN_SMOKE must be 0 or 1"

log "Capture protected service state before changes"
BEFORE_ASSISTANT="$(systemctl is-active assistant-lab.service)"
[[ "$BEFORE_ASSISTANT" == "active" ]] || die "assistant-lab.service is not active"
BEFORE_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)" || die "DDS3 readyz failed before activation"
BEFORE_READY="$BEFORE_READY" python3 - <<'PY'
import json, os
x=json.loads(os.environ['BEFORE_READY'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
print('DDS3_BEFORE_PASS')
PY

log "Prepare dedicated universal-video source checkout"
if ! command -v git >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends git ca-certificates >/dev/null
fi
mkdir -p "$(dirname "$SOURCE_DIR")"
if [[ -d "$SOURCE_DIR/.git" ]]; then
  current_origin="$(git -C "$SOURCE_DIR" remote get-url origin 2>/dev/null || true)"
  [[ "$current_origin" == "$REPO_URL" ]] || die "unexpected origin in isolated source checkout: $current_origin"
  [[ -z "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || die "isolated source checkout is dirty"
else
  [[ ! -e "$SOURCE_DIR" || -z "$(ls -A "$SOURCE_DIR" 2>/dev/null)" ]] || die "source directory exists and is not an empty git checkout"
  rm -rf "$SOURCE_DIR"
  git clone --quiet --no-tags --filter=blob:none "$REPO_URL" "$SOURCE_DIR"
fi

git -C "$SOURCE_DIR" fetch --quiet --prune origin "$GIT_REF"
git -C "$SOURCE_DIR" checkout --quiet --detach FETCH_HEAD
RESOLVED_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
log "Resolved universal-video source: $RESOLVED_COMMIT"
chown -R root:root "$SOURCE_DIR"
chmod -R a-w "$SOURCE_DIR"

log "Run side-by-side installer"
UNIVERSAL_VIDEO_SOURCE_DIR="$SOURCE_DIR" \
UNIVERSAL_VIDEO_DIR="$BASE_DIR" \
UNIVERSAL_VIDEO_ACTIVATE="$ACTIVATE" \
UNIVERSAL_VIDEO_PREWARM_MODEL="$PREWARM" \
  bash "$SOURCE_DIR/ops/oracle_universal_video_install.sh"

log "Activation evidence"
printf 'source_commit=%s\n' "$RESOLVED_COMMIT"
printf 'assistant_lab=%s\n' "$(systemctl is-active assistant-lab.service)"
printf 'universal_video_enabled=%s\n' "$(systemctl is-enabled universal-video.service 2>/dev/null || true)"
printf 'universal_video_active=%s\n' "$(systemctl is-active universal-video.service 2>/dev/null || true)"
ffmpeg -version | head -1
"$BASE_DIR/.venv/bin/python" --version
runuser -u universal-video -- env HF_HOME="$BASE_DIR/model-cache" "$BASE_DIR/.venv/bin/python" - <<'PY'
import faster_whisper
print('faster_whisper_import=PASS')
PY
find "$BASE_DIR/model-cache" -maxdepth 4 -type f -print | head -20 || true

[[ "$(systemctl is-active assistant-lab.service)" == "$BEFORE_ASSISTANT" ]] || die "assistant-lab state changed"
AFTER_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)" || die "DDS3 readyz failed after activation"
AFTER_READY="$AFTER_READY" python3 - <<'PY'
import json, os
x=json.loads(os.environ['AFTER_READY'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
print('DDS3_AFTER_PASS')
PY

if [[ "$RUN_SMOKE" == "1" ]]; then
  log "Run bounded synthetic local smoke job"
  media="$BASE_DIR/media/universal-video-smoke.mp4"
  job="$BASE_DIR/spool/inbox/universal-video-smoke.json"
  rm -f "$BASE_DIR/spool/done/universal-video-smoke.json" "$BASE_DIR/spool/failed/universal-video-smoke.json"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i color=c=black:s=320x180:d=3 \
    -f lavfi -i sine=frequency=440:duration=3 \
    -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$media"
  chown universal-video:universal-video "$media"
  cat >"$job.tmp" <<EOF
{"job_id":"universal-video-smoke","profile":"transcript_only","project":"infrastructure-smoke","source":{"kind":"local_path","path":"$media"},"metadata":{"synthetic":true},"options":{"max_duration_seconds":10,"chunk_seconds":60}}
EOF
  chown universal-video:universal-video "$job.tmp"
  mv "$job.tmp" "$job"
  deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    if [[ -f "$BASE_DIR/spool/done/universal-video-smoke.json" ]]; then
      cat "$BASE_DIR/spool/done/universal-video-smoke.json"
      echo UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS
      break
    fi
    if [[ -f "$BASE_DIR/spool/failed/universal-video-smoke.json" ]]; then
      cat "$BASE_DIR/spool/failed/universal-video-smoke.json" >&2
      die "synthetic smoke job failed"
    fi
    sleep 2
  done
  [[ -f "$BASE_DIR/spool/done/universal-video-smoke.json" ]] || die "synthetic smoke job timed out"
fi

echo UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS
