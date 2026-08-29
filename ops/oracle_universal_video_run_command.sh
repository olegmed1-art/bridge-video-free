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

VIDEO_WAS_ACTIVE=0
if systemctl is-active --quiet universal-video.service 2>/dev/null; then
  if runuser -u universal-video -- find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
    die "universal-video has a running job; refusing source upgrade"
  fi
  systemctl stop universal-video.service
  systemctl is-active --quiet universal-video.service 2>/dev/null && die "universal-video failed to stop"
  VIDEO_WAS_ACTIVE=1
  if runuser -u universal-video -- find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
    die "universal-video accepted a job while stopping; leaving sidecar stopped"
  fi
fi

log "Prepare dedicated universal-video source checkout"
if ! command -v git >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends git ca-certificates >/dev/null
fi
mkdir -p "$(dirname "$SOURCE_DIR")"

# Always build and verify the requested revision in a fresh sibling checkout.
# The current checkout is never reset or cleaned in-place: if it is dirty, it
# is preserved verbatim for diagnosis instead of risking loss of local state.
STAGING_DIR="$(mktemp -d "$(dirname "$SOURCE_DIR")/.universal-video-src.next.XXXXXX")"
rmdir "$STAGING_DIR"
cleanup_staging(){
  if [[ -n "${STAGING_DIR:-}" && -e "$STAGING_DIR" ]]; then rm -rf "$STAGING_DIR"; fi
}
trap cleanup_staging EXIT

git clone --quiet --no-tags --filter=blob:none "$REPO_URL" "$STAGING_DIR"
git -C "$STAGING_DIR" fetch --quiet --prune origin "$GIT_REF"
git -C "$STAGING_DIR" checkout --quiet --detach FETCH_HEAD
[[ -z "$(git -C "$STAGING_DIR" status --porcelain)" ]] || die "fresh staged source checkout is unexpectedly dirty"
RESOLVED_COMMIT="$(git -C "$STAGING_DIR" rev-parse HEAD)"
log "Resolved universal-video source: $RESOLVED_COMMIT"

OLD_DIR=""
OLD_DIRTY=0
if [[ -e "$SOURCE_DIR" ]]; then
  if [[ -d "$SOURCE_DIR/.git" ]]; then
    current_origin="$(git -C "$SOURCE_DIR" remote get-url origin 2>/dev/null || true)"
    [[ "$current_origin" == "$REPO_URL" ]] || die "unexpected origin in isolated source checkout: $current_origin"
    if [[ -n "$(git -C "$SOURCE_DIR" status --porcelain)" ]]; then OLD_DIRTY=1; fi
  elif [[ -n "$(ls -A "$SOURCE_DIR" 2>/dev/null)" ]]; then
    OLD_DIRTY=1
  fi
  OLD_DIR="${SOURCE_DIR}.previous.$(date -u +%Y%m%dT%H%M%SZ).$$"
  mv "$SOURCE_DIR" "$OLD_DIR"
fi
mv "$STAGING_DIR" "$SOURCE_DIR"
STAGING_DIR=""
chown -R root:root "$SOURCE_DIR"
chmod -R a-w "$SOURCE_DIR"

if [[ "$OLD_DIRTY" == "1" ]]; then
  PRESERVED_DIR="${SOURCE_DIR}.preserved.$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$OLD_DIR" "$PRESERVED_DIR"
  OLD_DIR="$PRESERVED_DIR"
  echo "UNIVERSAL_VIDEO_PREVIOUS_DIR=PRESERVED_DIRTY"
  log "Previous dirty checkout preserved without modification: $PRESERVED_DIR"
elif [[ -n "$OLD_DIR" ]]; then
  # Keep the prior clean tree until the installer has successfully restarted the
  # service; removal happens only after the post-install non-regression gate.
  echo "UNIVERSAL_VIDEO_PREVIOUS_DIR=STAGED_CLEAN"
fi

log "Run side-by-side installer"
UNIVERSAL_VIDEO_SOURCE_DIR="$SOURCE_DIR" \
UNIVERSAL_VIDEO_DIR="$BASE_DIR" \
UNIVERSAL_VIDEO_ACTIVATE="$ACTIVATE" \
UNIVERSAL_VIDEO_PREWARM_MODEL="$PREWARM" \
UNIVERSAL_VIDEO_PREVIOUSLY_ACTIVE="$VIDEO_WAS_ACTIVE" \
PYTHONDONTWRITEBYTECODE=1 \
  bash "$SOURCE_DIR/ops/oracle_universal_video_install.sh"

# Install the bounded generic control plane only after the isolated checkout
# and sidecar have passed their own gates. This grants ocarun two validated
# operations, never a shell or an arbitrary filesystem path.
log "Install bounded generic Universal Video operator"
SOURCE_FILE="$SOURCE_DIR/ops/universal_video_operator.sh" \
EXPECTED_RUNTIME_COMMIT="$RESOLVED_COMMIT" \
  bash "$SOURCE_DIR/ops/install_universal_video_operator.sh"
sudo -u ocarun sudo -n /usr/local/sbin/universal-video status install-smoke >/dev/null

