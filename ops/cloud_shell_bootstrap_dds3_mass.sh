#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded OCI Cloud Shell launcher for installing the staged DDS3 mass-validation
# service on the existing Frankfurt Oracle host. It never starts a mass stage.

readonly ORACLE_HOST='158.180.47.161'
readonly ORACLE_USER='ubuntu'
readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"
readonly EXPECTED_ED25519_FINGERPRINT='SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc'
readonly RUNTIME_COMMIT='54cecba85485f8c4cf3dcc91e592db36cdbd2226'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly PILOT_DRIVE_FILE_ID='1CVInlmO73-BvdIpJM1ZGvoUegiKTnjYU'
readonly PILOT_ZIP_SHA256='ef126c6842dda691b08325392b9d7fe5319acdba34b2db6b8981f03d56f8e130'
readonly PILOT_RAW_SHA256='8a21cf06ab7ac424ee0f245ccf274e6d6f4f7135fa9b8c4c0e52c595c0da5996'

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$#" -eq 1 ]] || die 'usage: bash cloud_shell_bootstrap_dds3_mass.sh probe|install|status'
readonly MODE="$1"
case "$MODE" in probe|install|status) ;; *) die "unsupported mode: $MODE" ;; esac

for c in ssh ssh-keygen ssh-keyscan stat; do command -v "$c" >/dev/null 2>&1 || die "$c is required"; done
[[ -f "$SSH_KEY_PATH" && ! -L "$SSH_KEY_PATH" ]] || die "private key missing or unsafe: $SSH_KEY_PATH"
key_mode="$(stat -c '%a' "$SSH_KEY_PATH")"
(( (8#$key_mode & 077) == 0 )) || die "private key must be mode 0600/0400-style, got $key_mode"
ssh-keygen -y -f "$SSH_KEY_PATH" >/dev/null 2>&1 || die 'private key cannot be parsed'

work_dir="$(mktemp -d -t dds3-mass-cloud-shell.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT INT TERM
known_hosts="$work_dir/known_hosts"
ssh-keyscan -T 10 -t ed25519 "$ORACLE_HOST" > "$known_hosts" 2>/dev/null || die 'could not collect Oracle host key'
actual_fingerprint="$(ssh-keygen -lf "$known_hosts" | awk 'NR==1 {print $2}')"
[[ "$actual_fingerprint" == "$EXPECTED_ED25519_FINGERPRINT" ]] || die "Oracle host fingerprint mismatch"

readonly -a SSH_OPTIONS=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o HostKeyAlgorithms=ssh-ed25519
  -o "UserKnownHostsFile=$known_hosts"
  -o ConnectTimeout=15
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

remote_status() {
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
sudo -n true
printf 'assistant_lab=%s\n' "$(sudo -n systemctl is-active assistant-lab.service 2>/dev/null || true)"
printf 'observer=%s\n' "$(sudo -n systemctl is-active assistant-lab-observer.service 2>/dev/null || true)"
printf 'control_bridge=%s\n' "$(sudo -n systemctl is-active assistant-lab-control-bridge.service 2>/dev/null || true)"
printf 'dds3_mass_unit=%s\n' "$(sudo -n systemctl show dds3-mass@10000.service -p LoadState --value 2>/dev/null || true)"
printf 'dds3_mass_10k=%s\n' "$(sudo -n systemctl is-active dds3-mass@10000.service 2>/dev/null || true)"
ready_json="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY_JSON="$ready_json" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
print('dds3=ready_real_no_fallback')
PY
REMOTE
}

case "$MODE" in
  probe)
    log 'Probe fixed Oracle host and protected services'
    remote_status
    echo DDS3_MASS_CLOUD_SHELL_PROBE_PASS
    ;;
  status)
    log 'Read DDS3 mass service status'
    remote_status
    echo "pilot_drive_file_id=$PILOT_DRIVE_FILE_ID"
    echo "pilot_zip_sha256=$PILOT_ZIP_SHA256"
    echo "pilot_raw_sha256=$PILOT_RAW_SHA256"
    echo DDS3_MASS_CLOUD_SHELL_STATUS_PASS
    ;;
  install)
    log 'Install bounded DDS3 mass-validation service; do not start 10k'
    ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
      "sudo -n env DDS3_MASS_BOOTSTRAP=1 DDS3_MASS_ACTIVATE=1 bash -s" <<REMOTE_INSTALL
set -Eeuo pipefail
repo=/opt/bridge-school/bridge-video-free
[[ -d "\$repo/.git" ]]
cd "\$repo"
git status --porcelain | grep -q . && { echo 'dirty repository; refusing install' >&2; exit 2; } || true
git fetch --quiet origin '$RUNTIME_COMMIT'
git checkout --quiet --detach '$RUNTIME_COMMIT'
bash ops/oracle_dds3_mass_install.sh
systemctl show dds3-mass@10000.service -p LoadState --value | grep -Fx loaded
systemctl is-active --quiet assistant-lab.service
curl -fsS --max-time 10 http://127.0.0.1:8080/readyz | python3 -c "import json,sys; x=json.load(sys.stdin); assert x.get('status')=='ready' and x.get('engine')=='DDS3' and x.get('fallback_used') is False"
! systemctl is-active --quiet dds3-mass@10000.service
printf 'DDS3_MASS_ORACLE_INSTALL_PASS auto_started=0\n'
REMOTE_INSTALL
    remote_status
    echo DDS3_MASS_CLOUD_SHELL_INSTALL_PASS
    ;;
esac
