#!/usr/bin/env bash
set -euo pipefail

fail=0

cpus=$(getconf _NPROCESSORS_ONLN)
mem_kb=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
root_kb=$(df -Pk / | awk 'NR==2 {print $2}')

printf 'CPU online: %s\n' "$cpus"
printf 'RAM total: %.1f GiB\n' "$(awk -v k="$mem_kb" 'BEGIN {print k/1024/1024}')"
printf 'Root filesystem: %.1f GiB\n' "$(awk -v k="$root_kb" 'BEGIN {print k/1024/1024}')"

if [ "$cpus" -lt 16 ]; then
  echo 'FAIL: expected at least 16 online vCPUs for Cube XL' >&2
  fail=1
fi

if [ "$mem_kb" -lt 30000000 ]; then
  echo 'FAIL: expected approximately 32 GiB RAM for Cube XL' >&2
  fail=1
fi

if [ "$root_kb" -lt 800000000 ]; then
  echo 'FAIL: expected approximately 960 GB local NVMe; visible filesystem is below 800 GB' >&2
  fail=1
fi

command -v docker >/dev/null || { echo 'FAIL: docker not installed' >&2; fail=1; }
if command -v docker >/dev/null; then
  docker version --format 'Docker server: {{.Server.Version}}' || { echo 'FAIL: Docker daemon unavailable' >&2; fail=1; }
  docker compose version || { echo 'FAIL: Docker Compose plugin unavailable' >&2; fail=1; }
fi

for d in /srv/bridge/compose /srv/bridge/env /srv/bridge/models /srv/bridge/cache /srv/bridge/work /srv/bridge/logs; do
  if [ ! -d "$d" ]; then
    echo "FAIL: required directory missing: $d" >&2
    fail=1
  fi
done

if [ -e /srv/bridge/env/production.env ]; then
  mode=$(stat -c '%a' /srv/bridge/env/production.env)
  if [ "$mode" != "600" ]; then
    echo "FAIL: /srv/bridge/env/production.env must be mode 600, got $mode" >&2
    fail=1
  fi
else
  echo 'FAIL: production environment file is missing' >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo 'IONOS_HOST_PREFLIGHT_FAIL' >&2
  exit 1
fi

echo 'IONOS_HOST_PREFLIGHT_PASS'
