#!/usr/bin/env bash
set -Eeuo pipefail

# Installs the existing Google Drive user-OAuth JSON into the Universal Video
# secret boundary without echoing the secret and without restarting any service.
# Usage:
#   sudo bash ops/oracle_universal_video_drive_secret_install.sh /secure/path/oauth.json
#   cat /secure/path/oauth.json | sudo bash ops/oracle_universal_video_drive_secret_install.sh -

USER_NAME="${UNIVERSAL_VIDEO_UNIX_USER:-universal-video}"
GROUP_NAME="${UNIVERSAL_VIDEO_UNIX_GROUP:-universal-video}"
BASE_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
SECRETS_DIR="${UNIVERSAL_VIDEO_SECRETS_DIR:-$BASE_DIR/secrets}"
SECRETS_ENV_FILE="${UNIVERSAL_VIDEO_SECRETS_ENV_FILE:-$BASE_DIR/universal-video-secrets.env}"
DRIVE_OAUTH_FILE="${UNIVERSAL_VIDEO_DRIVE_OAUTH_FILE:-$SECRETS_DIR/google-drive-oauth.json}"
SOURCE="${1:--}"

log(){ printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup(){ [[ -n "${TMP_FILE:-}" ]] && rm -f "$TMP_FILE"; }
trap cleanup EXIT

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
id "$USER_NAME" >/dev/null 2>&1 || die "$USER_NAME does not exist; install Universal Video runtime first"
getent group "$GROUP_NAME" >/dev/null 2>&1 || die "$GROUP_NAME does not exist"
install -d -m 0750 -o root -g "$GROUP_NAME" "$SECRETS_DIR"

umask 077
TMP_FILE="$(mktemp "$SECRETS_DIR/.google-drive-oauth.XXXXXX")"
if [[ "$SOURCE" == "-" ]]; then
  cat >"$TMP_FILE"
else
  [[ -f "$SOURCE" ]] || die "OAuth source file does not exist"
  cat -- "$SOURCE" >"$TMP_FILE"
fi
[[ -s "$TMP_FILE" ]] || die "OAuth JSON is empty"

DRIVE_SECRET_TMP="$TMP_FILE" python3 - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['DRIVE_SECRET_TMP'])
try:
    data=json.loads(p.read_text(encoding='utf-8'))
except Exception as exc:
    raise SystemExit('invalid OAuth JSON') from exc
if not isinstance(data, dict):
    raise SystemExit('OAuth JSON must be an object')
missing=[k for k in ('client_id','client_secret','refresh_token') if not isinstance(data.get(k), str) or not data[k].strip()]
if missing:
    raise SystemExit('OAuth JSON missing required fields: ' + ','.join(missing))
print('UNIVERSAL_VIDEO_DRIVE_SECRET_VALIDATION_PASS')
PY

install -m 0640 -o root -g "$GROUP_NAME" "$TMP_FILE" "$DRIVE_OAUTH_FILE"
printf 'GOOGLE_DRIVE_OAUTH_JSON_FILE=%s\n' "$DRIVE_OAUTH_FILE" >"$SECRETS_ENV_FILE.tmp"
chown root:"$GROUP_NAME" "$SECRETS_ENV_FILE.tmp"
chmod 0640 "$SECRETS_ENV_FILE.tmp"
mv -f "$SECRETS_ENV_FILE.tmp" "$SECRETS_ENV_FILE"

# Verify that the runtime identity can read and parse the secret. Never refresh
# or display credentials in this installer.
runuser -u "$USER_NAME" -- env DRIVE_OAUTH_FILE="$DRIVE_OAUTH_FILE" python3 - <<'PY'
import json, os
from pathlib import Path
x=json.loads(Path(os.environ['DRIVE_OAUTH_FILE']).read_text(encoding='utf-8'))
assert all(isinstance(x.get(k), str) and x[k].strip() for k in ('client_id','client_secret','refresh_token'))
print('UNIVERSAL_VIDEO_DRIVE_RUNTIME_READ_PASS')
PY

# No restart is needed: universal-video.service always receives the stable
# GOOGLE_DRIVE_OAUTH_JSON_FILE pointer at startup and the adapter reads the file
# lazily only when a Google Drive source is actually processed.
log "Drive OAuth secret installed in protected file; no service restarted"
echo UNIVERSAL_VIDEO_DRIVE_SECRET_INSTALL_PASS
