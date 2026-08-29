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
SECRETS_DIR="${UNIVERSAL_VIDEO_SECRETS_DIR:-$BASE_DIR/secrets}"
SECRETS_ENV_FILE="${UNIVERSAL_VIDEO_SECRETS_ENV_FILE:-$BASE_DIR/universal-video-secrets.env}"
DRIVE_OAUTH_FILE="${UNIVERSAL_VIDEO_DRIVE_OAUTH_FILE:-$SECRETS_DIR/google-drive-oauth.json}"
ACTIVATE="${UNIVERSAL_VIDEO_ACTIVATE:-1}"
MODEL="${UNIVERSAL_VIDEO_WHISPER_MODEL:-small}"
THREADS="${UNIVERSAL_VIDEO_ASR_THREADS:-6}"
PREWARM="${UNIVERSAL_VIDEO_PREWARM_MODEL:-1}"
PREVIOUSLY_ACTIVE="${UNIVERSAL_VIDEO_PREVIOUSLY_ACTIVE:-0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_ACTIVATE must be 0 or 1"
[[ "$PREWARM" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_PREWARM_MODEL must be 0 or 1"
[[ "$PREVIOUSLY_ACTIVE" =~ ^[01]$ ]] || die "UNIVERSAL_VIDEO_PREVIOUSLY_ACTIVE must be 0 or 1"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "UNIVERSAL_VIDEO_ASR_THREADS must be positive"
[[ -d "$SOURCE_DIR/.git" ]] || die "isolated source checkout missing at $SOURCE_DIR"
[[ -f "$SOURCE_DIR/universal_video/runner.py" ]] || die "universal video code missing"
[[ -f "$SOURCE_DIR/requirements-universal-video-speaker.txt" ]] || die "speaker requirements file missing"
[[ -f "$SERVICE_SRC" ]] || die "systemd unit missing"

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)"
[[ -n "$SOURCE_COMMIT" ]] || die "cannot resolve isolated source commit"
log "Universal-video source commit: $SOURCE_COMMIT"

log "Read-only protection gate for currently running compute"
bash "$SOURCE_DIR/ops/oracle_universal_video_preflight.sh"
BEFORE_ASSISTANT="$(systemctl is-active assistant-lab.service)"
BEFORE_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)"
VIDEO_WAS_ACTIVE="$PREVIOUSLY_ACTIVE"

log "Install host media prerequisites if absent"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends ffmpeg python3-venv ca-certificates >/dev/null
fi
command -v ffmpeg >/dev/null || die "ffmpeg installation failed"
command -v ffprobe >/dev/null || die "ffprobe installation failed"

log "Create isolated Unix identity and protected directory chain"
if ! getent group "$GROUP_NAME" >/dev/null 2>&1; then
  groupadd --system "$GROUP_NAME"
fi
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --gid "$GROUP_NAME" --home-dir "$BASE_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi

# The worker owns the spool leaves and virtualenv. Quiesce it before root
# validates or changes that path chain; on any later failure it deliberately
# remains stopped rather than reopening a partially migrated boundary.
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  if runuser -u "$USER_NAME" -- find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
    die "universal-video has a running job; refusing runtime upgrade"
  fi
  systemctl stop "$SERVICE_NAME"
  systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null && die "universal-video failed to stop"
  VIDEO_WAS_ACTIVE=1
  if runuser -u "$USER_NAME" -- find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
    die "universal-video accepted a job while stopping; leaving sidecar stopped"
  fi
fi

ensure_real_dir(){
  local path="$1" owner="$2" group="$3" mode="$4"
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -d "$path" && ! -L "$path" ]] || die "unsafe directory path: $path"
  fi
  install -d -m "$mode" -o "$owner" -g "$group" "$path"
}

