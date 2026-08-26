#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Bounded repair for UV-DIANA11-DURABLE-003.
# It may change only UNIVERSAL_VIDEO_SOURCE_COMMIT in the non-secret runtime env
# and restart only universal-video.service. It never enqueues or processes a job,
# writes Drive, touches Assistant Lab/DDS3, or changes routing outside the sidecar.

failure_stage='PRECHECK'
failure_reported=0

report_failure(){
  case "$failure_stage" in
    PRECHECK|READY_BEFORE|SPOOL_BEFORE|ENV_SHAPE|BACKUP|STOP_SERVICE|WRITE_ENV|VERIFY_FILE|SPOOL_AFTER_WRITE|START_SERVICE|VERIFY_LIVE|SPOOL_AFTER_START|READY_AFTER|ASSISTANT_AFTER|FINALIZE) ;;
    *) failure_stage='PRECHECK' ;;
  esac
  if (( failure_reported == 0 )); then
    printf 'UV003_RUNTIME_PIN_FAILURE_CODE=%s\n' "$failure_stage" >&2
    printf 'UV003_RUNTIME_PIN_REPAIR=FAILED_CLOSED\n' >&2
    failure_reported=1
  fi
}
fail(){
  trap - ERR
  report_failure
  exit 1
}
checkpoint(){
  local value="$1"
  case "$value" in
    PRECHECK_PASS|READY_BEFORE_PASS|SPOOL_BEFORE_PASS|ENV_SHAPE_ENTER|ENV_SHAPE_STAT_PASS|ENV_SHAPE_READ_PASS|ENV_SHAPE_UTF8_PASS|ENV_SHAPE_STRUCTURE_PASS|ENV_SHAPE_PASS|BACKUP_ENTER|BACKUP_PASS|ROLLBACK_ARMED|STOP_SERVICE_ENTER|STOP_SERVICE_PASS|WRITE_ENV_ENTER|WRITE_ENV_PASS|VERIFY_FILE_PASS|SPOOL_AFTER_WRITE_PASS|START_SERVICE_ENTER|START_SERVICE_PASS|VERIFY_LIVE_PASS|SPOOL_AFTER_START_PASS|READY_AFTER_PASS|ASSISTANT_AFTER_PASS|FINALIZE_ENTER) ;;
    *) failure_stage='PRECHECK'; fail ;;
  esac
  printf 'UV003_RUNTIME_PIN_CHECKPOINT=%s\n' "$value"
}
on_error(){
  local rc=$?
  trap - ERR
  report_failure
  exit "$rc"
}
trap on_error ERR

