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
readonly SYSTEM_PYTHON='/usr/bin/python3'
readonly RECEIPT_READER="$SOURCE_DIR/ops/universal_video_receipt_reader.py"
readonly SPOOL="$BASE_DIR/spool"
readonly STAGING='/opt/bridge-school/.universal-video-staging'
readonly INTAKE="$BASE_DIR/intake"
readonly DRIVE_OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly QUEUE_DSN_FILE="$BASE_DIR/secrets/video-queue-dsn"

fail(){ echo "UV_STATE=REJECTED"; echo "UV_ERROR=$1"; exit 1; }
intake_reject(){ echo 'UV_STATE=REJECTED'; echo "UV_ERROR_CODE=$1"; }
safe_id(){ [[ "$1" =~ ^[A-Za-z0-9._:-]{1,160}$ && "$1" != . && "$1" != .. ]]; }
verify(){
  [[ -x "$PYTHON" && -d "$SOURCE_DIR/.git" && -f "$RECEIPT_READER" && ! -L "$RECEIPT_READER" ]] || fail 'universal video runtime missing'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" && -d "$SPOOL/progress" ]] || fail 'universal video spool missing'
  systemctl is-active --quiet universal-video-container.service || fail 'universal-video-container.service inactive'
  systemctl is-active --quiet universal-video.service && fail 'legacy universal-video.service still active'
}
enqueue_batch(){
  [[ $# -eq 1 && ${#1} -le 24000 ]] || fail 'invalid encoded batch intake'
  verify
  [[ -d "$INTAKE" && ! -L "$INTAKE" && "$(stat -c '%U:%G:%a' "$INTAKE" 2>/dev/null)" == universal-video:universal-video:750 ]] || fail 'unsafe intake directory'
  [[ -f "$DRIVE_OAUTH_FILE" && ! -L "$DRIVE_OAUTH_FILE" ]] || fail 'Drive credential unavailable'
  [[ -f "$QUEUE_DSN_FILE" && ! -L "$QUEUE_DSN_FILE" ]] || fail 'video queue credential unavailable'
  local root_tmp='' request_tmp='' raw_size='' cleanup_cmd='' queue_rc=0
  if ! stage_job_payload "$1" root_tmp; then
    return 1
  fi
  printf -v cleanup_cmd 'rm -f -- %q' "$root_tmp"
  trap "$cleanup_cmd" EXIT
  if ! raw_size="$(stat -c '%s' -- "$root_tmp" 2>/dev/null)" \
     || [[ ! "$raw_size" =~ ^[0-9]+$ ]] \
     || (( raw_size > 16384 )); then
    rm -f -- "$root_tmp" 2>/dev/null || true
    trap - EXIT
    intake_reject 'UV_INTAKE_CONTRACT_INVALID'
    return 1
  fi
  if ! request_tmp="$(runuser -u universal-video -- mktemp -p "$INTAKE" batch.XXXXXXXX.json 2>/dev/null)"; then
    rm -f -- "$root_tmp" 2>/dev/null || true
    trap - EXIT
    intake_reject 'UV_INTAKE_IO_FAILED'
    return 1
  fi
  printf -v cleanup_cmd 'rm -f -- %q %q' "$root_tmp" "$request_tmp"
  trap "$cleanup_cmd" EXIT
  if [[ ! "$request_tmp" =~ ^/opt/bridge-school/universal-video/intake/batch\.[A-Za-z0-9._-]+\.json$ \
        || ! -f "$request_tmp" || -L "$request_tmp" ]]; then
    rm -f -- "$root_tmp" "$request_tmp" 2>/dev/null || true
    trap - EXIT
    intake_reject 'UV_INTAKE_EXECUTION_FAILED'
    return 1
  fi
  if ! install -o universal-video -g universal-video -m 0600 "$root_tmp" "$request_tmp" 2>/dev/null; then
    rm -f -- "$root_tmp" "$request_tmp" 2>/dev/null || true
    trap - EXIT
    intake_reject 'UV_INTAKE_IO_FAILED'
    return 1
  fi
  set +e
  runuser -u universal-video -- env \
    PYTHONPATH="$SOURCE_DIR" \
    GOOGLE_DRIVE_OAUTH_JSON_FILE="$DRIVE_OAUTH_FILE" \
    BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE="$QUEUE_DSN_FILE" \
    "$PYTHON" -m universal_video.video_queue_intake enqueue "$request_tmp"
  queue_rc=$?
  set -e
  rm -f -- "$root_tmp" "$request_tmp" 2>/dev/null || true
  trap - EXIT
  return "$queue_rc"
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
stage_job_payload(){
  [[ $# -eq 2 ]] || return 2
  local encoded="$1" output_var="$2" stage_output stage_rc stage_code stage_path
  set +e
  stage_output="$(printf '%s' "$encoded" | UNIVERSAL_VIDEO_STAGING_ROOT="$STAGING" "$SYSTEM_PYTHON" -c '
import base64
import binascii
import errno
import os
import stat
import sys
import tempfile

root = os.environ["UNIVERSAL_VIDEO_STAGING_ROOT"]
path = None
try:
    root_stat = os.lstat(root)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or root_stat.st_uid != 0
        or root_stat.st_gid != 0
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        print("UV_ERROR_CODE=UV_INTAKE_PERMISSION_DENIED")
        raise SystemExit(1)
    raw = base64.b64decode(sys.stdin.buffer.read(), validate=True)
    if len(raw) > 262144:
        print("UV_ERROR_CODE=UV_INTAKE_CONTRACT_INVALID")
        raise SystemExit(1)
    fd, path = tempfile.mkstemp(prefix="request.", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    print("UV_STAGE_PATH=" + path)
except (binascii.Error, ValueError):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
    print("UV_ERROR_CODE=UV_INTAKE_CONTRACT_INVALID")
    raise SystemExit(1)
except OSError as exc:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
    code = {
        errno.EACCES: "UV_INTAKE_PERMISSION_DENIED",
        errno.EPERM: "UV_INTAKE_PERMISSION_DENIED",
        errno.EXDEV: "UV_INTAKE_CROSS_DEVICE",
        errno.ENOSPC: "UV_INTAKE_DISK_FULL",
        errno.EROFS: "UV_INTAKE_READ_ONLY",
        errno.EEXIST: "UV_INTAKE_COLLISION",
    }.get(exc.errno, "UV_INTAKE_IO_FAILED")
    print("UV_ERROR_CODE=" + code)
    raise SystemExit(1)
except SystemExit:
    raise
except BaseException:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
    print("UV_ERROR_CODE=UV_INTAKE_EXECUTION_FAILED")
    raise SystemExit(1)
')"
  stage_rc=$?
  set -e
  if (( stage_rc != 0 )); then
    stage_code="$(sed -nE 's/^UV_ERROR_CODE=(UV_INTAKE_[A-Z0-9_]{1,96})$/\1/p' <<<"$stage_output" | tail -n1)"
    [[ -n "$stage_code" ]] || stage_code='UV_INTAKE_EXECUTION_FAILED'
    intake_reject "$stage_code"
    return 1
  fi
  stage_path="$(sed -nE 's#^UV_STAGE_PATH=(/opt/bridge-school/\.universal-video-staging/request\.[A-Za-z0-9._-]+\.json)$#\1#p' <<<"$stage_output" | tail -n1)"
  if [[ -z "$stage_path" || ! -f "$stage_path" || -L "$stage_path" ]]; then
    [[ -z "$stage_path" ]] || rm -f -- "$stage_path" 2>/dev/null || true
    intake_reject 'UV_INTAKE_EXECUTION_FAILED'
    return 1
  fi
  printf -v "$output_var" '%s' "$stage_path"
}
submit_drive(){
  [[ $# -eq 1 && ${#1} -le 350000 ]] || { intake_reject 'UV_INTAKE_CONTRACT_INVALID'; return 1; }
  verify
  local tmp='' cleanup_cmd='' intake_output='' intake_code=''
  if ! stage_job_payload "$1" tmp; then
    return 1
  fi
  printf -v cleanup_cmd 'rm -f -- %q' "$tmp"
  trap "$cleanup_cmd" EXIT
  if intake_output="$(UNIVERSAL_VIDEO_STAGING_ROOT="$STAGING" PYTHONPATH="$SOURCE_DIR" "$SYSTEM_PYTHON" -m universal_video.server_intake submit "$tmp" "$SPOOL" 2>&1)"; then
    printf '%s\n' "$intake_output"
  else
    intake_code="$(sed -nE 's/^UV_ERROR_CODE=(UV_INTAKE_[A-Z0-9_]{1,96})$/\1/p' <<<"$intake_output" | tail -n1)"
    [[ -n "$intake_code" ]] || intake_code='UV_INTAKE_EXECUTION_FAILED'
    rm -f -- "$tmp" 2>/dev/null || true
    tmp=''
    trap - EXIT
    intake_reject "$intake_code"
    return 1
  fi
  rm -f -- "$tmp" 2>/dev/null || true
  tmp=''
  trap - EXIT
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
