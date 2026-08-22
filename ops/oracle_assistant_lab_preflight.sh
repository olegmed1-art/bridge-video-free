#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only preflight for Assistant Lab on the existing Oracle DDS3 VM.
# It makes no OCI, firewall, Docker, systemd, or database changes.

die(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
log(){ printf '\n== %s ==\n' "$*"; }

log "Host"
printf 'hostname=%s\n' "$(hostname)"
printf 'arch=%s\n' "$(uname -m)"
printf 'kernel=%s\n' "$(uname -r)"
printf 'cpus=%s\n' "$(nproc)"
awk '/MemTotal/ {printf "mem_total_kb=%s\n", $2}' /proc/meminfo

df -Pk / /opt 2>/dev/null || true

log "Runtime prerequisites"
command -v python3 >/dev/null || die "python3 missing"
command -v docker >/dev/null || die "docker missing"
python3 - <<'PY'
import sys
print("python=" + sys.version.split()[0])
if sys.version_info < (3, 11):
    raise SystemExit("Python >=3.11 required")
PY

log "Hot DDS3"
docker inspect bridge-school-dds3-runtime >/dev/null 2>&1 || die "bridge-school-dds3-runtime container missing"
ready="$(curl -fsS --max-time 5 http://127.0.0.1:8080/readyz)" || die "local DDS3 readyz failed"
printf '%s\n' "$ready"
READY_JSON="$ready" python3 - <<'PY'
import json, os
r=json.loads(os.environ["READY_JSON"])
assert r.get("status") == "ready", r
assert r.get("engine") == "DDS3", r
assert r.get("fallback_used") is False, r
print("dds3_provenance=PASS")
PY

log "Assistant Lab local state"
if systemctl list-unit-files assistant-lab.service >/dev/null 2>&1; then
  systemctl is-enabled assistant-lab.service 2>/dev/null || true
  systemctl is-active assistant-lab.service 2>/dev/null || true
else
  echo "assistant_lab_service=not_installed"
fi

if [[ -f /opt/bridge-school/assistant-lab/assistant-lab.env ]]; then
  mode="$(stat -c '%a' /opt/bridge-school/assistant-lab/assistant-lab.env)"
  printf 'assistant_lab_env=present mode=%s\n' "$mode"
  [[ "$mode" == "600" ]] || die "assistant-lab.env must be mode 600"
else
  echo "assistant_lab_env=not_present"
fi

log "Result"
echo "ASSISTANT_LAB_PREFLIGHT_PASS"
