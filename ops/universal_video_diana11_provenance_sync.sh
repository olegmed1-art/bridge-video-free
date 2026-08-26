#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Narrow preparation-only repair for UV-DIANA11-DURABLE-003.
# It may synchronize exactly one non-secret provenance key and restart only the
# isolated universal-video sidecar. It refuses queued/running work and never
# submits a job, processes media, writes Drive, or touches DDS3 routing.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly RUNTIME_ENV="$BASE_DIR/universal-video.env"
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly SPOOL_ROOT="$BASE_DIR/spool"
readonly SERVICE='universal-video.service'
readonly EXPECTED_REVISION="${EXPECTED_RUNTIME_COMMIT:-}"

backup=''
tmp=''
env_changed=0
service_stopped=0
completed=0

queue_empty(){
  local leaf
  for leaf in inbox running; do
    if find "$SPOOL_ROOT/$leaf" -mindepth 1 -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
      return 1
    fi
  done
  return 0
}

restore_state(){
  local ok=0
  set +e
  if (( env_changed == 1 )) && [[ -n "$backup" && -f "$backup" ]]; then
    cp --preserve=mode,ownership,timestamps "$backup" "$RUNTIME_ENV" || ok=1
  fi
  if (( service_stopped == 1 )) && ! systemctl is-active --quiet "$SERVICE"; then
    systemctl start "$SERVICE" >/dev/null 2>&1 || ok=1
  fi
  if (( service_stopped == 1 )); then
    systemctl is-active --quiet "$SERVICE" || ok=1
  fi
  return "$ok"
}

emit_fail(){
  local code="$1"
  trap - ERR EXIT
  if ! restore_state; then
    code='ROLLBACK_FAILED'
  fi
  rm -f "${tmp:-}" "${backup:-}" >/dev/null 2>&1 || true
  printf 'UV003_SYNC_STATUS=FAIL\n'
  printf 'UV003_SYNC_CODE=%s\n' "$code"
  exit 0
}
unexpected(){ emit_fail UNEXPECTED_SYNC_FAILURE; }
trap unexpected ERR
trap '(( completed == 1 )) || emit_fail UNEXPECTED_SYNC_FAILURE' EXIT

