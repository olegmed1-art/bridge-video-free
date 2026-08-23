#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded OCI Cloud Shell launcher for the existing Oracle Universal Video
# sidecar. It has no arbitrary host/user/command inputs and never processes a
# real school video. Modes: probe, status, activate, smoke.

readonly ORACLE_HOST='158.180.47.161'
readonly ORACLE_USER='ubuntu'
readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"
readonly EXPECTED_ED25519_FINGERPRINT='SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc'
readonly RUNTIME_COMMIT='59377de601c1586ae9914a51a340dc72ac2007ce'
readonly PAYLOAD_BLOB_SHA1='bbf4dc5779726fca415f641b90d017a802daaabf'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly PAYLOAD_PATH='ops/oracle_universal_video_run_command.sh'
readonly ISSUE_NUMBER='318'

log() { printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: bash cloud_shell_activate_universal_video.sh MODE

MODE must be exactly one of:
  probe     verify pinned host identity, SSH key, sudo, Assistant Lab and DDS3
  status    read sidecar/Assistant Lab/DDS3 state without changing the host
  activate  install and start the sidecar; no video job and no synthetic smoke
  smoke     repeat idempotent activation and run one bounded 3-second synthetic job

The host, user, SSH key path, source commit and remote payload are fixed.
No real video is submitted by this launcher.
USAGE
}

[[ "$#" -eq 1 ]] || { usage >&2; exit 64; }
readonly MODE="$1"
case "$MODE" in
  probe|status|activate|smoke) ;;
  *) usage >&2; die "unsupported mode: $MODE" ;;
esac

for command_name in curl git python3 ssh ssh-keygen ssh-keyscan stat; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required in OCI Cloud Shell"
done

