#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded OCI Cloud Shell launcher for the existing Oracle Universal Video
# sidecar. It has no arbitrary host/user/command inputs and never processes a
# real school video. Modes: probe, status, activate, smoke, bootstrap.

readonly ORACLE_HOST='158.180.47.161'
readonly ORACLE_USER='ubuntu'
readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"
readonly EXPECTED_ED25519_FINGERPRINT='SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc'
readonly RUNTIME_COMMIT='59377de601c1586ae9914a51a340dc72ac2007ce'
readonly PAYLOAD_BLOB_SHA1='bbf4dc5779726fca415f641b90d017a802daaabf'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly PAYLOAD_PATH='ops/oracle_universal_video_run_command.sh'
readonly ISSUE_NUMBER='318'
readonly MIN_AVAILABLE_MEMORY_BYTES=$((4 * 1024 * 1024 * 1024))
readonly MIN_FREE_DISK_BYTES=$((6 * 1024 * 1024 * 1024))

log() { printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: bash cloud_shell_activate_universal_video.sh MODE

MODE must be exactly one of:
  probe      verify pinned host identity, SSH key, sudo, protected services and capacity
  status     read sidecar/Assistant Lab/DDS3 state without changing the host
  activate   install and start the sidecar; no video job and no synthetic smoke
  smoke      repeat idempotent activation and run one bounded 3-second synthetic job
  bootstrap  gated one-step sequence: probe -> activate -> status -> synthetic smoke

The host, user, SSH key path, source commit and remote payload are fixed.
No real video is submitted by this launcher.
USAGE
}

[[ "$#" -eq 1 ]] || { usage >&2; exit 64; }
readonly MODE="$1"
case "$MODE" in
  probe|status|activate|smoke|bootstrap) ;;
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

optional_resident_states() {
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'bash -s' <<'REMOTE_OPTIONAL'
set -Eeuo pipefail
for service in assistant-lab-observer.service assistant-lab-control.service; do
  load="$(sudo -n systemctl show "$service" -p LoadState --value 2>/dev/null || true)"
  active="$(sudo -n systemctl is-active "$service" 2>/dev/null || true)"
  enabled="$(sudo -n systemctl is-enabled "$service" 2>/dev/null || true)"
  [[ -n "$load" ]] || load='not-found'
  [[ -n "$active" ]] || active='unknown'
  [[ -n "$enabled" ]] || enabled='unknown'
  printf '%s|load=%s|active=%s|enabled=%s\n' "$service" "$load" "$active" "$enabled"
done
REMOTE_OPTIONAL
}

