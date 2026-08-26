#!/usr/bin/env bash
# Bounded resident control plane for Universal Video jobs.
# The only writable input is a base64-encoded JSON job contract; it is never
# interpreted as shell. All source identity and profile validation happens in
# universal_video.server_intake.
set -Eeuo pipefail
umask 077

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SPOOL="$BASE_DIR/spool"
readonly STAGING='/opt/bridge-school/.universal-video-staging'

fail(){ echo "UV_STATE=REJECTED"; echo "UV_ERROR=$1"; exit 1; }
safe_id(){ [[ "$1" =~ ^[A-Za-z0-9._:-]{1,160}$ && "$1" != . && "$1" != .. ]]; }
verify(){
  [[ -x "$PYTHON" && -d "$SOURCE_DIR/.git" ]] || fail 'universal video runtime missing'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" ]] || fail 'universal video spool missing'
  [[ -d "$STAGING" && ! -L "$STAGING" && "$(stat -c '%U:%G:%a' "$STAGING")" == root:root:700 ]] || fail 'unsafe staging directory'
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
}
submit(){
  [[ $# -eq 1 && ${#1} -le 350000 ]] || fail 'invalid encoded job'
  verify
  local tmp
  tmp="$(mktemp -p "$STAGING" request.XXXXXXXX.json)"
  trap 'rm -f "${tmp:-}"' EXIT
  printf '%s' "$1" | base64 --decode >"$tmp" 2>/dev/null || fail 'invalid job encoding'
  [[ $(stat -c '%s' "$tmp") -le 262144 ]] || fail 'job payload too large'
  chown root:root "$tmp"; chmod 0600 "$tmp"
  UNIVERSAL_VIDEO_STAGING_ROOT="$STAGING" PYTHONPATH="$SOURCE_DIR" "$PYTHON" -m universal_video.server_intake submit "$tmp" "$SPOOL"
  rm -f "$tmp"; trap - EXIT
}
status(){
  [[ $# -eq 1 ]] && safe_id "$1" || fail 'invalid job id'
  verify
  local id="$1" name="$1.json" found=() state receipt
  for state in inbox running done failed; do [[ -f "$SPOOL/$state/$name" && ! -L "$SPOOL/$state/$name" ]] && found+=("$state"); done
  [[ ${#found[@]} -le 1 ]] || { echo 'UV_STATE=CONFLICT'; exit 1; }
  [[ ${#found[@]} -eq 1 ]] || { echo 'UV_STATE=MISSING'; exit 0; }
  state="${found[0]}"
  case "$state" in
    inbox) echo 'UV_STATE=QUEUED' ;;
    running) echo 'UV_STATE=RUNNING' ;;
    failed) echo 'UV_STATE=FAILED' ;;
    done)
      receipt="$SPOOL/done/$name"
      STATUS="$(JOB="$receipt" python3 - <<'PY'
import json, os
x=json.load(open(os.environ['JOB'], encoding='utf-8'))
print(x.get('status',''))
PY
)"
      [[ "$STATUS" == COMPLETED ]] && echo 'UV_STATE=TECHNICAL_CONFORMANT' || echo 'UV_STATE=REVIEW'
      ;;
  esac
}
[[ $# -ge 1 ]] || fail 'usage: universal-video submit-base64 PAYLOAD | status JOB_ID'
case "$1" in
  submit-base64) shift; submit "$@" ;;
  status) shift; status "$@" ;;
  *) fail 'unsupported operation' ;;
esac
