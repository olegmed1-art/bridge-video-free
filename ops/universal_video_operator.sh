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
cleanup_staged_files(){
  local path
  for path in "$@"; do
    [[ -n "$path" ]] || continue
    rm -f -- "$path" 2>/dev/null || return 1
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
  done
}
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
  local root_tmp='' request_tmp='' request_stage='' request_code='' copy_output='' copy_code='' copy_rc=0 raw_size='' cleanup_cmd='' queue_output='' queue_rc=0
  if ! stage_job_payload "$1" root_tmp raw_size; then
    return 1
  fi
  printf -v cleanup_cmd 'rm -f -- %q 2>/dev/null || true' "$root_tmp"
  trap "$cleanup_cmd" EXIT
  if [[ ! "$raw_size" =~ ^[0-9]+$ ]] || (( raw_size > 16384 )); then
    if ! cleanup_staged_files "$root_tmp"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
      return 1
    fi
    root_tmp=''
    trap - EXIT
    intake_reject 'UV_INTAKE_CONTRACT_INVALID'
    return 1
  fi
  set +e
  request_stage="$(runuser -u universal-video -- env UNIVERSAL_VIDEO_INTAKE_ROOT="$INTAKE" "$SYSTEM_PYTHON" -c '
import errno, os, tempfile
try:
    fd, path = tempfile.mkstemp(prefix="batch.", suffix=".json", dir=os.environ["UNIVERSAL_VIDEO_INTAKE_ROOT"])
    os.close(fd)
    print("UV_INTAKE_PATH=" + path)
except OSError as exc:
    code = {
        errno.EACCES: "UV_INTAKE_PERMISSION_DENIED",
        errno.EPERM: "UV_INTAKE_PERMISSION_DENIED",
        errno.ENOSPC: "UV_INTAKE_DISK_FULL",
        errno.EROFS: "UV_INTAKE_READ_ONLY",
        errno.EEXIST: "UV_INTAKE_COLLISION",
    }.get(exc.errno, "UV_INTAKE_IO_FAILED")
    print("UV_ERROR_CODE=" + code)
    raise SystemExit(1)
except BaseException:
    print("UV_ERROR_CODE=UV_INTAKE_EXECUTION_FAILED")
    raise SystemExit(1)
' 2>/dev/null)"
  request_stage_rc=$?
  set -e
  if (( request_stage_rc != 0 )); then
    request_code="$(sed -nE 's/^UV_ERROR_CODE=(UV_INTAKE_[A-Z0-9_]{1,96})$/\1/p' <<<"$request_stage" | tail -n1)"
    [[ -n "$request_code" ]] || request_code='UV_INTAKE_EXECUTION_FAILED'
    if ! cleanup_staged_files "$root_tmp"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
      return 1
    fi
    root_tmp=''
    trap - EXIT
    intake_reject "$request_code"
    return 1
  fi
  request_tmp="$(sed -n 's/^UV_INTAKE_PATH=//p' <<<"$request_stage" | tail -n1)"
  printf -v cleanup_cmd 'rm -f -- %q %q 2>/dev/null || true' "$root_tmp" "$request_tmp"
  trap "$cleanup_cmd" EXIT
  if [[ ! "$request_tmp" =~ ^/opt/bridge-school/universal-video/intake/batch\.[A-Za-z0-9._-]+\.json$ \
        || ! -f "$request_tmp" || -L "$request_tmp" ]]; then
    if ! cleanup_staged_files "$root_tmp" "$request_tmp"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
      return 1
    fi
    root_tmp=''
    request_tmp=''
    trap - EXIT
    intake_reject 'UV_INTAKE_EXECUTION_FAILED'
    return 1
  fi
  set +e
  copy_output="$(UNIVERSAL_VIDEO_COPY_SOURCE="$root_tmp" UNIVERSAL_VIDEO_COPY_TARGET="$request_tmp" "$SYSTEM_PYTHON" -c '
import errno, os, stat
source_fd = target_fd = None
try:
    source_fd = os.open(os.environ["UNIVERSAL_VIDEO_COPY_SOURCE"], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    target_fd = os.open(os.environ["UNIVERSAL_VIDEO_COPY_TARGET"], os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(source_fd).st_mode) or not stat.S_ISREG(os.fstat(target_fd).st_mode):
        raise OSError(errno.EPERM, "unsafe copy endpoint")
    while True:
        block = os.read(source_fd, 65536)
        if not block:
            break
        view = memoryview(block)
        while view:
            view = view[os.write(target_fd, view):]
    os.fsync(target_fd)
    os.fchmod(target_fd, 0o600)
    os.close(target_fd)
    target_fd = None
    print("UV_COPY_PASS")
except OSError as exc:
    code = {
        errno.EACCES: "UV_INTAKE_PERMISSION_DENIED",
        errno.EPERM: "UV_INTAKE_PERMISSION_DENIED",
        errno.ENOSPC: "UV_INTAKE_DISK_FULL",
        errno.EROFS: "UV_INTAKE_READ_ONLY",
    }.get(exc.errno, "UV_INTAKE_IO_FAILED")
    print("UV_ERROR_CODE=" + code)
    raise SystemExit(1)
except BaseException:
    print("UV_ERROR_CODE=UV_INTAKE_EXECUTION_FAILED")
    raise SystemExit(1)
finally:
    for descriptor in (source_fd, target_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
' 2>/dev/null)"
  copy_rc=$?
  set -e
  if (( copy_rc != 0 )); then
    copy_code="$(sed -nE 's/^UV_ERROR_CODE=(UV_INTAKE_[A-Z0-9_]{1,96})$/\1/p' <<<"$copy_output" | tail -n1)"
    [[ -n "$copy_code" ]] || copy_code='UV_INTAKE_EXECUTION_FAILED'
    if ! cleanup_staged_files "$root_tmp" "$request_tmp"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
      return 1
    fi
    root_tmp=''
    request_tmp=''
    trap - EXIT
    intake_reject "$copy_code"
    return 1
  fi
  set +e
  queue_output="$(runuser -u universal-video -- env \
    PYTHONPATH="$SOURCE_DIR" \
    GOOGLE_DRIVE_OAUTH_JSON_FILE="$DRIVE_OAUTH_FILE" \
    BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE="$QUEUE_DSN_FILE" \
    "$PYTHON" -m universal_video.video_queue_intake enqueue "$request_tmp" 2>&1)"
  queue_rc=$?
  set -e
  if ! cleanup_staged_files "$root_tmp" "$request_tmp"; then
    intake_reject 'UV_INTAKE_CLEANUP_FAILED'
    return 1
  fi
  root_tmp=''
  request_tmp=''
  trap - EXIT
  printf '%s\n' "$queue_output"
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
resume_batch(){
  [[ $# -eq 2 && ${#2} -le 24000 ]] || fail 'invalid resumable batch intake'
  safe_id "$1" || fail 'invalid request key'
  local status_output disposition
  if ! status_output="$(batch_status "$1")"; then
    printf '%s\n' "$status_output"
    return 1
  fi
  if ! disposition="$(STATUS_OUTPUT="$status_output" EXPECTED_REQUEST_KEY="$1" "$SYSTEM_PYTHON" - <<'PY'
import json
import os

value = json.loads(os.environ["STATUS_OUTPUT"].splitlines()[-1])
assert value.get("schema") == "universal-video-batch-status-v1"
assert value.get("request_key") == os.environ["EXPECTED_REQUEST_KEY"]
print("MISSING" if value.get("status") == "MISSING" else "ACCEPTED")
PY
  )"; then
    intake_reject 'UV_BATCH_INTAKE_FAILED'
    return 1
  fi
  if [[ "$disposition" == ACCEPTED ]]; then
    printf '%s\n' "$status_output"
    return 0
  fi
  [[ "$disposition" == MISSING ]] || { intake_reject 'UV_BATCH_INTAKE_FAILED'; return 1; }
  enqueue_batch "$2"
}
conformance_json(){
  local job_id="$1" profile="$2" job_hash="$3" source_file_id="$4" artifact_set_sha256="$5"
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$PYTHON" -m universal_video.result_conformance \
      --job-dir "$SPOOL/results/$job_id" \
      --expected-job-id "$job_id" \
      --expected-profile "$profile" \
      --expected-job-hash "$job_hash" \
      --expected-source-file-id "$source_file_id" \
      --expected-artifact-set-sha256 "$artifact_set_sha256" \
      --require-server-review \
      --evidence-phase POST_HOC_OBSERVATION
}
stage_job_payload(){
  [[ $# -eq 2 || $# -eq 3 ]] || return 2
  local encoded="$1" output_var="$2" size_var="${3:-}" stage_output stage_rc stage_code stage_path stage_size
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

def cleanup_staged(candidate):
    if not candidate:
        return True
    try:
        os.unlink(candidate)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        os.lstat(candidate)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False

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
    print("UV_STAGE_SIZE=" + str(len(raw)))
except (binascii.Error, ValueError):
    if not cleanup_staged(path):
        print("UV_ERROR_CODE=UV_INTAKE_CLEANUP_FAILED")
        raise SystemExit(1)
    print("UV_ERROR_CODE=UV_INTAKE_CONTRACT_INVALID")
    raise SystemExit(1)
except OSError as exc:
    if not cleanup_staged(path):
        print("UV_ERROR_CODE=UV_INTAKE_CLEANUP_FAILED")
        raise SystemExit(1)
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
    if not cleanup_staged(path):
        print("UV_ERROR_CODE=UV_INTAKE_CLEANUP_FAILED")
        raise SystemExit(1)
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
  stage_size="$(sed -nE 's/^UV_STAGE_SIZE=([0-9]{1,6})$/\1/p' <<<"$stage_output" | tail -n1)"
  if [[ -z "$stage_path" || -z "$stage_size" || ! -f "$stage_path" || -L "$stage_path" ]]; then
    if [[ -n "$stage_path" ]] && ! cleanup_staged_files "$stage_path"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
    else
      intake_reject 'UV_INTAKE_EXECUTION_FAILED'
    fi
    return 1
  fi
  printf -v "$output_var" '%s' "$stage_path"
  [[ -z "$size_var" ]] || printf -v "$size_var" '%s' "$stage_size"
}
submit_drive(){
  [[ $# -eq 1 && ${#1} -le 350000 ]] || { intake_reject 'UV_INTAKE_CONTRACT_INVALID'; return 1; }
  verify
  local tmp='' cleanup_cmd='' intake_output='' intake_code=''
  if ! stage_job_payload "$1" tmp; then
    return 1
  fi
  printf -v cleanup_cmd 'rm -f -- %q 2>/dev/null || true' "$tmp"
  trap "$cleanup_cmd" EXIT
  if ! intake_output="$(UNIVERSAL_VIDEO_STAGING_ROOT="$STAGING" PYTHONPATH="$SOURCE_DIR" "$SYSTEM_PYTHON" -m universal_video.server_intake submit "$tmp" "$SPOOL" 2>&1)"; then
    intake_code="$(sed -nE 's/^UV_ERROR_CODE=(UV_INTAKE_[A-Z0-9_]{1,96})$/\1/p' <<<"$intake_output" | tail -n1)"
    [[ -n "$intake_code" ]] || intake_code='UV_INTAKE_EXECUTION_FAILED'
    if ! cleanup_staged_files "$tmp"; then
      intake_reject 'UV_INTAKE_CLEANUP_FAILED'
      return 1
    fi
    tmp=''
    trap - EXIT
    intake_reject "$intake_code"
    return 1
  fi
  if ! cleanup_staged_files "$tmp"; then
    intake_reject 'UV_INTAKE_CLEANUP_FAILED'
    return 1
  fi
  tmp=''
  trap - EXIT
  printf '%s\n' "$intake_output"
}
status(){
  [[ $# -eq 4 ]] || fail 'status requires exact job identity'
  safe_id "$1" || fail 'invalid job id'
  [[ "$2" =~ ^[a-z0-9_]{1,80}$ ]] || fail 'invalid profile identity'
  [[ "$3" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid job hash identity'
  [[ "$4" =~ ^[A-Za-z0-9_-]{10,200}$ ]] || fail 'invalid Drive identity'
  verify
  local id="$1" profile="$2" job_hash="$3" source_file_id="$4"
  local name="$1.json" found=() state receipt summary report inventory expected_artifact_set_sha256
  for state in inbox running done failed; do [[ -f "$SPOOL/$state/$name" && ! -L "$SPOOL/$state/$name" ]] && found+=("$state"); done
  [[ ${#found[@]} -le 1 ]] || { echo 'UV_STATE=CONFLICT'; exit 1; }
  [[ ${#found[@]} -eq 1 ]] || { echo 'UV_STATE=MISSING'; exit 0; }
  state="${found[0]}"
  receipt="$SPOOL/$state/$name"
  case "$state" in
    inbox)
      if ! runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" \
        /usr/bin/python3 "$RECEIPT_READER" inspect-job "$receipt" \
          "$id" "$profile" "$job_hash" "$source_file_id" >/dev/null 2>&1; then
        if [[ ! -e "$receipt" && "${STATUS_TRANSITION_RETRY:-0}" -lt 3 ]]; then
          STATUS_TRANSITION_RETRY=$(( ${STATUS_TRANSITION_RETRY:-0} + 1 )) status "$id" "$profile" "$job_hash" "$source_file_id"
          return
        fi
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=PENDING_JOB_IDENTITY_MISMATCH'
        return 0
      fi
      echo 'UV_STATE=DOWNLOAD_QUEUED'
      ;;
    running)
      if ! runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" \
        /usr/bin/python3 "$RECEIPT_READER" inspect-job "$receipt" \
          "$id" "$profile" "$job_hash" "$source_file_id" >/dev/null 2>&1; then
        if [[ ! -e "$receipt" && "${STATUS_TRANSITION_RETRY:-0}" -lt 3 ]]; then
          STATUS_TRANSITION_RETRY=$(( ${STATUS_TRANSITION_RETRY:-0} + 1 )) status "$id" "$profile" "$job_hash" "$source_file_id"
          return
        fi
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=PENDING_JOB_IDENTITY_MISMATCH'
        return 0
      fi
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
      if ! summary="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" inspect-failed "$receipt" "$name" "$profile" "$job_hash" "$source_file_id" 2>/dev/null)"; then
        if [[ ! -e "$receipt" && "${STATUS_TRANSITION_RETRY:-0}" -lt 3 ]]; then
          STATUS_TRANSITION_RETRY=$(( ${STATUS_TRANSITION_RETRY:-0} + 1 )) status "$id" "$profile" "$job_hash" "$source_file_id"
          return
        fi
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=FAILED_RECEIPT_IDENTITY_MISMATCH'
        return 0
      fi
      echo 'UV_STATE=FAILED'
      printf '%s\n' "$summary"
      ;;
    done)
      if ! inventory="$(runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" \
        /usr/bin/python3 "$RECEIPT_READER" inspect-done-bound "$receipt" \
          "$id" "$profile" "$job_hash" "$source_file_id" 2>/dev/null)"; then
        if [[ ! -e "$receipt" && "${STATUS_TRANSITION_RETRY:-0}" -lt 3 ]]; then
          STATUS_TRANSITION_RETRY=$(( ${STATUS_TRANSITION_RETRY:-0} + 1 )) status "$id" "$profile" "$job_hash" "$source_file_id"
          return
        fi
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=DONE_RECEIPT_IDENTITY_MISMATCH'
        return 0
      fi
      summary="$(sed -n 's/^UV_RESULT_STATUS=//p' <<<"$inventory" | tail -n1)"
      expected_artifact_set_sha256="$(sed -n 's/^UV_EXPECTED_ARTIFACT_SET_SHA256=//p' <<<"$inventory" | tail -n1)"
      echo "UV_RESULT_STATUS=$summary"
      if [[ "$summary" == REVIEW ]]; then
        echo 'UV_STATE=REVIEW'
        return 0
      fi
      if [[ "$summary" != COMPLETED || ! "$expected_artifact_set_sha256" =~ ^[0-9a-f]{64}$ ]] \
        || ! report="$(conformance_json "$id" "$profile" "$job_hash" "$source_file_id" \
          "$expected_artifact_set_sha256" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=RESULT_CONFORMANCE_FAILED'
        return 0
      fi
      REPORT_JSON="$report" python3 - <<'PY'
import json, os
x=json.loads(os.environ['REPORT_JSON'])
assert x.get('state') == 'PASS'
print('UV_STATE=TECHNICAL_CONFORMANT')
print('UV_CONFORMANCE_STATE=PASS')
print('UV_ATTESTATION_MODE='+str(x.get('evidence_phase') or ''))
print('UV_ARTIFACT_SET_SHA256='+str(x.get('artifact_set_sha256') or ''))
print('UV_MANIFEST_SHA256='+str(x.get('manifest_sha256') or ''))
print('UV_ARTIFACT_COUNT='+str(x.get('artifact_count') or ''))
print('UV_TOTAL_BYTES='+str(x.get('total_bytes') or ''))
PY
      ;;
  esac
}
repair_submit_drive(){
  [[ $# -eq 1 ]] || fail 'invalid repair-submit request'
  local repair_out
  if ! repair_out="$(/usr/local/sbin/universal-video-spool-repair 2>/dev/null)"; then
    echo 'UV_STATE=REJECTED'
    echo 'UV_ERROR_CODE=UV_SPOOL_REPAIR_COMMAND_FAILED'
    return 1
  fi
  if ! grep -qx 'UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS' <<<"$repair_out"; then
    echo 'UV_STATE=REJECTED'
    echo 'UV_ERROR_CODE=UV_SPOOL_REPAIR_MARKER_MISSING'
    return 1
  fi
  submit_drive "$1"
}
[[ $# -ge 1 ]] || fail 'usage: universal-video submit-drive-base64 PAYLOAD | repair-submit-drive-base64 PAYLOAD | status JOB_ID PROFILE JOB_HASH DRIVE_FILE_ID | enqueue-batch-base64 PAYLOAD | batch-status REQUEST_KEY | resume-batch-base64 REQUEST_KEY PAYLOAD'
case "$1" in
  submit-drive-base64) shift; submit_drive "$@" ;;
  repair-submit-drive-base64) shift; repair_submit_drive "$@" ;;
  status) shift; status "$@" ;;
  enqueue-batch-base64) shift; enqueue_batch "$@" ;;
  batch-status) shift; batch_status "$@" ;;
  resume-batch-base64) shift; resume_batch "$@" ;;
  *) fail 'unsupported operation' ;;
esac