[[ $(id -u) -eq 0 ]] || emit_fail NOT_ROOT
[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || emit_fail EXPECTED_REVISION_INVALID
printf 'UV003_SYNC_EXPECTED_REVISION=%s\n' "$EXPECTED_REVISION"

[[ -f "$RUNTIME_ENV" && ! -L "$RUNTIME_ENV" ]] || emit_fail RUNTIME_ENV_UNSAFE
[[ "$(stat -c '%U:%G:%a' "$RUNTIME_ENV" 2>/dev/null || true)" == 'root:root:644' ]] || emit_fail RUNTIME_ENV_UNSAFE
[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || emit_fail SOURCE_CHECKOUT_UNSAFE
[[ -d "$SPOOL_ROOT/inbox" && ! -L "$SPOOL_ROOT/inbox" ]] || emit_fail SPOOL_ROOT_UNSAFE
[[ -d "$SPOOL_ROOT/running" && ! -L "$SPOOL_ROOT/running" ]] || emit_fail SPOOL_ROOT_UNSAFE
systemctl is-active --quiet "$SERVICE" || emit_fail SERVICE_NOT_ACTIVE
queue_empty || emit_fail JOB_QUEUE_NOT_EMPTY

actual_revision="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)"
[[ "$actual_revision" =~ ^[0-9a-f]{40}$ ]] || emit_fail SOURCE_HEAD_INVALID
printf 'UV003_SYNC_SOURCE_REVISION=%s\n' "$actual_revision"
[[ "$actual_revision" == "$EXPECTED_REVISION" ]] || emit_fail SOURCE_HEAD_MISMATCH

git -C "$SOURCE_DIR" diff --quiet --ignore-submodules -- || emit_fail SOURCE_TRACKED_DIRTY
git -C "$SOURCE_DIR" diff --cached --quiet --ignore-submodules -- || emit_fail SOURCE_TRACKED_DIRTY
checkout_state="$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all 2>/dev/null || printf '__GIT_STATUS_FAILED__')"
[[ "$checkout_state" != '__GIT_STATUS_FAILED__' ]] || emit_fail GIT_STATUS_UNREADABLE
[[ -z "$checkout_state" ]] || emit_fail SOURCE_UNTRACKED_DIRTY
printf 'UV003_SYNC_SOURCE_CHECKOUT=CLEAN\n'

parsed="$(RUNTIME_ENV="$RUNTIME_ENV" python3 - <<'PY' 2>/dev/null
import os
from pathlib import Path
values={}
for raw in Path(os.environ['RUNTIME_ENV']).read_text(encoding='utf-8').splitlines():
    line=raw.strip()
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        raise SystemExit(2)
    key,value=line.split('=',1)
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT','UNIVERSAL_VIDEO_WHISPER_MODEL','WHISPER_MODEL'}:
        if key in values:
            raise SystemExit(3)
        values[key]=value.strip()
revision=values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT','')
model=(values.get('UNIVERSAL_VIDEO_WHISPER_MODEL','').strip()
       or values.get('WHISPER_MODEL','').strip()
       or 'small')
print(revision)
print(model)
PY
)" || emit_fail RUNTIME_ENV_PARSE_FAILED

env_revision="$(printf '%s\n' "$parsed" | sed -n '1p')"
model="$(printf '%s\n' "$parsed" | sed -n '2p')"
[[ "$env_revision" =~ ^[0-9a-f]{40}$ ]] || emit_fail RUNTIME_ENV_REVISION_INVALID
[[ "$model" =~ ^[A-Za-z0-9._/-]{1,80}$ ]] || emit_fail RUNTIME_MODEL_INVALID
printf 'UV003_SYNC_PREVIOUS_REVISION=%s\n' "$env_revision"
printf 'UV003_SYNC_MODEL=%s\n' "$model"

main_pid="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || true)"
[[ "$main_pid" =~ ^[1-9][0-9]*$ && -r "/proc/$main_pid/environ" ]] || emit_fail SERVICE_PROCESS_UNSAFE
process_parsed="$(PID="$main_pid" python3 - <<'PY' 2>/dev/null
import os
p=f"/proc/{os.environ['PID']}/environ"
values={}
for item in open(p,'rb').read().split(b'\0'):
    if b'=' not in item:
        continue
    k,v=item.split(b'=',1)
    key=k.decode('utf-8','strict')
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT','UNIVERSAL_VIDEO_WHISPER_MODEL','WHISPER_MODEL'}:
        values[key]=v.decode('utf-8','strict').strip()
revision=values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT','')
model=(values.get('UNIVERSAL_VIDEO_WHISPER_MODEL','').strip()
       or values.get('WHISPER_MODEL','').strip()
       or 'small')
print(revision)
print(model)
PY
)" || emit_fail SERVICE_PROCESS_ENV_UNREADABLE
process_revision="$(printf '%s\n' "$process_parsed" | sed -n '1p')"
process_model="$(printf '%s\n' "$process_parsed" | sed -n '2p')"
[[ "$process_revision" =~ ^[0-9a-f]{40}$ ]] || emit_fail SERVICE_PROCESS_REVISION_INVALID
[[ "$process_model" =~ ^[A-Za-z0-9._/-]{1,80}$ ]] || emit_fail SERVICE_PROCESS_MODEL_INVALID
[[ "$process_revision" == "$env_revision" && "$process_model" == "$model" ]] || emit_fail SERVICE_PROCESS_ENV_MISMATCH
printf 'UV003_SYNC_PROCESS_PRECONDITION=PASS\n'

if [[ "$env_revision" == "$EXPECTED_REVISION" ]]; then
  printf 'UV003_SYNC_SERVICE_RESTARTED=NO\n'
  printf 'UV003_SYNC_STATUS=PASS\n'
  printf 'UV003_SYNC_CODE=ALREADY_ALIGNED\n'
  completed=1
  trap - EXIT ERR
  exit 0
fi

backup="$(mktemp /run/uv003-runtime-env-backup.XXXXXX)"
cp --preserve=mode,ownership,timestamps "$RUNTIME_ENV" "$backup"

