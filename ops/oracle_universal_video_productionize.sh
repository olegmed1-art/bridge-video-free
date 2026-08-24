#!/usr/bin/env bash
set -Eeuo pipefail

# Apply #371 production I/O/resource gates to the already-proven Oracle sidecar.
# This script never starts ASR and never changes production routing.

USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"
GROUP_NAME="${UNIVERSAL_VIDEO_UNIX_GROUP:-universal-video}"
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SOURCE_DIR="${UNIVERSAL_VIDEO_SOURCE_DIR:-/opt/bridge-school/universal-video-src}"
SERVICE_NAME="${UNIVERSAL_VIDEO_SERVICE_NAME:-universal-video.service}"
MAINT_SERVICE="universal-video-maintenance.service"
MAINT_TIMER="universal-video-maintenance.timer"
SECRETS_ENV_FILE="${UNIVERSAL_VIDEO_SECRETS_ENV_FILE:-$BASE_DIR/universal-video-secrets.env}"
DRIVE_PROBE_FILE_ID="${UNIVERSAL_VIDEO_DRIVE_PROBE_FILE_ID:-}"
DRIVE_RESULTS_FOLDER_ID="${UNIVERSAL_VIDEO_DRIVE_RESULTS_FOLDER_ID:-}"
MAX_SOURCE_BYTES="${UNIVERSAL_VIDEO_MAX_SOURCE_BYTES:-17179869184}"
MAX_DURATION_SECONDS="${UNIVERSAL_VIDEO_MAX_DURATION_SECONDS:-43200}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
state(){ systemctl is-active "$1" 2>/dev/null || true; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ -d "$SOURCE_DIR/.git" ]] || die "isolated source checkout missing"
[[ -f "$SOURCE_DIR/universal_video/maintenance.py" ]] || die "maintenance module missing"
[[ -f "$SOURCE_DIR/universal_video/drive_preflight.py" ]] || die "Drive preflight module missing"
[[ -f "$SOURCE_DIR/universal_video/drive_results.py" ]] || die "Drive result router missing"
[[ -f "$SOURCE_DIR/deploy/oracle-universal-video/universal-video.service" ]] || die "sidecar unit missing"
[[ -f "$SOURCE_DIR/deploy/oracle-universal-video/$MAINT_SERVICE" ]] || die "maintenance unit missing"
[[ -f "$SOURCE_DIR/deploy/oracle-universal-video/$MAINT_TIMER" ]] || die "maintenance timer missing"
[[ -n "$DRIVE_PROBE_FILE_ID" ]] || die "UNIVERSAL_VIDEO_DRIVE_PROBE_FILE_ID is required for the no-ASR Drive source gate"
[[ -n "$DRIVE_RESULTS_FOLDER_ID" ]] || die "UNIVERSAL_VIDEO_DRIVE_RESULTS_FOLDER_ID is required for the result-routing gate"

if find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
  die "universal-video has a running job; refusing resource/retention rollout"
fi

BEFORE_ASSISTANT="$(state assistant-lab.service)"
BEFORE_OBSERVER="$(state assistant-lab-observer.service)"
BEFORE_CONTROL="$(state assistant-lab-control.service)"
BEFORE_CONTROL_BRIDGE="$(state assistant-lab-control-bridge.service)"
BEFORE_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)"

log "Install bounded sidecar and maintenance units"
install -m 0644 -o root -g root "$SOURCE_DIR/deploy/oracle-universal-video/universal-video.service" "/etc/systemd/system/$SERVICE_NAME"
install -m 0644 -o root -g root "$SOURCE_DIR/deploy/oracle-universal-video/$MAINT_SERVICE" "/etc/systemd/system/$MAINT_SERVICE"
install -m 0644 -o root -g root "$SOURCE_DIR/deploy/oracle-universal-video/$MAINT_TIMER" "/etc/systemd/system/$MAINT_TIMER"
systemctl daemon-reload
systemd-analyze verify "/etc/systemd/system/$SERVICE_NAME" "/etc/systemd/system/$MAINT_SERVICE" "/etc/systemd/system/$MAINT_TIMER" >/dev/null

log "Apply MemoryHigh/MemoryMax without touching protected services"
if systemctl is-active --quiet "$SERVICE_NAME"; then
  systemctl restart "$SERVICE_NAME"
else
  systemctl start "$SERVICE_NAME"
