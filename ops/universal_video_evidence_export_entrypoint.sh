#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Root-owned, fixed-path evidence export entrypoint for OCI Run Command.
# The unprivileged ocarun user may invoke only this exact command through
# sudoers and supplies one bounded JSON request on stdin. No path, command,
# service, job, or commit argument is accepted.

readonly EXPECTED_SOURCE_COMMIT='edbb4cae625323146fcab3ad4f80ed3d9a9abc90'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly REQUEST_DIR='/var/lib/bridge-school/universal-video'
readonly STATUS_DIR='/run/bridge-school'
readonly REQUEST_PATH="$REQUEST_DIR/evidence-export-request.json"
readonly STATUS_PATH="$STATUS_DIR/universal-video-status.json"
readonly MAX_REQUEST_BYTES=4096

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
[[ $# -eq 0 ]] || fail 'usage: universal-video-evidence-export'
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)" == "$EXPECTED_SOURCE_COMMIT" ]] \
  || fail 'exporter source pin mismatch'
systemctl is-active --quiet universal-video.service || fail 'universal-video.service is not active'
running="$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit)" \
  || fail 'running job guard unavailable'
[[ -z "$running" ]] || fail 'universal-video has a running job'

install -d -m 0750 -o root -g universal-video "$REQUEST_DIR" "$STATUS_DIR"
work="$(mktemp -d -p "$REQUEST_DIR" .evidence-export.XXXXXX)"
status_tmp="$(mktemp -p "$STATUS_DIR" .universal-video-status.XXXXXX)"
trap 'rm -rf "${work:-}"; rm -f "${status_tmp:-}"' EXIT INT TERM

request_tmp="$work/request.json"
dd if=/dev/stdin of="$request_tmp" bs=$((MAX_REQUEST_BYTES + 1)) count=1 status=none
request_size="$(stat -c '%s' "$request_tmp")"
[[ "$request_size" =~ ^[0-9]+$ ]] || fail 'request size unavailable'
(( request_size > 0 && request_size <= MAX_REQUEST_BYTES )) || fail 'request exceeds byte cap'
python3 - "$request_tmp" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(value, dict):
    raise SystemExit("request must be an object")
PY
chown root:universal-video "$request_tmp"
chmod 0640 "$request_tmp"
mv -f "$request_tmp" "$REQUEST_PATH"

python3 - "$status_tmp" <<'PY'
import json, os, sys, time

path = sys.argv[1]
payload = {
    "schema": "universal-video-resident-status-v1",
    "instance_state": "RUNNING",
    "active_jobs": [],
    "observed_at_unix": time.time(),
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
chown root:universal-video "$status_tmp"
chmod 0640 "$status_tmp"
mv -f "$status_tmp" "$STATUS_PATH"
status_tmp=''

running="$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit)" \
  || fail 'final running job guard unavailable'
[[ -z "$running" ]] || fail 'universal-video acquired a running job'

runuser -u universal-video -- env \
  PYTHONPATH="$SOURCE_DIR" \
  PYTHONDONTWRITEBYTECODE=1 \
  "$BASE_DIR/.venv/bin/python" \
  "$SOURCE_DIR/ops/universal_video_resident_evidence_export.py"
