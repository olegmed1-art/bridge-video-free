#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Read-only, fixed diagnostic for the Universal Video sidecar on the existing
# Frankfurt Oracle host. No service mutation, media submission, ASR or routing.
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly SERVICE='universal-video.service'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SAFE_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
readonly EXPECTED_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'

fail(){ printf 'UNIVERSAL_VIDEO_SIDECAR_DIAGNOSTIC_FAIL=%s\n' "$1" >&2; exit 1; }
[[ $# -eq 0 ]] || fail USAGE
[[ $(id -u) -eq 0 ]] || fail MUST_RUN_AS_ROOT

# Guard unrelated production surfaces before reading diagnostics.
for s in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
  [[ "$(systemctl is-active "$s" 2>/dev/null || true)" == active ]] || fail PROTECTED_SERVICE
 done
ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail DDS3
READY_JSON="$ready" python3 - <<'PY' >/dev/null || fail DDS3
import json, os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver') == 'ready'
PY
echo 'protected_services=active'
echo 'dds3=ready_real_no_fallback'

[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || fail RUNTIME_LAYOUT
source_head="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null)" || fail RUNTIME_LAYOUT
printf 'source_head=%s\n' "$source_head"
[[ "$source_head" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail RUNTIME_PIN
if [[ -n "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all 2>/dev/null)" ]]; then
  echo 'source_clean=no'
else
  echo 'source_clean=yes'
fi

# Only fixed service lifecycle fields are exposed.
systemctl show "$SERVICE" --no-pager \
  -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts \
  | sed -nE '/^(ActiveState|SubState|Result|ExecMainCode|ExecMainStatus|NRestarts)=/p'
printf 'service_enabled=%s\n' "$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"

# Fixed permission probes as the service identity. No paths are caller supplied.
probe(){
  local label="$1" testop="$2" path="$3"
  if runuser -u universal-video -- test "$testop" "$path"; then
    printf '%s=pass\n' "$label"
  else
    printf '%s=fail\n' "$label"
  fi
}
probe source_root_traverse -x "$SOURCE_DIR"
probe source_package_traverse -x "$SOURCE_DIR/universal_video"
probe source_worker_read -r "$SOURCE_DIR/universal_video/spool_worker.py"
probe runtime_root_traverse -x "$BASE_DIR"
probe venv_python_exec -x "$PYTHON"

# Emit fixed metadata only; no directory listing or file contents.
for p in "$SOURCE_DIR" "$SOURCE_DIR/universal_video" "$SOURCE_DIR/universal_video/spool_worker.py" "$BASE_DIR" "$BASE_DIR/.venv" "$PYTHON"; do
  if [[ -e "$p" ]]; then
    case "$p" in
      "$SOURCE_DIR") label=source_root ;;
      "$SOURCE_DIR/universal_video") label=source_package ;;
      "$SOURCE_DIR/universal_video/spool_worker.py") label=source_worker ;;
      "$BASE_DIR") label=runtime_root ;;
      "$BASE_DIR/.venv") label=venv_root ;;
      "$PYTHON") label=venv_python ;;
      *) label=unknown ;;
    esac
    printf '%s_meta=%s\n' "$label" "$(stat -c '%U:%G:%a:%F' "$p" | tr ' ' '_')"
  fi
done

# Reproduce only the startup import as the service user, with bytecode writes
# disabled. Raw stderr stays local and is reduced to a bounded error class.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT INT TERM
set +e
runuser -u universal-video -- /usr/bin/env -i \
  PATH="$SAFE_PATH" HOME="$BASE_DIR" PYTHONPATH="$SOURCE_DIR" \
  HF_HOME="$BASE_DIR/model-cache" PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON" -c 'import universal_video.spool_worker' \
  >/dev/null 2>"$tmp"
import_rc=$?
set -e
printf 'service_user_import_rc=%s\n' "$import_rc"
if (( import_rc == 0 )); then
  echo 'service_user_import=pass'
else
  echo 'service_user_import=fail'
  DIAG_FILE="$tmp" SOURCE_DIR="$SOURCE_DIR" BASE_DIR="$BASE_DIR" python3 - <<'PY'
import os,re
from pathlib import Path
text=Path(os.environ['DIAG_FILE']).read_text(encoding='utf-8',errors='replace')[-12000:]
kind='UNKNOWN'
module=''
path_class='NONE'
if 'PermissionError' in text:
    kind='PERMISSION_ERROR'
elif 'ModuleNotFoundError' in text:
    kind='MODULE_NOT_FOUND'
    m=re.search(r"No module named ['\"]([A-Za-z0-9_.-]{1,120})['\"]", text)
    if m: module=m.group(1)
elif 'ImportError' in text:
    kind='IMPORT_ERROR'
elif 'FileNotFoundError' in text:
    kind='FILE_NOT_FOUND'
elif 'OSError' in text:
    kind='OS_ERROR'
source=os.environ['SOURCE_DIR']
runtime=os.environ['BASE_DIR']
if source in text:
    path_class='SOURCE'
elif runtime in text:
    path_class='RUNTIME'
print('import_failure_kind='+kind)
print('import_failure_path_class='+path_class)
if module:
    print('import_missing_module='+module)
PY
fi

echo UNIVERSAL_VIDEO_SIDECAR_DIAGNOSTIC_PASS