fi
systemctl is-active --quiet "$SERVICE_NAME" || die "universal-video failed to become active"
MEMORY_HIGH="$(systemctl show "$SERVICE_NAME" -p MemoryHigh --value)"
MEMORY_MAX="$(systemctl show "$SERVICE_NAME" -p MemoryMax --value)"
MEMORY_HIGH="$MEMORY_HIGH" MEMORY_MAX="$MEMORY_MAX" python3 - <<'PY'
import os
hi=int(os.environ['MEMORY_HIGH']); mx=int(os.environ['MEMORY_MAX'])
assert hi == 12 * 1024**3, (hi, mx)
assert mx == 16 * 1024**3, (hi, mx)
assert hi < mx
print('UNIVERSAL_VIDEO_MEMORY_LIMITS_PASS')
PY

log "Validate and enable bounded retention"
runuser -u "$USER_NAME" -- env PYTHONPATH="$SOURCE_DIR" \
  "$BASE_DIR/.venv/bin/python" -m universal_video.maintenance --base-dir "$BASE_DIR"
systemctl enable --now "$MAINT_TIMER" >/dev/null
systemctl is-active --quiet "$MAINT_TIMER" || die "maintenance timer is not active"
systemctl start "$MAINT_SERVICE"
systemctl is-failed --quiet "$MAINT_SERVICE" && die "maintenance service failed"
echo 'UNIVERSAL_VIDEO_RETENTION_PASS'

log "Validate protected file-backed Google Drive OAuth boundary"
[[ -f "$SECRETS_ENV_FILE" ]] || die "universal-video secrets environment file missing"
OAUTH_FILE="$(sed -n 's/^GOOGLE_DRIVE_OAUTH_JSON_FILE=//p' "$SECRETS_ENV_FILE" | head -n 1)"
[[ -n "$OAUTH_FILE" ]] || die "file-backed Google Drive OAuth path is not configured"
DRIVE_STATUS="$(runuser -u "$USER_NAME" -- env PYTHONPATH="$SOURCE_DIR" GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
  "$BASE_DIR/.venv/bin/python" -m universal_video.drive_preflight credential-status)"
[[ "$DRIVE_STATUS" == 'UNIVERSAL_VIDEO_DRIVE_OAUTH=CONFIGURED' ]] || die "Google Drive OAuth boundary is not configured"
echo "$DRIVE_STATUS"

log "Probe Google Drive result destination without uploading"
runuser -u "$USER_NAME" -- env PYTHONPATH="$SOURCE_DIR" GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
  "$BASE_DIR/.venv/bin/python" -m universal_video.drive_results probe-destination --folder-id "$DRIVE_RESULTS_FOLDER_ID"
echo 'UNIVERSAL_VIDEO_RESULT_ROUTING_PASS'

log "Exercise one Drive video metadata/download/checksum/ffprobe path; ASR remains off"
runuser -u "$USER_NAME" -- env PYTHONPATH="$SOURCE_DIR" GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
  "$BASE_DIR/.venv/bin/python" -m universal_video.drive_preflight source-probe \
  --file-id "$DRIVE_PROBE_FILE_ID" \
  --max-source-bytes "$MAX_SOURCE_BYTES" \
  --max-duration-seconds "$MAX_DURATION_SECONDS"
echo 'UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS'

log "Post-change protected-service non-regression"
[[ "$(state assistant-lab.service)" == "$BEFORE_ASSISTANT" ]] || die "assistant-lab state changed"
[[ "$(state assistant-lab-observer.service)" == "$BEFORE_OBSERVER" ]] || die "observer state changed"
[[ "$(state assistant-lab-control.service)" == "$BEFORE_CONTROL" ]] || die "control state changed"
[[ "$(state assistant-lab-control-bridge.service)" == "$BEFORE_CONTROL_BRIDGE" ]] || die "control bridge state changed"
AFTER_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)"
BEFORE_READY="$BEFORE_READY" AFTER_READY="$AFTER_READY" python3 - <<'PY'
import json, os
before=json.loads(os.environ['BEFORE_READY']); after=json.loads(os.environ['AFTER_READY'])
for label, value in [('before', before), ('after', after)]:
    assert value.get('status') == 'ready', (label, value)
    assert value.get('engine') == 'DDS3', (label, value)
    assert value.get('fallback_used') is False, (label, value)
print('UNIVERSAL_VIDEO_DDS3_NONREGRESSION_PASS')
PY

printf 'UNIVERSAL_VIDEO_PRODUCTIONIZE_PASS source_probe=%s result_folder_configured=1 asr_started=0\n' "$DRIVE_PROBE_FILE_ID"