[[ "$(id -u)" -eq 0 ]] || fail
[[ -n "${EXPECTED_RUNTIME_COMMIT:-}" ]] || fail
[[ "$EXPECTED_RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail

readonly BASE='/opt/bridge-school/universal-video'
readonly SOURCE='/opt/bridge-school/universal-video-src'
readonly ENV_FILE="$BASE/universal-video.env"
readonly SERVICE='universal-video.service'

[[ -d "$SOURCE/.git" && ! -L "$SOURCE" ]] || fail
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail
[[ -z "$(git -C "$SOURCE" status --porcelain=v1 --untracked-files=all)" ]] || fail
[[ "$(systemctl is-active assistant-lab.service)" == active ]] || fail
[[ "$(systemctl is-active "$SERVICE")" == active ]] || fail
checkpoint PRECHECK_PASS

failure_stage='READY_BEFORE'
ready_before="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail
READY_JSON="$ready_before" python3 - <<'PY' || fail
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status')=='ready'
assert x.get('engine')=='DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver')=='ready'
PY
checkpoint READY_BEFORE_PASS

spool_empty(){
  local state
  for state in inbox running; do
    [[ -d "$BASE/spool/$state" && ! -L "$BASE/spool/$state" ]] || return 1
    if find "$BASE/spool/$state" -mindepth 1 -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
      return 1
    fi
  done
}

failure_stage='SPOOL_BEFORE'
spool_empty || fail
printf 'UV003_RUNTIME_PIN_SPOOL=EMPTY\n'
checkpoint SPOOL_BEFORE_PASS

# UV003_ENV_SHAPE_PROBE_PARITY_V3: use the same bounded, one-read validation
# path that independently classified the resident file as SOURCE_KEY_ONE.
# No environment line or value is emitted; only fixed progress markers appear.
failure_stage='ENV_SHAPE'
checkpoint ENV_SHAPE_ENTER
ENV_FILE="$ENV_FILE" python3 - <<'PY' || fail
import os
import stat
from pathlib import Path

path=Path(os.environ['ENV_FILE'])
maximum=1_048_576
assert not path.is_symlink()
info=path.stat()
assert stat.S_ISREG(info.st_mode)
assert info.st_size <= maximum
print('UV003_RUNTIME_PIN_CHECKPOINT=ENV_SHAPE_STAT_PASS', flush=True)
fd=os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
try:
    raw=os.read(fd, maximum + 1)
finally:
    os.close(fd)
assert len(raw) <= maximum
print('UV003_RUNTIME_PIN_CHECKPOINT=ENV_SHAPE_READ_PASS', flush=True)
assert raw and b'\x00' not in raw
text=raw.decode('utf-8', errors='strict')
assert not text.startswith('\ufeff')
print('UV003_RUNTIME_PIN_CHECKPOINT=ENV_SHAPE_UTF8_PASS', flush=True)
for line in text.splitlines():
    assert len(line) <= 16_384
    stripped=line.strip()
    if not stripped or stripped.startswith('#'):
        continue
    assert '=' in line
print('UV003_RUNTIME_PIN_CHECKPOINT=ENV_SHAPE_STRUCTURE_PASS', flush=True)
PY
checkpoint ENV_SHAPE_PASS

failure_stage='BACKUP'
checkpoint BACKUP_ENTER
backup="$(mktemp -p "$BASE" .universal-video.env.uv003-backup.XXXXXX)" || fail
cp --preserve=mode,ownership,timestamps "$ENV_FILE" "$backup" || fail
checkpoint BACKUP_PASS
changed=0
service_stopped=0
service_started_new=0
rollback(){
  local rc=$?
  trap - ERR
  set +e
  if (( rc != 0 )); then
    # If the new process was already started, stop it only while the spool is
    # still empty, then restore the old file and prior active service state.
    if (( service_started_new == 1 )) && spool_empty; then
      systemctl stop "$SERVICE" >/dev/null 2>&1 || true
      if ! systemctl is-active --quiet "$SERVICE"; then
        service_stopped=1
      fi
    fi
    if (( changed == 1 )); then
      cp --preserve=mode,ownership,timestamps "$backup" "$ENV_FILE"
    fi
    if (( service_stopped == 1 )) && spool_empty; then
      systemctl start "$SERVICE" >/dev/null 2>&1 || true
    fi
    report_failure
  fi
  rm -f "$backup"
  return "$rc"
}
trap rollback EXIT
checkpoint ROLLBACK_ARMED

failure_stage='STOP_SERVICE'
checkpoint STOP_SERVICE_ENTER
systemctl stop "$SERVICE" || fail
service_stopped=1
systemctl is-active --quiet "$SERVICE" && fail
spool_empty || fail
checkpoint STOP_SERVICE_PASS

# Canonicalize only the single source-revision setting: remove every existing
# instance of that key and append exactly one pinned value. All other lines are
# preserved byte-for-byte modulo the final newline.
failure_stage='WRITE_ENV'
checkpoint WRITE_ENV_ENTER
changed=1
ENV_FILE="$ENV_FILE" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" python3 - <<'PY' || fail
import os,tempfile
from pathlib import Path
p=Path(os.environ['ENV_FILE'])
expected=os.environ['EXPECTED_RUNTIME_COMMIT']
lines=p.read_text(encoding='utf-8').splitlines()
out=[line for line in lines if not line.startswith('UNIVERSAL_VIDEO_SOURCE_COMMIT=')]
out.append('UNIVERSAL_VIDEO_SOURCE_COMMIT='+expected)
fd,tmp=tempfile.mkstemp(prefix='.universal-video.env.uv003.', dir=str(p.parent), text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as h:
        h.write('\n'.join(out)+'\n'); h.flush(); os.fsync(h.fileno())
    os.chmod(tmp,0o644)
    os.chown(tmp,0,0)
    os.replace(tmp,p)
finally:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
PY
checkpoint WRITE_ENV_PASS

failure_stage='VERIFY_FILE'
ENV_FILE="$ENV_FILE" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" python3 - <<'PY' || fail
import os
from pathlib import Path
vals=[]
for line in Path(os.environ['ENV_FILE']).read_text(encoding='utf-8').splitlines():
    if line.startswith('UNIVERSAL_VIDEO_SOURCE_COMMIT='):
        vals.append(line.split('=',1)[1].strip())
assert vals == [os.environ['EXPECTED_RUNTIME_COMMIT']]
PY
checkpoint VERIFY_FILE_PASS

failure_stage='SPOOL_AFTER_WRITE'
spool_empty || fail
checkpoint SPOOL_AFTER_WRITE_PASS

failure_stage='START_SERVICE'
checkpoint START_SERVICE_ENTER
systemctl start "$SERVICE" || fail
service_stopped=0
service_started_new=1
sleep 2
[[ "$(systemctl is-active "$SERVICE")" == active ]] || fail
checkpoint START_SERVICE_PASS

failure_stage='VERIFY_LIVE'
pid="$(systemctl show "$SERVICE" -p MainPID --value)"
[[ "$pid" =~ ^[1-9][0-9]*$ ]] || fail
PID="$pid" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" python3 - <<'PY' || fail
import os
from pathlib import Path
raw=Path('/proc')/os.environ['PID']/'environ'
items=raw.read_bytes().split(b'\0')
vals=[]
for item in items:
    if item.startswith(b'UNIVERSAL_VIDEO_SOURCE_COMMIT='):
        vals.append(item.split(b'=',1)[1].decode('ascii'))
assert vals == [os.environ['EXPECTED_RUNTIME_COMMIT']]
PY
checkpoint VERIFY_LIVE_PASS

failure_stage='SPOOL_AFTER_START'
spool_empty || fail
checkpoint SPOOL_AFTER_START_PASS

failure_stage='READY_AFTER'
ready_after="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail
READY_JSON="$ready_after" python3 - <<'PY' || fail
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status')=='ready'
assert x.get('engine')=='DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver')=='ready'
PY
checkpoint READY_AFTER_PASS

failure_stage='ASSISTANT_AFTER'
[[ "$(systemctl is-active assistant-lab.service)" == active ]] || fail
checkpoint ASSISTANT_AFTER_PASS

failure_stage='FINALIZE'
checkpoint FINALIZE_ENTER
changed=0
service_started_new=0
trap - ERR
trap - EXIT
rm -f "$backup" || fail
printf 'UV003_RUNTIME_PIN_FILE=PINNED\n'
printf 'UV003_RUNTIME_PIN_LIVE=PINNED\n'
printf 'UV003_DDS3_NONREGRESSION=PASS\n'
printf 'UV003_RUNTIME_PIN_REPAIR=PASS\n'