# Keep the fixed evidence-export entrypoint and its root-owned source pin on
# the same exact revision as the resident worker. The installer exposes only
# fixed audit/productionize/repair/export commands and performs its own visudo,
# ownership, argument-rejection, and read-only audit gates. It does not submit
# a job, start ASR, or publish evidence.
log "Install revision-bound Universal Video admin and evidence export entrypoints"
SOURCE_COMMIT="$RESOLVED_COMMIT" \
  bash "$SOURCE_DIR/ops/install_universal_video_ocarun_admin.sh"
echo 'universal_video_admin=installed_revision_bound'

log "Activation evidence"
printf 'source_commit=%s\n' "$RESOLVED_COMMIT"
printf 'assistant_lab=%s\n' "$(systemctl is-active assistant-lab.service)"
printf 'universal_video_enabled=%s\n' "$(systemctl is-enabled universal-video.service 2>/dev/null || true)"
printf 'universal_video_active=%s\n' "$(systemctl is-active universal-video.service 2>/dev/null || true)"
echo 'universal_video_operator=installed'
drive_file="$BASE_DIR/secrets/google-drive-oauth.json"
if [[ -f "$drive_file" ]] && DRIVE_OAUTH_FILE="$drive_file" python3 - <<'PY'
import json, os
from pathlib import Path
try:
    x=json.loads(Path(os.environ['DRIVE_OAUTH_FILE']).read_text(encoding='utf-8'))
    ok=isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k].strip() for k in ('client_id','client_secret','refresh_token'))
except Exception:
    ok=False
raise SystemExit(0 if ok else 1)
PY
then
  echo 'universal_video_drive_auth=CONFIGURED'
else
  echo 'universal_video_drive_auth=NOT_CONFIGURED_LOCAL_PATH_ONLY'
fi
ffmpeg -version | head -1
runuser -u universal-video -- "$BASE_DIR/.venv/bin/python" --version
runuser -u universal-video -- env HF_HOME="$BASE_DIR/model-cache" PYTHONDONTWRITEBYTECODE=1 "$BASE_DIR/.venv/bin/python" - <<'PY'
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

# Only a previously clean source tree is disposable. A dirty checkout remains
# preserved for operator inspection and is never silently deleted by rollout.
if [[ "$OLD_DIRTY" == "0" && -n "$OLD_DIR" && -e "$OLD_DIR" ]]; then
  rm -rf "$OLD_DIR"
  OLD_DIR=""
fi

if [[ "$RUN_SMOKE" == "1" ]]; then
  log "Run bounded synthetic Oracle-staged smoke job"
  smoke_stage="$BASE_DIR/media/drive-ready/universal-video-smoke"
  if [[ -e "$smoke_stage" || -L "$smoke_stage" ]]; then
    [[ -d "$smoke_stage" && ! -L "$smoke_stage" ]] || die "unsafe synthetic smoke staging path"
  fi
  install -d -o universal-video -g universal-video -m 0750 "$smoke_stage"
  media="$smoke_stage/source.mp4"
  job="$BASE_DIR/spool/inbox/universal-video-smoke.json"
  runuser -u universal-video -- rm -f -- \
    "$BASE_DIR/spool/done/universal-video-smoke.json" \
    "$BASE_DIR/spool/failed/universal-video-smoke.json" \
    "$job.tmp"
  runuser -u universal-video -- ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=640x360:rate=30:duration=5 \
    -f lavfi -i sine=frequency=440:duration=3 \
    -shortest -c:v mpeg4 -q:v 2 -pix_fmt yuv420p -c:a aac "$media"
  runuser -u universal-video -- env JOB_TMP="$job.tmp" JOB_PATH="$job" MEDIA_PATH="$media" /usr/bin/python3 - <<'PY'
import hashlib, json, os
path=os.environ['MEDIA_PATH']
size=os.path.getsize(path)
assert size >= 1024 * 1024, size
digest=hashlib.sha256()
with open(path,'rb') as handle:
    for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
        digest.update(block)
sha=digest.hexdigest()
payload={
    'job_id':'universal-video-smoke',
    'profile':'transcript_only',
    'project':'infrastructure-smoke',
    'source':{
        'kind':'oracle_drive_staged',
        'path':path,
        'file_id':'syntheticSmokeOracleDrive0001',
        'drive_name':'universal-video-smoke.mp4',
        'mime_type':'video/mp4',
        'size_bytes':size,
        'sha256':sha,
    },
    'metadata':{'synthetic':True},
    'options':{'max_duration_seconds':10,'chunk_seconds':60},
}
flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|getattr(os,'O_NOFOLLOW',0)
fd=os.open(os.environ['JOB_TMP'],flags,0o640)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        json.dump(payload,handle,separators=(',',':'))
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    os.link(os.environ['JOB_TMP'],os.environ['JOB_PATH'])
finally:
    try: os.unlink(os.environ['JOB_TMP'])
    except FileNotFoundError: pass
PY
  deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    if [[ -f "$BASE_DIR/spool/done/universal-video-smoke.json" ]]; then
      runuser -u universal-video -- /bin/cat "$BASE_DIR/spool/done/universal-video-smoke.json"
      echo UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS
      break
    fi
    if [[ -f "$BASE_DIR/spool/failed/universal-video-smoke.json" ]]; then
      runuser -u universal-video -- /bin/cat "$BASE_DIR/spool/failed/universal-video-smoke.json" >&2
      die "synthetic smoke job failed"
    fi
    sleep 2
  done
  [[ -f "$BASE_DIR/spool/done/universal-video-smoke.json" ]] || die "synthetic smoke job timed out"
fi

echo UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS
