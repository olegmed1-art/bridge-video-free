#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Read-only, bounded diagnostic for UV-DIANA11-DURABLE-003.
# It never submits a job, processes media, writes Drive, changes services, or
# prints arbitrary command output. Every failure is reduced to one allowlisted
# code suitable for public GitHub evidence.

readonly JOB_ID='diana11-shadow-20260826-001'
readonly RUNTIME_ENV='/opt/bridge-school/universal-video/universal-video.env'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly SPOOL_ROOT='/opt/bridge-school/universal-video/spool'
readonly TARGET='/usr/local/sbin/universal-video-diana11-shadow-preflight'
readonly SUDOERS='/etc/sudoers.d/universal-video-diana11-shadow-preflight-ocarun'
readonly EXPECTED_REVISION="${EXPECTED_RUNTIME_COMMIT:-}"

emit_fail(){
  trap - ERR
  printf 'UV003_DIAG_STATUS=FAIL\n'
  printf 'UV003_DIAG_CODE=%s\n' "$1"
  exit 0
}
unexpected(){ emit_fail UNEXPECTED_DIAGNOSTIC_FAILURE; }
trap unexpected ERR

[[ $(id -u) -eq 0 ]] || emit_fail NOT_ROOT
[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || emit_fail EXPECTED_REVISION_INVALID
printf 'UV003_DIAG_EXPECTED_REVISION=%s\n' "$EXPECTED_REVISION"

[[ -f "$RUNTIME_ENV" && ! -L "$RUNTIME_ENV" ]] || emit_fail RUNTIME_ENV_UNSAFE
[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || emit_fail SOURCE_CHECKOUT_UNSAFE
[[ -d "$SPOOL_ROOT" && ! -L "$SPOOL_ROOT" ]] || emit_fail SPOOL_ROOT_UNSAFE

actual_revision="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)"
[[ "$actual_revision" =~ ^[0-9a-f]{40}$ ]] || emit_fail RUNTIME_HEAD_INVALID
printf 'UV003_DIAG_ACTUAL_REVISION=%s\n' "$actual_revision"
[[ "$actual_revision" == "$EXPECTED_REVISION" ]] || emit_fail RUNTIME_HEAD_MISMATCH

checkout_state="$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all 2>/dev/null || printf '__GIT_STATUS_FAILED__')"
[[ "$checkout_state" != '__GIT_STATUS_FAILED__' ]] || emit_fail GIT_STATUS_UNREADABLE
[[ -z "$checkout_state" ]] || emit_fail RUNTIME_CHECKOUT_DIRTY
printf 'UV003_DIAG_RUNTIME_CHECKOUT=CLEAN\n'

parsed="$(RUNTIME_ENV="$RUNTIME_ENV" python3 - <<'PY' 2>/dev/null
import os
from pathlib import Path

values = {}
for raw in Path(os.environ['RUNTIME_ENV']).read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        raise SystemExit(2)
    key, value = line.split('=', 1)
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT', 'UNIVERSAL_VIDEO_WHISPER_MODEL', 'WHISPER_MODEL'}:
        values[key] = value.strip()
revision = values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT', '')
model = (
    values.get('UNIVERSAL_VIDEO_WHISPER_MODEL', '').strip()
    or values.get('WHISPER_MODEL', '').strip()
    or 'small'
)
print(revision)
print(model)
PY
)" || emit_fail RUNTIME_ENV_PARSE_FAILED

env_revision="$(printf '%s\n' "$parsed" | sed -n '1p')"
model="$(printf '%s\n' "$parsed" | sed -n '2p')"
[[ "$env_revision" =~ ^[0-9a-f]{40}$ ]] || emit_fail RUNTIME_ENV_REVISION_INVALID
printf 'UV003_DIAG_ENV_REVISION=%s\n' "$env_revision"
[[ "$env_revision" == "$EXPECTED_REVISION" ]] || emit_fail RUNTIME_ENV_REVISION_MISMATCH
[[ "$model" =~ ^[A-Za-z0-9._/-]{1,80}$ ]] || emit_fail RUNTIME_MODEL_INVALID
printf 'UV003_DIAG_MODEL=%s\n' "$model"

job_file="$JOB_ID.json"
for state in inbox running done failed; do
  candidate="$SPOOL_ROOT/$state/$job_file"
  [[ ! -e "$candidate" && ! -L "$candidate" ]] || emit_fail FRESH_ID_CONFLICT
 done
result_dir="$SPOOL_ROOT/results/$JOB_ID"
[[ ! -e "$result_dir" && ! -L "$result_dir" ]] || emit_fail FRESH_ID_CONFLICT
printf 'UV003_DIAG_FRESH_ID=ABSENT\n'

[[ -f "$TARGET" && ! -L "$TARGET" ]] || emit_fail PREFLIGHT_TARGET_UNSAFE
[[ "$(stat -c '%U:%G:%a' "$TARGET" 2>/dev/null || true)" == 'root:root:755' ]] || emit_fail PREFLIGHT_TARGET_UNSAFE
[[ -f "$SUDOERS" && ! -L "$SUDOERS" ]] || emit_fail PREFLIGHT_SUDOERS_UNSAFE
[[ "$(stat -c '%U:%G:%a' "$SUDOERS" 2>/dev/null || true)" == 'root:root:440' ]] || emit_fail PREFLIGHT_SUDOERS_UNSAFE
visudo -cf "$SUDOERS" >/dev/null 2>&1 || emit_fail PREFLIGHT_SUDOERS_INVALID
id ocarun >/dev/null 2>&1 || emit_fail OCARUN_IDENTITY_MISSING

validate_preflight_output(){
  local output="$1" line pass_count=0 revision_count=0 model_count=0 fingerprint_count=0
  while IFS= read -r line; do
    case "$line" in
      'UV003_EXPERIMENT_ID=UV-DIANA11-DURABLE-003'|'UV003_JOB_ID=diana11-shadow-20260826-001'|'UV003_JOB_HASH=a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d'|'UV003_PROFILE=bridge_lesson'|'UV003_SOURCE_FILE_ID=1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'|'UV003_SOURCE_SIZE_BYTES=740292560'|'UV003_DESTINATION_FOLDER_ID=1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'|'UV003_JOB_GUARD=ABSENT'|'UV003_AUTOMATIC_RETRIES=0'|'UV003_EXECUTION_AUTHORIZED=NO'|'UV003_PUBLICATION_AUTHORIZED=NO'|'UV003_PRODUCTION_PROMOTION=BLOCKED')
        ;;
      UV003_PROCESSING_REVISION=????????????????????????????????????????)
        [[ "${line#UV003_PROCESSING_REVISION=}" =~ ^[0-9a-f]{40}$ ]] || return 1
        revision_count=$((revision_count + 1)) ;;
      UV003_PROCESSING_WHISPER_MODEL=*)
        [[ "${line#UV003_PROCESSING_WHISPER_MODEL=}" =~ ^[A-Za-z0-9._/-]{1,80}$ ]] || return 1
        model_count=$((model_count + 1)) ;;
      UV003_PROCESSING_FINGERPRINT=????????????????????????????????????????????????????????????????)
        [[ "${line#UV003_PROCESSING_FINGERPRINT=}" =~ ^[0-9a-f]{64}$ ]] || return 1
        fingerprint_count=$((fingerprint_count + 1)) ;;
      'UV003_PREFLIGHT_PASS') pass_count=$((pass_count + 1)) ;;
      '') ;;
      *) return 1 ;;
    esac
  done <<< "$output"
  [[ $pass_count -eq 1 && $revision_count -eq 1 && $model_count -eq 1 && $fingerprint_count -eq 1 ]]
}

readonly MAX_PREFLIGHT_OUTPUT_BYTES=65536

run_preflight() {
  local output_file="$1"
  shift
  (
    ulimit -f 128
    timeout --signal=KILL 60 "$@"
  ) >"$output_file" 2>/dev/null || {
    rm -f "$output_file"
    return 1
  }
  local size
  size="$(stat -c '%s' "$output_file" 2>/dev/null || printf '0')"
  [[ "$size" =~ ^[0-9]+$ && "$size" -le "$MAX_PREFLIGHT_OUTPUT_BYTES" ]] || {
    rm -f "$output_file"
    return 1
  }
  cat "$output_file"
  rm -f "$output_file"
}

root_file="$(mktemp)"
run_preflight "$root_file" "$TARGET" || emit_fail PREFLIGHT_ROOT_COMMAND_FAILED
root_output="$(cat "$root_file")"
rm -f "$root_file"
validate_preflight_output "$root_output" || emit_fail PREFLIGHT_ROOT_OUTPUT_INVALID
printf 'UV003_DIAG_ROOT_COMMAND=PASS\n'

sudo_file="$(mktemp)"
run_preflight "$sudo_file" sudo -u ocarun sudo -n "$TARGET" || emit_fail PREFLIGHT_SUDO_COMMAND_FAILED
sudo_output="$(cat "$sudo_file")"
rm -f "$sudo_file"
validate_preflight_output "$sudo_output" || emit_fail PREFLIGHT_SUDO_OUTPUT_INVALID

extract_field() {
  local output="$1" key="$2"
  printf '%s\n' "$output" | awk -F= -v key="$key" '$1 == key {print $2}'
}
root_processing_revision="$(extract_field "$root_output" UV003_PROCESSING_REVISION)"
root_processing_model="$(extract_field "$root_output" UV003_PROCESSING_WHISPER_MODEL)"
root_processing_fingerprint="$(extract_field "$root_output" UV003_PROCESSING_FINGERPRINT)"
sudo_processing_revision="$(extract_field "$sudo_output" UV003_PROCESSING_REVISION)"
sudo_processing_model="$(extract_field "$sudo_output" UV003_PROCESSING_WHISPER_MODEL)"
sudo_processing_fingerprint="$(extract_field "$sudo_output" UV003_PROCESSING_FINGERPRINT)"

[[ "$root_processing_revision" == "$EXPECTED_REVISION" && "$root_processing_model" == "$model" ]] ||
  emit_fail PREFLIGHT_ROOT_OUTPUT_INVALID
[[ "$sudo_processing_revision" == "$EXPECTED_REVISION" && "$sudo_processing_model" == "$model" ]] ||
  emit_fail PREFLIGHT_SUDO_OUTPUT_INVALID
[[ "$root_processing_fingerprint" == "$sudo_processing_fingerprint" ]] ||
  emit_fail PREFLIGHT_SUDO_OUTPUT_INVALID
printf 'UV003_DIAG_SUDO_COMMAND=PASS\n'
printf 'UV003_DIAG_STATUS=PASS\n'
printf 'UV003_DIAG_CODE=NONE\n'
