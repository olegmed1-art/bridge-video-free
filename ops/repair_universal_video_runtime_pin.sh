#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Bounded repair for UV-DIANA11-DURABLE-003.
# It may change only UNIVERSAL_VIDEO_SOURCE_COMMIT in the non-secret runtime env
# and restart only universal-video.service. It never enqueues or processes a job,
# writes Drive, touches Assistant Lab/DDS3, or changes routing outside the sidecar.

stage='PRECHECK'
mark(){ stage="$1"; printf 'UV003_RUNTIME_PIN_STAGE=%s\n' "$stage"; }
fail(){ printf 'UV003_RUNTIME_PIN_STAGE=%s\n' "$stage" >&2; printf 'UV003_RUNTIME_PIN_REPAIR=FAILED_CLOSED\n' >&2; exit 1; }
mark PRECHECK
[[ "$(id -u)" -eq 0 ]] || fail
: "${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}"
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

mark DDS3_BEFORE
ready_before="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail
READY_JSON="$ready_before" python3 - <<'PY' || fail
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status')=='ready'
assert x.get('engine')=='DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver')=='ready'
PY

spool_empty(){
  local state
  for state in inbox running; do
    [[ -d "$BASE/spool/$state" && ! -L "$BASE/spool/$state" ]] || return 1
    if find "$BASE/spool/$state" -mindepth 1 -maxdepth 1 -type f -name '*.json' -print -quit | grep -q .; then
      return 1
    fi
  done
}
mark SPOOL_GUARD
spool_empty || fail
printf 'UV003_RUNTIME_PIN_SPOOL=EMPTY\n'

mark ENV_SHAPE
env_shape="$(ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os
from pathlib import Path
lines=Path(os.environ['ENV_FILE']).read_text(encoding='utf-8').splitlines()
n=sum(1 for line in lines if line.startswith('UNIVERSAL_VIDEO_SOURCE_COMMIT='))
print('ZERO' if n == 0 else 'ONE' if n == 1 else 'MULTIPLE')
PY
)" || fail
[[ "$env_shape" == ZERO || "$env_shape" == ONE || "$env_shape" == MULTIPLE ]] || fail

mark BACKUP
backup="$(mktemp -p "$BASE" .universal-video.env.uv003-backup.XXXXXX)"
cp --preserve=mode,ownership,timestamps "$ENV_FILE" "$backup"
changed=0
stopped=0
rollback(){
  local rc=$?
  set +e
  if (( rc != 0 )); then
    if (( changed == 1 )); then
      cp --preserve=mode,ownership,timestamps "$backup" "$ENV_FILE"
    fi
    if (( stopped == 1 )) && spool_empty; then
      systemctl start "$SERVICE" >/dev/null 2>&1 || true
    fi
    printf 'UV003_RUNTIME_PIN_STAGE=%s\n' "$stage" >&2
    printf 'UV003_RUNTIME_PIN_REPAIR=FAILED_CLOSED\n' >&2
  fi
  rm -f "$backup"
  return "$rc"
}
trap rollback EXIT

mark STOP_SIDECAR
systemctl stop "$SERVICE"
stopped=1
systemctl is-active --quiet "$SERVICE" && fail
spool_empty || fail

mark WRITE_PIN
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
changed=1

mark VERIFY_FILE_PIN
ENV_FILE="$ENV_FILE" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" python3 - <<'PY' || fail
import os
from pathlib import Path
vals=[]
for line in Path(os.environ['ENV_FILE']).read_text(encoding='utf-8').splitlines():
    if line.startswith('UNIVERSAL_VIDEO_SOURCE_COMMIT='):
        vals.append(line.split('=',1)[1].strip())
assert vals == [os.environ['EXPECTED_RUNTIME_COMMIT']]
PY
spool_empty || fail

mark START_SIDECAR
systemctl start "$SERVICE"
stopped=0
sleep 2
[[ "$(systemctl is-active "$SERVICE")" == active ]] || fail

mark VERIFY_LIVE_PIN
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
spool_empty || fail

mark DDS3_AFTER
ready_after="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail
READY_JSON="$ready_after" python3 - <<'PY' || fail
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status')=='ready'
assert x.get('engine')=='DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver')=='ready'
PY
[[ "$(systemctl is-active assistant-lab.service)" == active ]] || fail

changed=0
trap - EXIT
rm -f "$backup"
printf 'UV003_RUNTIME_PIN_FILE=PINNED\n'
printf 'UV003_RUNTIME_PIN_LIVE=PINNED\n'
printf 'UV003_DDS3_NONREGRESSION=PASS\n'
printf 'UV003_RUNTIME_PIN_REPAIR=PASS\n'