host_resource_gate() {
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
    "sudo -n env MIN_AVAILABLE_MEMORY_BYTES='$MIN_AVAILABLE_MEMORY_BYTES' MIN_FREE_DISK_BYTES='$MIN_FREE_DISK_BYTES' python3 -" <<'PY'
import os
import shutil
from pathlib import Path

mem_available_kib = 0
for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
    if line.startswith('MemAvailable:'):
        mem_available_kib = int(line.split()[1])
        break
mem_available = mem_available_kib * 1024
mount = '/opt' if Path('/opt').exists() else '/'
free_disk = shutil.disk_usage(mount).free
cpus = os.cpu_count() or 1
load1 = os.getloadavg()[0]
min_mem = int(os.environ['MIN_AVAILABLE_MEMORY_BYTES'])
min_disk = int(os.environ['MIN_FREE_DISK_BYTES'])
max_load = max(4.0, cpus * 1.5)
print(f'host_capacity_cpus={cpus}')
print(f'host_capacity_load1={load1:.2f}')
print(f'host_capacity_mem_available_bytes={mem_available}')
print(f'host_capacity_disk_free_bytes={free_disk}')
assert mem_available >= min_mem, f'insufficient available memory: {mem_available} < {min_mem}'
assert free_disk >= min_disk, f'insufficient free disk: {free_disk} < {min_disk}'
assert load1 <= max_load, f'host load too high for bootstrap: {load1:.2f} > {max_load:.2f}'
print('ORACLE_UNIVERSAL_VIDEO_RESOURCE_GATE_PASS')
PY
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
  log 'Probe the fixed SSH/sudo control path, protected services and host capacity'
  ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'sudo -n true'
  remote_read_only_status
  optional_resident_states
  host_resource_gate
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
  grep -Fx "source_commit=$RUNTIME_COMMIT" "$output_log" >/dev/null \
    || die "remote source commit does not match the pinned runtime"
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

activate_sidecar_protected() {
  local smoke="$1"
  local before_optional after_optional
  before_optional="$(optional_resident_states)"
  activate_sidecar "$smoke"
  after_optional="$(optional_resident_states)"
  if [[ "$before_optional" != "$after_optional" ]]; then
    printf 'optional_resident_before:\n%s\noptional_resident_after:\n%s\n' "$before_optional" "$after_optional" >&2
    die "optional Assistant Lab resident service state changed"
  fi
  printf '%s\n' "$after_optional"
  echo ORACLE_OPTIONAL_RESIDENT_SERVICES_NONREGRESSION_PASS
}

status_gate() {
  log 'Read Universal Video, Assistant Lab and DDS3 status'
  local status_output
  status_output="$(remote_read_only_status)"
  printf '%s\n' "$status_output"
  grep -F 'assistant_lab=active' <<<"$status_output" >/dev/null \
    || die "Assistant Lab is not active"
  grep -F 'universal_video_enabled=enabled' <<<"$status_output" >/dev/null \
    || die "Universal Video is not enabled"
  grep -F 'universal_video_active=active' <<<"$status_output" >/dev/null \
    || die "Universal Video is not active"
  optional_resident_states
  external_dds3_check
  echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_STATUS_PASS
}

run_synthetic_smoke_only() {
  log 'Run one bounded 3-second synthetic smoke without reinstalling the sidecar'
  local smoke_output
  smoke_output="$(ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'sudo -n nice -n 10 bash -s' <<'REMOTE_SMOKE'
set -Eeuo pipefail
base='/opt/bridge-school/universal-video'
media="$base/media/universal-video-smoke.mp4"
job="$base/spool/inbox/universal-video-smoke.json"
done_file="$base/spool/done/universal-video-smoke.json"
failed_file="$base/spool/failed/universal-video-smoke.json"

[[ "$(systemctl is-active assistant-lab.service)" == active ]]
[[ "$(systemctl is-enabled universal-video.service 2>/dev/null || true)" == enabled ]]
[[ "$(systemctl is-active universal-video.service 2>/dev/null || true)" == active ]]
ready_json="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY_JSON="$ready_json" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
assert x.get('position_solver') == 'ready', x
PY

rm -f "$done_file" "$failed_file" "$job" "$job.tmp"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i color=c=black:s=320x180:d=3 \
  -f lavfi -i sine=frequency=440:duration=3 \
  -shortest -c:v mpeg4 -q:v 10 -pix_fmt yuv420p -c:a aac "$media"
chown universal-video:universal-video "$media"
cat >"$job.tmp" <<EOF
{"job_id":"universal-video-smoke","profile":"transcript_only","project":"infrastructure-smoke","source":{"kind":"local_path","path":"$media"},"metadata":{"synthetic":true},"options":{"max_duration_seconds":10,"chunk_seconds":60}}
EOF
chown universal-video:universal-video "$job.tmp"
mv "$job.tmp" "$job"

deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  if [[ -f "$done_file" ]]; then
    cat "$done_file"
    RESULT_FILE="$done_file" python3 - <<'PY'
import json, os
from pathlib import Path
x=json.loads(Path(os.environ['RESULT_FILE']).read_text(encoding='utf-8'))
assert x.get('status') == 'REVIEW', x
assert x.get('profile') == 'transcript_only', x
assert x.get('project') == 'infrastructure-smoke', x
assert (x.get('metadata') or {}).get('synthetic') is True, x
transcript=x.get('transcript') or {}
assert transcript.get('qc_pass') is False, x
media=x.get('media') or {}
assert 0 < float(media.get('duration_seconds') or 0) <= 10, x
print('UNIVERSAL_VIDEO_SYNTHETIC_RESULT_CONTRACT_PASS')
PY
    echo UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS
    break
  fi
  if [[ -f "$failed_file" ]]; then
    cat "$failed_file" >&2
    exit 1
  fi
  sleep 2
done
[[ -f "$done_file" ]] || { echo 'synthetic smoke timed out' >&2; exit 1; }

[[ "$(systemctl is-active assistant-lab.service)" == active ]]
ready_json="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY_JSON="$ready_json" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
assert x.get('position_solver') == 'ready', x
print('DDS3_AFTER_SYNTHETIC_SMOKE_PASS')
PY
REMOTE_SMOKE
)"
  printf '%s\n' "$smoke_output"
  grep -F 'UNIVERSAL_VIDEO_SYNTHETIC_RESULT_CONTRACT_PASS' <<<"$smoke_output" >/dev/null \
    || die "synthetic result contract marker is missing"
  grep -F 'UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS' <<<"$smoke_output" >/dev/null \
    || die "bounded synthetic smoke marker is missing"
  grep -F 'DDS3_AFTER_SYNTHETIC_SMOKE_PASS' <<<"$smoke_output" >/dev/null \
    || die "DDS3 post-smoke marker is missing"
  remote_read_only_status
  external_dds3_check
  echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_SMOKE_PASS
}

run_synthetic_smoke_protected() {
  local before_optional after_optional
  before_optional="$(optional_resident_states)"
  run_synthetic_smoke_only
  after_optional="$(optional_resident_states)"
  if [[ "$before_optional" != "$after_optional" ]]; then
    printf 'optional_resident_before:\n%s\noptional_resident_after:\n%s\n' "$before_optional" "$after_optional" >&2
    die "optional Assistant Lab resident service state changed during smoke"
  fi
  printf '%s\n' "$after_optional"
  echo ORACLE_OPTIONAL_RESIDENT_SERVICES_POST_SMOKE_PASS
}

case "$MODE" in
  probe)
    probe_control_path
    ;;
  status)
    status_gate
    ;;
  activate)
    host_resource_gate
    activate_sidecar_protected 0
    ;;
  smoke)
    host_resource_gate
    activate_sidecar_protected 1
    ;;
  bootstrap)
    log 'Begin fail-closed one-step Universal Video bootstrap'
    probe_control_path
    activate_sidecar_protected 0
    status_gate
    run_synthetic_smoke_protected
    echo ORACLE_UNIVERSAL_VIDEO_CLOUD_SHELL_BOOTSTRAP_PASS
    ;;
esac

printf '\nIssue #%s evidence boundary complete for mode=%s.\n' "$ISSUE_NUMBER" "$MODE"