[[ -f "$SSH_KEY_PATH" ]] || die "private key is missing: $SSH_KEY_PATH"
[[ ! -L "$SSH_KEY_PATH" ]] || die "refusing symlink private key: $SSH_KEY_PATH"
key_mode="$(stat -c '%a' "$SSH_KEY_PATH")"
[[ "$key_mode" =~ ^[0-7]{3,4}$ ]] || die "unexpected private-key mode: $key_mode"
(( (8#$key_mode & 077) == 0 )) || die "private key must not be accessible by group or others (mode=$key_mode)"
ssh-keygen -y -f "$SSH_KEY_PATH" >/dev/null 2>&1 || die "private key cannot be parsed"

work_dir="$(mktemp -d -t universal-video-cloud-shell.XXXXXX)"
cleanup() {
  if [[ -n "${work_dir:-}" && -d "$work_dir" ]]; then
    find "$work_dir" -type f -exec chmod u+w {} + 2>/dev/null || true
    rm -rf "$work_dir"
  fi
}
trap cleanup EXIT INT TERM

known_hosts="$work_dir/known_hosts"
: > "$known_hosts"
for attempt in 1 2 3; do
  ssh-keyscan -T 10 -t ed25519 "$ORACLE_HOST" > "$known_hosts.tmp" 2>/dev/null || true
  if [[ -s "$known_hosts.tmp" ]]; then
    sort -u "$known_hosts.tmp" > "$known_hosts"
    break
  fi
  sleep $((attempt * 2))
done
[[ -s "$known_hosts" ]] || die "could not collect the Oracle ED25519 host key"
actual_fingerprint="$(ssh-keygen -lf "$known_hosts" | awk 'NR==1 {print $2}')"
[[ "$actual_fingerprint" == "$EXPECTED_ED25519_FINGERPRINT" ]] \
  || die "Oracle SSH host fingerprint mismatch: expected $EXPECTED_ED25519_FINGERPRINT, got ${actual_fingerprint:-none}"
echo "oracle_host_fingerprint=$actual_fingerprint"

readonly -a SSH_OPTIONS=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$known_hosts"
  -o ConnectTimeout=15
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

remote_read_only_status() {
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'bash -s' <<'REMOTE_STATUS'
set -Eeuo pipefail
sudo -n true
assistant_state="$(sudo -n systemctl is-active assistant-lab.service)"
[[ "$assistant_state" == 'active' ]] || { echo "assistant_lab=$assistant_state"; exit 1; }
ready_json="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY_JSON="$ready_json" python3 - <<'PY'
import json, os
x = json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
assert x.get('position_solver') == 'ready', x
print('dds3_local=ready_real_no_fallback')
PY
sidecar_load="$(sudo -n systemctl show universal-video.service -p LoadState --value 2>/dev/null || true)"
sidecar_enabled="$(sudo -n systemctl is-enabled universal-video.service 2>/dev/null || true)"
sidecar_active="$(sudo -n systemctl is-active universal-video.service 2>/dev/null || true)"
[[ -n "$sidecar_load" ]] || sidecar_load='not-found'
[[ -n "$sidecar_enabled" ]] || sidecar_enabled='not-found'
[[ -n "$sidecar_active" ]] || sidecar_active='inactive'
printf 'assistant_lab=%s\n' "$assistant_state"
printf 'universal_video_load=%s\n' "$sidecar_load"
printf 'universal_video_enabled=%s\n' "$sidecar_enabled"
printf 'universal_video_active=%s\n' "$sidecar_active"
echo ORACLE_UNIVERSAL_VIDEO_READ_ONLY_STATUS_PASS
REMOTE_STATUS
}

external_dds3_check() {
  local ready_json
  ready_json="$(curl -fsS --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 20 \
    "https://$ORACLE_HOST/readyz")" || die "external DDS3 readiness endpoint failed"
  READY_JSON="$ready_json" python3 - <<'PY'
import json, os
x = json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
assert x.get('position_solver') == 'ready', x
print('ORACLE_DDS3_EXTERNAL_NONREGRESSION_PASS')
PY
}

probe_control_path() {
  log 'Probe the fixed SSH/sudo control path and protected services'
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'sudo -n true'
  remote_read_only_status
  echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_PROBE_PASS
}

download_pinned_payload() {
  local payload="$1"
  local url="https://raw.githubusercontent.com/$REPOSITORY/$RUNTIME_COMMIT/$PAYLOAD_PATH"
  curl -fsS --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 "$url" -o "$payload"
  [[ "$(git hash-object "$payload")" == "$PAYLOAD_BLOB_SHA1" ]] \
    || die "pinned activation payload blob mismatch"
  bash -n "$payload"
  chmod 0400 "$payload"
  echo "activation_runtime_commit=$RUNTIME_COMMIT"
  echo "activation_payload_blob=$PAYLOAD_BLOB_SHA1"
}

activate_sidecar() {
  local smoke="$1"
  local payload="$work_dir/oracle_universal_video_run_command.sh"
  local output_log="$work_dir/oracle-universal-video-$MODE.log"

  download_pinned_payload "$payload"
  log "Run pinned side-by-side activation (synthetic_smoke=$smoke) at reduced scheduling priority"
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
    "sudo -n env UNIVERSAL_VIDEO_GIT_REF='$RUNTIME_COMMIT' UNIVERSAL_VIDEO_RUN_SMOKE='$smoke' UNIVERSAL_VIDEO_ACTIVATE=1 UNIVERSAL_VIDEO_PREWARM_MODEL=1 nice -n 10 bash -s" \
    < "$payload" 2>&1 | tee "$output_log"

  grep -Fx 'UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS' "$output_log" >/dev/null \
    || die "remote activation completion marker is missing"
  grep -F 'assistant_lab=active' "$output_log" >/dev/null \
    || die "Assistant Lab acceptance marker is missing"
  grep -F 'universal_video_enabled=enabled' "$output_log" >/dev/null \
    || die "Universal Video enabled marker is missing"
  grep -F 'universal_video_active=active' "$output_log" >/dev/null \
    || die "Universal Video active marker is missing"
  grep -F 'DDS3_AFTER_PASS' "$output_log" >/dev/null \
    || die "DDS3 non-regression marker is missing"
  if [[ "$smoke" == '1' ]]; then
    grep -F 'UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS' "$output_log" >/dev/null \
      || die "bounded synthetic smoke marker is missing"
  fi

  remote_read_only_status
  external_dds3_check
  echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_ACTIVATION_PASS
  [[ "$smoke" == '0' ]] || echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_SMOKE_PASS
}

case "$MODE" in
  probe)
    probe_control_path
    ;;
  status)
    log 'Read Universal Video, Assistant Lab and DDS3 status'
    remote_read_only_status
    external_dds3_check
    echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_STATUS_PASS
    ;;
  activate)
    activate_sidecar 0
    ;;
  smoke)
    activate_sidecar 1
    ;;
esac

printf '\nIssue #%s evidence boundary complete for mode=%s.\n' "$ISSUE_NUMBER" "$MODE"
