#!/usr/bin/env bash
# Bounded Drive-only resident control plane for Universal Video jobs.
# The only writable input is a base64-encoded JSON job contract; it is never
# interpreted as shell. All source identity and profile validation happens in
# universal_video.server_intake.
set -Eeuo pipefail
umask 077

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly RECEIPT_READER="$SOURCE_DIR/ops/universal_video_receipt_reader.py"
readonly SPOOL="$BASE_DIR/spool"
readonly STAGING='/opt/bridge-school/.universal-video-staging'
readonly INTAKE="$BASE_DIR/intake"
readonly DRIVE_OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly QUEUE_DSN_FILE="$BASE_DIR/secrets/video-queue-dsn"

fail(){ echo "UV_STATE=REJECTED"; echo "UV_ERROR=$1"; exit 1; }
safe_id(){ [[ "$1" =~ ^[A-Za-z0-9._:-]{1,160}$ && "$1" != . && "$1" != .. ]]; }
verify(){
  [[ -x "$PYTHON" && -d "$SOURCE_DIR/.git" && -f "$RECEIPT_READER" && ! -L "$RECEIPT_READER" ]] || fail 'universal video runtime missing'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" && -d "$SPOOL/progress" ]] || fail 'universal video spool missing'
  [[ -d "$STAGING" && ! -L "$STAGING" && "$(stat -c '%U:%G:%a' "$STAGING")" == root:root:700 ]] || fail 'unsafe staging directory'
  systemctl is-active --quiet universal-video-container.service || fail 'universal-video-container.service inactive'
  systemctl is-active --quiet universal-video.service && fail 'legacy universal-video.service still active'
}
enqueue_batch(){
  [[ $# -eq 1 && ${#1} -le 24000 ]] || fail 'invalid encoded batch intake'
  verify
  [[ -d "$INTAKE" && ! -L "$INTAKE" && "$(stat -c '%U:%G:%a' "$INTAKE")" == universal-video:universal-video:750 ]] || fail 'unsafe intake directory'
  [[ -f "$DRIVE_OAUTH_FILE" && ! -L "$DRIVE_OAUTH_FILE" ]] || fail 'Drive credential unavailable'
  [[ -f "$QUEUE_DSN_FILE" && ! -L "$QUEUE_DSN_FILE" ]] || fail 'video queue credential unavailable'
  local root_tmp request_tmp
  root_tmp="$(mktemp -p "$STAGING" batch.XXXXXXXX.json)"
  request_tmp="$(runuser -u universal-video -- mktemp -p "$INTAKE" batch.XXXXXXXX.json)"
  trap 'rm -f "${root_tmp:-}" "${request_tmp:-}"' EXIT
  printf '%s' "$1" | base64 --decode >"$root_tmp" 2>/dev/null || fail 'invalid batch encoding'
  [[ $(stat -c '%s' "$root_tmp") -le 16384 ]] || fail 'batch intake too large'
  install -o universal-video -g universal-video -m 0600 "$root_tmp" "$request_tmp"
  runuser -u universal-video -- env \
    PYTHONPATH="$SOURCE_DIR" \
    GOOGLE_DRIVE_OAUTH_JSON_FILE="$DRIVE_OAUTH_FILE" \
    BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE="$QUEUE_DSN_FILE" \
    "$PYTHON" -m universal_video.video_queue_intake enqueue "$request_tmp"
  rm -f "$root_tmp" "$request_tmp"; trap - EXIT
}
batch_status(){
  [[ $# -eq 1 ]] && safe_id "$1" || fail 'invalid request key'
  verify
  [[ -f "$QUEUE_DSN_FILE" && ! -L "$QUEUE_DSN_FILE" ]] || fail 'video queue credential unavailable'
  runuser -u universal-video -- env \
    PYTHONPATH="$SOURCE_DIR" \
    BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE="$QUEUE_DSN_FILE" \
    "$PYTHON" -m universal_video.video_queue_intake status "$1"
}
submit_drive(){
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
  local id="$1" name="$1.json" found=() state receipt summary
  for state in inbox running done failed; do [[ -f "$SPOOL/$state/$name" && ! -L "$SPOOL/$state/$name" ]] && found+=("$state"); done
  [[ ${#found[@]} -le 1 ]] || { echo 'UV_STATE=CONFLICT'; exit 1; }
  [[ ${#found[@]} -eq 1 ]] || { echo 'UV_STATE=MISSING'; exit 0; }
  state="${found[0]}"
  receipt="$SPOOL/$state/$name"
  case "$state" in
    inbox) echo 'UV_STATE=DOWNLOAD_QUEUED' ;;
    running)
      progress="$SPOOL/progress/$name"
      if [[ -f "$progress" && ! -L "$progress" ]]; then
        JOB="$progress" python3 - <<'PY'
import json, os
x=json.load(open(os.environ['JOB'], encoding='utf-8'))
state=str(x.get('state') or 'RUNNING')
print('UV_STATE='+state if state in {'DOWNLOADING_FROM_DRIVE','SOURCE_READY_ON_ORACLE','PROCESSING'} else 'UV_STATE=RUNNING')
PY
      else
        echo 'UV_STATE=RUNNING'
      fi
      ;;
    failed)
      if ! summary="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" inspect-failed "$receipt" "$name" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=UNSAFE_FAILED_RECEIPT'
        return 0
      fi
      echo 'UV_STATE=FAILED'
      printf '%s\n' "$summary"
      ;;
    done)
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
[[ $# -ge 1 ]] || fail 'usage: universal-video submit-drive-base64 PAYLOAD | status JOB_ID'
[[ $# -ge 1 ]] || fail 'usage: universal-video submit-drive-base64 PAYLOAD | status JOB_ID | enqueue-batch-base64 PAYLOAD | batch-status REQUEST_KEY'
case "$1" in
  submit-drive-base64) shift; submit_drive "$@" ;;
  status) shift; status "$@" ;;
  enqueue-batch-base64) shift; enqueue_batch "$@" ;;
  batch-status) shift; batch_status "$@" ;;
  *) fail 'unsupported operation' ;;
esac
