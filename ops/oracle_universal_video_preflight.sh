#!/usr/bin/env bash
set -Eeuo pipefail

die(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
log(){ printf '\n== %s ==\n' "$*"; }

log "Protect current Oracle services"
systemctl is-active --quiet assistant-lab.service || die "assistant-lab.service is not active"
ready="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)" || die "local DDS3 readyz failed"
READY_JSON="$ready" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
print('dds3=PASS')
PY

log "Host capacity"
printf 'hostname=%s\n' "$(hostname)"
printf 'arch=%s\n' "$(uname -m)"
printf 'cpus=%s\n' "$(nproc)"
awk '/MemTotal/ {printf "mem_total_kb=%s\n", $2}' /proc/meminfo
df -Pk / /opt 2>/dev/null || true

log "Prerequisites"
command -v python3 >/dev/null || die "python3 missing"
python3 - <<'PY'
import sys
print('python=' + sys.version.split()[0])
assert sys.version_info >= (3, 11), sys.version
PY
command -v curl >/dev/null || die "curl missing"
command -v systemctl >/dev/null || die "systemd missing"

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ffmpeg -version | head -1
  ffprobe -version | head -1
else
  echo 'ffmpeg=not_installed'
fi

log "Existing sidecar state"
if systemctl list-unit-files universal-video.service >/dev/null 2>&1; then
  systemctl is-enabled universal-video.service 2>/dev/null || true
  systemctl is-active universal-video.service 2>/dev/null || true
else
  echo 'universal_video_service=not_installed'
fi

log "Result"
echo UNIVERSAL_VIDEO_PREFLIGHT_PASS
