#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Root-owned, fixed-path evidence export entrypoint for OCI Run Command.
# The unprivileged ocarun user may invoke only this exact command through
# sudoers and supplies one bounded JSON request on stdin. No path, command,
# service, job, or commit argument is accepted.

readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly PIN_PATH='/etc/bridge-school/universal-video-evidence-export.commit'
readonly REQUEST_DIR='/var/lib/bridge-school/universal-video'
readonly STATUS_DIR='/run/bridge-school'
readonly REQUEST_PATH="$REQUEST_DIR/evidence-export-request.json"
readonly STATUS_PATH="$STATUS_DIR/universal-video-status.json"
readonly MAX_REQUEST_BYTES=4096

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
[[ $# -eq 0 ]] || fail 'usage: universal-video-evidence-export'
[[ -f "$PIN_PATH" && ! -L "$PIN_PATH" ]] || fail 'exporter source pin missing or unsafe'
[[ "$(stat -c '%U:%G:%a' "$PIN_PATH")" == 'root:root:444' ]] || fail 'exporter source pin ownership mismatch'
read -r expected_source_commit < "$PIN_PATH"
[[ "$expected_source_commit" =~ ^[0-9a-f]{40}$ ]] || fail 'exporter source pin invalid'
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)" == "$expected_source_commit" ]] \
  || fail 'exporter source pin mismatch'
systemctl is-active --quiet universal-video-container.service || fail 'universal-video-container.service is not active'
systemctl is-active --quiet universal-video.service && fail 'legacy universal-video.service is still active'
running="$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit)" \
  || fail 'running job guard unavailable'
[[ -z "$running" ]] || fail 'universal-video has a running job'

install -d -m 0750 -o root -g universal-video "$REQUEST_DIR"
[[ -d "$STATUS_DIR" && ! -L "$STATUS_DIR" ]] || fail 'resident status directory missing or unsafe'
[[ "$(stat -c '%U:%G:%a' "$STATUS_DIR")" == 'universal-video:universal-video:750' ]] \
  || fail 'resident status directory ownership mismatch'
work="$(mktemp -d -p "$REQUEST_DIR" .evidence-export.XXXXXX)"
trap 'rm -rf "${work:-}"' EXIT INT TERM

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

[[ -f "$STATUS_PATH" && ! -L "$STATUS_PATH" ]] || fail 'resident status missing or unsafe'
[[ "$(stat -c '%U:%G:%a' "$STATUS_PATH")" == 'universal-video:universal-video:640' ]] \
  || fail 'resident status ownership mismatch'
status_size="$(stat -c '%s' "$STATUS_PATH")"
[[ "$status_size" =~ ^[0-9]+$ ]] || fail 'resident status size unavailable'
(( status_size > 0 && status_size <= 16384 )) || fail 'resident status exceeds byte cap'

running="$(find "$BASE_DIR/spool/running" -maxdepth 1 -type f -name '*.json' -print -quit)" \
  || fail 'final running job guard unavailable'
[[ -z "$running" ]] || fail 'universal-video acquired a running job'

runuser -u universal-video -- env \
  PYTHONPATH="$SOURCE_DIR" \
  PYTHONDONTWRITEBYTECODE=1 \
  "$BASE_DIR/.venv/bin/python" \
  "$SOURCE_DIR/ops/universal_video_resident_evidence_export.py"