ensure_real_dir "$BASE_DIR" root "$GROUP_NAME" 0750
ensure_real_dir "$BASE_DIR/spool" root "$GROUP_NAME" 0750
for d in inbox running done failed results; do
  ensure_real_dir "$BASE_DIR/spool/$d" "$USER_NAME" "$GROUP_NAME" 0750
  chown "$USER_NAME:$GROUP_NAME" "$BASE_DIR/spool/$d"
  chmod 0750 "$BASE_DIR/spool/$d"
done
for d in model-cache media output; do
  ensure_real_dir "$BASE_DIR/$d" "$USER_NAME" "$GROUP_NAME" 0750
done
ensure_real_dir "$SECRETS_DIR" root "$GROUP_NAME" 0750
bash "$SOURCE_DIR/ops/oracle_universal_video_spool_guard.sh" \
  verify "$BASE_DIR" root "$USER_NAME" "$GROUP_NAME"
runuser -u "$USER_NAME" -- /usr/bin/python3 - "$BASE_DIR/spool" <<'PY'
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
for leaf in ("inbox", "running", "done", "failed", "results"):
    path = root / leaf / f".write-check-{os.getpid()}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    path.unlink()
print("UNIVERSAL_VIDEO_SPOOL_WRITE_ACCESS_PASS")
PY

log "Prepare file-backed secret boundary for optional Google Drive sources"
if [[ ! -e "$SECRETS_ENV_FILE" ]]; then
  printf 'GOOGLE_DRIVE_OAUTH_JSON_FILE=%s\n' "$DRIVE_OAUTH_FILE" >"$SECRETS_ENV_FILE"
fi
chown root:"$GROUP_NAME" "$SECRETS_ENV_FILE"
chmod 0640 "$SECRETS_ENV_FILE"
if [[ -f "$DRIVE_OAUTH_FILE" ]]; then
  chown root:"$GROUP_NAME" "$DRIVE_OAUTH_FILE"
  chmod 0640 "$DRIVE_OAUTH_FILE"
  DRIVE_OAUTH_FILE="$DRIVE_OAUTH_FILE" python3 - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['DRIVE_OAUTH_FILE'])
x=json.loads(p.read_text(encoding='utf-8'))
assert isinstance(x, dict)
for key in ('client_id','client_secret','refresh_token'):
    assert isinstance(x.get(key), str) and x[key].strip(), key
print('UNIVERSAL_VIDEO_DRIVE_SECRET=CONFIGURED')
PY
else
  echo 'UNIVERSAL_VIDEO_DRIVE_SECRET=NOT_CONFIGURED_LOCAL_PATH_ONLY'
fi

log "Build isolated Python runtime"
if [[ -e "$BASE_DIR/.venv" || -L "$BASE_DIR/.venv" ]]; then
  [[ -d "$BASE_DIR/.venv" && ! -L "$BASE_DIR/.venv" ]] || die 'unsafe virtualenv path'
else
  /usr/bin/python3 -m venv "$BASE_DIR/.venv"
  chown -R "$USER_NAME:$GROUP_NAME" "$BASE_DIR/.venv"
fi
runuser -u "$USER_NAME" -- "$BASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip >/dev/null
runuser -u "$USER_NAME" -- "$BASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$SOURCE_DIR/requirements-universal-video-speaker.txt"

log "Verify runtime imports"
runuser -u "$USER_NAME" -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
  "$BASE_DIR/.venv/bin/python" - <<'PY'
from universal_video.contract import validate_job
from universal_video.drive_adapter import access_token
from universal_video.profiles import PROFILES
from universal_video.speaker_structure import run_speaker_structure
from faster_whisper import WhisperModel
import numpy
assert 'transcript_only' in PROFILES
assert 'educational' in PROFILES
assert 'bridge_lesson' in PROFILES
assert callable(run_speaker_structure)
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
  systemctl enable "$SERVICE_NAME" >/dev/null
  systemctl start "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "universal video sidecar failed to become active"
  }
  VIDEO_WAS_ACTIVE=0
elif (( VIDEO_WAS_ACTIVE == 1 )); then
  systemctl start "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || die "universal video sidecar failed to restore its prior active state"
  VIDEO_WAS_ACTIVE=0
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
