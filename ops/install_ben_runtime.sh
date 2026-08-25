#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotently install the reviewed BEN policy runtime on the existing Oracle
# host. The caller must provide an immutable GHCR digest through BEN_IMAGE.

[[ "$(id -u)" -eq 0 ]] || {
  echo "install_ben_runtime.sh requires root" >&2
  exit 20
}

BEN_IMAGE="${BEN_IMAGE:-}"
[[ "$BEN_IMAGE" =~ ^ghcr\.io/lorserker/ben@sha256:[0-9a-f]{64}$ ]] || {
  echo "BEN_IMAGE must be the reviewed immutable GHCR digest" >&2
  exit 21
}

command -v docker >/dev/null
command -v curl >/dev/null
systemctl is-active --quiet docker
docker pull "$BEN_IMAGE" >/tmp/bridge-ben-pull.log
docker image inspect "$BEN_IMAGE" >/dev/null

previous_unit="$(mktemp /tmp/bridge-ben.service.previous.XXXXXX)"
had_previous_unit=0
previous_digest=''
if [[ -f /etc/systemd/system/bridge-ben.service ]]; then
  cp /etc/systemd/system/bridge-ben.service "$previous_unit"
  had_previous_unit=1
fi
if [[ -s /opt/bridge-school/ben-runtime/image-digest ]]; then
  previous_digest="$(head -n 1 /opt/bridge-school/ben-runtime/image-digest)"
fi

rollback_required=1
rollback() {
  rc=$?
  trap - ERR
  echo "=== failed BEN rollout diagnostics (before rollback) ===" >&2
  systemctl cat bridge-ben.service --no-pager >&2 || true
  systemctl status bridge-ben.service --no-pager -l >&2 || true
  journalctl -u bridge-ben.service -n 200 --no-pager -o short-iso >&2 || true
  docker ps -a --filter 'name=^/bridge-ben$' --no-trunc >&2 || true
  docker inspect --format 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{json .State.Error}} mounts={{json .Mounts}}' bridge-ben >&2 || true
  echo "BEN rollout failed; restoring previous systemd service" >&2
  systemctl disable --now bridge-ben-healthcheck.timer >/dev/null 2>&1 || true
  if [[ "$had_previous_unit" == 1 ]]; then
    cp "$previous_unit" /etc/systemd/system/bridge-ben.service
    if [[ -n "$previous_digest" ]]; then
      printf '%s\n' "$previous_digest" > /opt/bridge-school/ben-runtime/image-digest
    fi
    systemctl daemon-reload
    systemctl restart bridge-ben.service || true
  else
    systemctl disable --now bridge-ben.service >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap rollback ERR

install -d -m 0755 /opt/bridge-school/ben-runtime
printf '%s\n' "$BEN_IMAGE" > /opt/bridge-school/ben-runtime/image-digest

cat >/etc/systemd/system/bridge-ben.service <<EOF
[Unit]
Description=Bridge BEN policy runtime
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
Restart=on-failure
RestartSec=3
ExecStartPre=-/usr/bin/docker rm -f bridge-ben
ExecStart=/usr/bin/docker run --rm --name bridge-ben --pull=never --read-only --cap-drop=ALL --security-opt=no-new-privileges:true --pids-limit=256 --memory=6g --memory-swap=6g --cpus=2.0 --shm-size=512m --tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m --tmpfs /logs:rw,nosuid,nodev,noexec,size=64m -p 127.0.0.1:8085:8085 $BEN_IMAGE
ExecStop=/usr/bin/docker stop -t 10 bridge-ben
TimeoutStartSec=240
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

cat >/usr/local/sbin/bridge-ben-healthcheck <<'EOF'
#!/bin/sh
set -eu
url='http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true'
if /usr/bin/curl -fsS --max-time 15 "$url" >/dev/null; then
  exit 0
fi
/usr/bin/logger -t bridge-ben-healthcheck 'BEN policy probe failed; restarting bridge-ben.service'
/usr/bin/systemctl try-restart bridge-ben.service
exit 1
EOF
chmod 0755 /usr/local/sbin/bridge-ben-healthcheck

cat >/etc/systemd/system/bridge-ben-healthcheck.service <<'EOF'
[Unit]
Description=Probe and recover the Bridge BEN policy runtime
After=bridge-ben.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bridge-ben-healthcheck
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
EOF

cat >/etc/systemd/system/bridge-ben-healthcheck.timer <<'EOF'
[Unit]
Description=Periodic Bridge BEN policy runtime watchdog

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
RandomizedDelaySec=15s
Persistent=true
Unit=bridge-ben-healthcheck.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable bridge-ben.service
systemctl restart bridge-ben.service
for attempt in $(seq 1 120); do
  code="$(curl -sS -o /tmp/bridge-ben-ready.json -w '%{http_code}' --max-time 5 \
    'http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true' || true)"
  [[ "$code" == 200 ]] && break
  sleep 2
done
[[ "${code:-}" == 200 ]]
ss -ltn | grep -Eq '127\.0\.0\.1:8085[[:space:]]'
! ss -ltn | grep -Eq '(^|[[:space:]])0\.0\.0\.0:8085|\[::\]:8085'
systemctl enable --now bridge-ben-healthcheck.timer
systemctl start bridge-ben-healthcheck.service
systemctl is-active --quiet bridge-ben.service
systemctl is-active --quiet bridge-ben-healthcheck.timer

if systemctl cat assistant-lab.service >/dev/null 2>&1; then
  systemctl restart assistant-lab.service
  sleep 3
  systemctl is-active --quiet assistant-lab.service
fi

rollback_required=0
trap - ERR
rm -f "$previous_unit"
printf 'ORACLE_BEN_RUNTIME_ROLLOUT_PASS\nben_image=%s\n' "$BEN_IMAGE"
