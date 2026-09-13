#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact repair for the failed Diana 11 transcript job. It never prints OAuth
# contents, never changes Assistant Lab/DDS3, and refuses to restart Universal
# Video while any job is running.

readonly BASE='/opt/bridge-school/universal-video'
readonly SECRET_DIR="$BASE/secrets"
readonly SECRET_FILE="$SECRET_DIR/google-drive-oauth.json"
readonly ENV_FILE="$BASE/universal-video-secrets.env"
readonly FAILED="$BASE/spool/failed/diana11-transcript-20260825-01.json"
readonly ARCHIVE="$BASE/spool/failed/diana11-transcript-20260825-01.oauth-read-failure.json"
readonly OPERATOR='/usr/local/sbin/universal-video-diana11'

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
id universal-video >/dev/null 2>&1 || fail 'universal-video user missing'
[[ -f "$SECRET_FILE" && ! -L "$SECRET_FILE" ]] || fail 'OAuth secret file missing or unsafe'
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'OAuth env file missing or unsafe'
[[ -x "$OPERATOR" ]] || fail 'Diana 11 operator missing'

# Preserve protected boundary while repairing stale ownership/mode.
chown root:universal-video "$SECRET_DIR" "$SECRET_FILE" "$ENV_FILE"
chmod 0750 "$SECRET_DIR"
chmod 0640 "$SECRET_FILE" "$ENV_FILE"
printf 'GOOGLE_DRIVE_OAUTH_JSON_FILE=%s\n' "$SECRET_FILE" > "$ENV_FILE.tmp"
chown root:universal-video "$ENV_FILE.tmp"
chmod 0640 "$ENV_FILE.tmp"
mv -f "$ENV_FILE.tmp" "$ENV_FILE"

# Prove the runtime Unix identity can read and parse the file; never emit values.
runuser -u universal-video -- env DRIVE_OAUTH_FILE="$SECRET_FILE" python3 - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['DRIVE_OAUTH_FILE'])
x=json.loads(p.read_text(encoding='utf-8'))
assert isinstance(x, dict)
assert all(isinstance(x.get(k), str) and x[k].strip() for k in ('client_id','client_secret','refresh_token'))
print('DIANA11_OAUTH_RUNTIME_READ_PASS')
PY

# EnvironmentFile is read only at process start. The previous installer comment
# saying no restart was necessary was wrong. Restart only when the queue has no
# active job, so the service reloads the protected path.
if find "$BASE/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
  fail 'running Universal Video job exists; refusing restart'
fi
systemctl restart universal-video.service
sleep 2
systemctl is-active --quiet universal-video.service || fail 'Universal Video failed after restart'

pid="$(systemctl show universal-video.service -p MainPID --value)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]] || fail 'missing Universal Video MainPID'
if ! tr '\0' '\n' < "/proc/$pid/environ" | grep -Fx "GOOGLE_DRIVE_OAUTH_JSON_FILE=$SECRET_FILE" >/dev/null; then
  fail 'service did not reload GOOGLE_DRIVE_OAUTH_JSON_FILE'
fi
echo DIANA11_OAUTH_SERVICE_ENV_PASS

# Verify the failed artifact is exactly the known OAuth-read failure before
# archiving it and retrying. This prevents hiding unrelated failures.
[[ -f "$FAILED" && ! -L "$FAILED" ]] || fail 'expected failed Diana 11 artifact missing'
python3 - "$FAILED" <<'PY'
import json,sys
x=json.load(open(sys.argv[1],encoding='utf-8'))
assert x.get('error_type') == 'RuntimeError', x.get('error_type')
assert 'cannot read GOOGLE_DRIVE_OAUTH_JSON_FILE' in str(x.get('error') or ''), x.get('error')
print('DIANA11_EXPECTED_FAILURE_CONFIRMED')
PY
[[ ! -e "$ARCHIVE" ]] || fail 'OAuth failure archive already exists'
mv "$FAILED" "$ARCHIVE"
chmod 0640 "$ARCHIVE"
chown universal-video:universal-video "$ARCHIVE"

"$OPERATOR" submit

echo DIANA11_OAUTH_REPAIR_AND_RETRY_PASS
