#!/usr/bin/env bash
set -Eeuo pipefail

# Side-by-side installer for the universal educational video analyzer.
# It must not stop/restart assistant-lab.service or DDS3 and starts no video job.
# The analyzer runs from its own read-only source checkout so updates never touch
# the checkout used by Assistant Lab.

USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"
GROUP_NAME="${UNIVERSAL_VIDEO_UNIX_GROUP:-universal-video}"
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
SERVICE_NAME="${UNIVERSAL_VIDEO_SERVICE_NAME:-universal-video.service}"
SERVICE_SRC="$SOURCE_DIR/deploy/oracle-universal-video/universal-video.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
ACTIVATE="${UNIVERSAL_VIDEO_ACTIVATE:-1}"
MODEL="${UNIVERSAL_VIDEO_WHISPER_MODEL:-small}"
THREADS="${UNIVERSAL_VIDEO_ASR_THREADS:-6}"
PREWARM="${UNIVERSAL_VIDEO_PREWARM_MODEL:-1}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_ACTIVATE must be 0 or 1"
[[ "$PREWARM" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_PREWARM_MODEL must be 0 or 1"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "UNIVERSAL_VIDEO_ASR_THREADS must be positive"
[[ -d "$SOURCE_DIR/.git" ]] || die "isolated source checkout missing at $SOURCE_DIR"
[[ -f "$SOURCE_DIR/universal_video/runner.py" ]] || die "universal video code missing"
[[ -f "$SOURCE_DIR/requirements-universal-video.txt" ]] || die "requirements file missing"
[[ -f "$SERVICE_SRC" ]] || die "systemd unit missing"

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)"
[[ -n "$SOURCE_COMMIT" ]] || die "cannot resolve isolated source commit"
log "Universal-video source commit: $SOURCE_COMMIT"

log "Read-only protection gate for currently running compute"
bash "$SOURCE_DIR/ops/oracle_universal_video_preflight.sh"
BEFORE_ASSISTANT="$(systemctl is-active assistant-lab.service)"
BEFORE_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)"

log "Install host media prerequisites if absent"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends ffmpeg python3-venv ca-certificates >/dev/null
fi
command -v ffmpeg >/dev/null || die "ffmpeg installation failed"
command -v ffprobe >/dev/null || die "ffprobe installation failed"

log "Create isolated Unix identity and directories"
if ! getent group "$GROUP_NAME" >/dev/null 2>&1; then
  groupadd --system "$GROUP_NAME"
fi
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --gid "$GROUP_NAME" --home-dir "$BASE_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi
for d in "$BASE_DIR" "$BASE_DIR/spool" "$BASE_DIR/spool/inbox" "$BASE_DIR/spool/running" "$BASE_DIR/spool/done" "$BASE_DIR/spool/failed" "$BASE_DIR/spool/results" "$BASE_DIR/model-cache" "$BASE_DIR/media" "$BASE_DIR/output"; do
  install -d -m 0750 -o "$USER_NAME" -g "$GROUP_NAME" "$d"
done

log "Build isolated Python runtime"
python3 -m venv "$BASE_DIR/.venv"
"$BASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip >/dev/null
"$BASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$SOURCE_DIR/requirements-universal-video.txt"
chown -R "$USER_NAME:$GROUP_NAME" "$BASE_DIR/.venv"

log "Verify runtime imports"
PYTHONPATH="$SOURCE_DIR" "$BASE_DIR/.venv/bin/python" - <<'PY'
from universal_video.contract import validate_job
from universal_video.profiles import PROFILES
from faster_whisper import WhisperModel
assert 'transcript_only' in PROFILES
assert 'educational' in PROFILES
assert 'bridge_lesson' in PROFILES
validate_job({'job_id':'install-smoke','profile':'transcript_only','source':{'kind':'local_path','path':'/opt/bridge-school/universal-video/media/test.mp4'}}, allowed_local_root='/opt/bridge-school/universal-video/media')
print('UNIVERSAL_VIDEO_IMPORTS_PASS')
PY

if [[ "$PREWARM" == "1" ]]; then
  log "Prewarm bounded CPU ASR model cache ($MODEL)"
  runuser -u "$USER_NAME" -- env HF_HOME="$BASE_DIR/model-cache" UNIVERSAL_VIDEO_WHISPER_MODEL="$MODEL" \
    "$BASE_DIR/.venv/bin/python" - <<'PY'
import os
from faster_whisper import WhisperModel
name=os.environ['UNIVERSAL_VIDEO_WHISPER_MODEL']
WhisperModel(name, device='cpu', compute_type='int8', cpu_threads=1)
print('UNIVERSAL_VIDEO_MODEL_PREWARM_PASS')
PY
fi

log "Write non-secret runtime environment"
cat >"$BASE_DIR/universal-video.env" <<EOF
UNIVERSAL_VIDEO_SPOOL_ROOT=$BASE_DIR/spool
UNIVERSAL_VIDEO_OUTPUT_ROOT=$BASE_DIR/output
UNIVERSAL_VIDEO_MEDIA_ROOT=$BASE_DIR/media
UNIVERSAL_VIDEO_WHISPER_MODEL=$MODEL
UNIVERSAL_VIDEO_ASR_THREADS=$THREADS
UNIVERSAL_VIDEO_POLL_SECONDS=2
UNIVERSAL_VIDEO_SOURCE_COMMIT=$SOURCE_COMMIT
EOF
chown root:root "$BASE_DIR/universal-video.env"
chmod 0644 "$BASE_DIR/universal-video.env"

log "Install sidecar systemd unit without touching existing services"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null
if [[ "$ACTIVATE" == "1" ]]; then
  systemctl enable --now "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "universal video sidecar failed to become active"
  }
fi

log "Post-install non-regression gate"
[[ "$(systemctl is-active assistant-lab.service)" == "$BEFORE_ASSISTANT" ]] || die "assistant-lab state changed"
AFTER_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)"
BEFORE_READY="$BEFORE_READY" AFTER_READY="$AFTER_READY" python3 - <<'PY'
import json, os
before=json.loads(os.environ['BEFORE_READY']); after=json.loads(os.environ['AFTER_READY'])
for x in (before,after):
    assert x.get('status') == 'ready', x
    assert x.get('engine') == 'DDS3', x
    assert x.get('fallback_used') is False, x
print('CURRENT_COMPUTE_NONREGRESSION_PASS')
PY

printf 'UNIVERSAL_VIDEO_INSTALL_PASS activated=%s model=%s threads=%s source_commit=%s\n' "$ACTIVATE" "$MODEL" "$THREADS" "$SOURCE_COMMIT"
