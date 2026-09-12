#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME=remote-desktop-commander.service
SERVICE_USER=ubuntu
EXPECTED_HOSTNAME=bridge-school-dds3-frankfurt
PACKAGE_SPEC=@wonderwhy-er/desktop-commander@0.2.50
DEVICE_CONFIG=/home/ubuntu/.desktop-commander-device/device.json
RUNNER_PATH=/usr/local/libexec/remote-desktop-commander-agent
UNIT_PATH=/etc/systemd/system/${SERVICE_NAME}

fail() {
  printf 'RDC_AUTOSTART_ERROR=%s\n' "$1" >&2
  exit 1
}

[[ ${EUID} -eq 0 ]] || fail root_required
[[ $(hostname) == "$EXPECTED_HOSTNAME" ]] || fail unexpected_hostname
[[ ${EXPECTED_DEVICE_ID:-} =~ ^[0-9a-f-]{36}$ ]] || fail invalid_expected_device_id
id "$SERVICE_USER" >/dev/null 2>&1 || fail service_user_missing
[[ -f "$DEVICE_CONFIG" && ! -L "$DEVICE_CONFIG" ]] || fail persisted_session_missing
[[ $(stat -c '%U' "$DEVICE_CONFIG") == "$SERVICE_USER" ]] || fail persisted_session_owner

DEVICE_CONFIG="$DEVICE_CONFIG" EXPECTED_DEVICE_ID="$EXPECTED_DEVICE_ID" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["DEVICE_CONFIG"])
payload = json.loads(path.read_text(encoding="utf-8"))
session = payload.get("session")
assert payload.get("deviceId") == os.environ["EXPECTED_DEVICE_ID"]
assert isinstance(session, dict)
assert isinstance(session.get("access_token"), str) and session["access_token"]
assert isinstance(session.get("refresh_token"), str) and session["refresh_token"]
PY
chmod 0600 "$DEVICE_CONFIG"
printf 'RDC_PERSISTED_SESSION_PREFLIGHT=PASS\n'

npx_path=$(sudo -u "$SERVICE_USER" -H bash -lc 'command -v npx' 2>/dev/null || true)
if [[ -z "$npx_path" && -d /home/ubuntu/.nvm/versions/node ]]; then
  npx_path=$(find /home/ubuntu/.nvm/versions/node -mindepth 3 -maxdepth 3 \
    -path '*/bin/npx' -print | sort -V | tail -n 1)
fi
[[ -n "$npx_path" && -x "$npx_path" ]] || fail npx_missing
node_path="$(dirname "$npx_path")/node"
if [[ ! -x "$node_path" ]]; then
  node_path=$(sudo -u "$SERVICE_USER" -H bash -lc 'command -v node' 2>/dev/null || true)
fi
[[ -n "$node_path" && -x "$node_path" ]] || fail node_missing
node_major=$(sudo -u "$SERVICE_USER" -H "$node_path" -p 'Number(process.versions.node.split(".")[0])')
[[ "$node_major" =~ ^[0-9]+$ && "$node_major" -ge 18 ]] || fail node_too_old
npx_dir=$(dirname "$npx_path")
runtime_path="$npx_dir:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
printf 'RDC_NODE_PREFLIGHT=PASS\n'

sudo -u "$SERVICE_USER" -H env PATH="$runtime_path" timeout 180 \
  "$npx_path" --yes --prefer-offline \
  "$PACKAGE_SPEC" remote --help >/dev/null

install -d -m 0755 -o root -g root "$(dirname "$RUNNER_PATH")"
runner_tmp=$(mktemp "${RUNNER_PATH}.tmp.XXXXXXXX")
unit_tmp=$(mktemp "${UNIT_PATH}.tmp.XXXXXXXX")
# Invoked indirectly by the EXIT/signal trap below.
# shellcheck disable=SC2317
cleanup() {
  rm -f -- "$runner_tmp" "$unit_tmp"
}
trap cleanup EXIT HUP INT TERM

cat >"$runner_tmp" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
export HOME=/home/${SERVICE_USER}
export USER=${SERVICE_USER}
export LOGNAME=${SERVICE_USER}
export PATH='$runtime_path'
exec '$npx_path' --yes --prefer-offline '$PACKAGE_SPEC' remote --disable-no-sleep
EOF
install -m 0755 -o root -g root "$runner_tmp" "$RUNNER_PATH"

cat >"$unit_tmp" <<EOF
[Unit]
Description=Remote Desktop Commander device agent
Documentation=https://github.com/desktop-commander/remote-desktop-commander/blob/main/docs/SETUP.md
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=/home/${SERVICE_USER}
Environment=HOME=/home/${SERVICE_USER}
Environment=USER=${SERVICE_USER}
Environment=LOGNAME=${SERVICE_USER}
UMask=0077
ExecStart=${RUNNER_PATH}
Restart=always
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
install -m 0644 -o root -g root "$unit_tmp" "$UNIT_PATH"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

rdc_state=STARTING
for _ in $(seq 1 18); do
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    main_pid=$(systemctl show -p MainPID --value "$SERVICE_NAME")
    if [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] && [[ $(ps -o user= -p "$main_pid" | xargs) == "$SERVICE_USER" ]]; then
      invocation_id=$(systemctl show -p InvocationID --value "$SERVICE_NAME")
      service_log=$(journalctl --no-pager -o cat "_SYSTEMD_INVOCATION_ID=$invocation_id" 2>/dev/null || true)
      if grep -Fq 'Device ready:' <<<"$service_log"; then
        printf 'RDC_AUTOSTART_INSTALL=PASS\n'
        printf 'RDC_AGENT_READY=YES\n'
        printf 'RDC_SERVICE_ACTIVE=YES\n'
        printf 'RDC_SERVICE_ENABLED=%s\n' "$(systemctl is-enabled "$SERVICE_NAME")"
        printf 'RDC_SERVICE_USER=%s\n' "$SERVICE_USER"
        printf 'RDC_DEVICE_ID_MATCH=YES\n'
        exit 0
      fi
      if grep -Fq 'Persisted session invalid:' <<<"$service_log"; then
        rdc_state=PERSISTED_SESSION_INVALID
      elif grep -Fq 'Waiting for authorization' <<<"$service_log"; then
        rdc_state=AUTHORIZATION_REQUIRED
      elif grep -Fq 'Device startup failed:' <<<"$service_log"; then
        rdc_state=STARTUP_FAILED
      fi
    fi
  fi
  sleep 5
done

printf 'RDC_AGENT_STATE=%s\n' "$rdc_state" >&2
fail service_not_ready