systemctl stop "$SERVICE" >/dev/null 2>&1 || emit_fail SERVICE_STOP_FAILED
service_stopped=1
systemctl is-active --quiet "$SERVICE" && emit_fail SERVICE_STOP_FAILED
queue_empty || emit_fail JOB_QUEUE_CHANGED

tmp="$(mktemp "$BASE_DIR/.uv003-runtime-env.XXXXXX")"
RUNTIME_ENV="$RUNTIME_ENV" OUTPUT="$tmp" EXPECTED_REVISION="$EXPECTED_REVISION" python3 - <<'PY' 2>/dev/null || emit_fail ENV_REWRITE_FAILED
import os
from pathlib import Path
src=Path(os.environ['RUNTIME_ENV'])
out=Path(os.environ['OUTPUT'])
expected=os.environ['EXPECTED_REVISION']
lines=src.read_text(encoding='utf-8').splitlines()
indexes=[i for i,line in enumerate(lines) if line.startswith('UNIVERSAL_VIDEO_SOURCE_COMMIT=')]
if len(indexes)!=1:
    raise SystemExit(2)
lines[indexes[0]]=f'UNIVERSAL_VIDEO_SOURCE_COMMIT={expected}'
out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
PY
chown --reference="$RUNTIME_ENV" "$tmp"
chmod --reference="$RUNTIME_ENV" "$tmp"
[[ "$(stat -c '%U:%G:%a' "$tmp" 2>/dev/null || true)" == 'root:root:644' ]] || emit_fail ENV_REWRITE_FAILED
mv -f "$tmp" "$RUNTIME_ENV" || emit_fail ENV_REWRITE_FAILED
tmp=''
env_changed=1

new_revision="$(sed -n 's/^UNIVERSAL_VIDEO_SOURCE_COMMIT=//p' "$RUNTIME_ENV")"
[[ "$new_revision" == "$EXPECTED_REVISION" ]] || emit_fail ENV_REWRITE_FAILED

systemctl start "$SERVICE" >/dev/null 2>&1 || emit_fail SERVICE_START_FAILED
service_stopped=0
for _ in $(seq 1 20); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 1
done
systemctl is-active --quiet "$SERVICE" || emit_fail SERVICE_START_FAILED
sleep 2
queue_empty || emit_fail JOB_QUEUE_CHANGED

new_pid="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || true)"
[[ "$new_pid" =~ ^[1-9][0-9]*$ && -r "/proc/$new_pid/environ" ]] || emit_fail SERVICE_PROCESS_UNSAFE
new_process="$(PID="$new_pid" python3 - <<'PY' 2>/dev/null
import os
p=f"/proc/{os.environ['PID']}/environ"
values={}
for item in open(p,'rb').read().split(b'\0'):
    if b'=' not in item:
        continue
    k,v=item.split(b'=',1)
    key=k.decode('utf-8','strict')
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT','UNIVERSAL_VIDEO_WHISPER_MODEL','WHISPER_MODEL'}:
        values[key]=v.decode('utf-8','strict').strip()
revision=values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT','')
model=(values.get('UNIVERSAL_VIDEO_WHISPER_MODEL','').strip()
       or values.get('WHISPER_MODEL','').strip()
       or 'small')
print(revision)
print(model)
PY
)" || emit_fail SERVICE_PROCESS_ENV_UNREADABLE
new_process_revision="$(printf '%s\n' "$new_process" | sed -n '1p')"
new_process_model="$(printf '%s\n' "$new_process" | sed -n '2p')"
[[ "$new_process_revision" == "$EXPECTED_REVISION" ]] || emit_fail SERVICE_PROCESS_ENV_MISMATCH
[[ "$new_process_model" == "$model" ]] || emit_fail SERVICE_PROCESS_ENV_MISMATCH

rm -f "$backup"
backup=''
env_changed=0
printf 'UV003_SYNC_SERVICE_RESTARTED=YES\n'
printf 'UV003_SYNC_PROCESS_POSTCONDITION=PASS\n'
printf 'UV003_SYNC_STATUS=PASS\n'
printf 'UV003_SYNC_CODE=PROVENANCE_ALIGNED\n'
completed=1
trap - EXIT ERR
