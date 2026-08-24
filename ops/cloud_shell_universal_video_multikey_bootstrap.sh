#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility wrapper for the audited Universal Video Cloud Shell launcher.
# The original launcher pinned only the ED25519 host key. OpenSSH on OCI Cloud
# Shell may negotiate the already-trusted ECDSA key instead. This wrapper
# verifies the exact audited launcher blob, replaces only its host-key gate with
# exact-set verification of all three previously recorded Oracle host keys, and
# then executes the same bounded launcher.

readonly SOURCE_CONTROL_COMMIT='775dd6a88ede5672c3df5f42589e71a16146e2f4'
readonly SOURCE_LAUNCHER_BLOB='96f5d0245c85865f20de715d783034a369912623'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly SOURCE_PATH='ops/cloud_shell_activate_universal_video.sh'

usage(){
  echo 'Usage: bash cloud_shell_universal_video_multikey_bootstrap.sh [probe|status|activate|smoke|bootstrap]' >&2
}
[[ "$#" -le 1 ]] || { usage; exit 64; }
MODE="${1:-bootstrap}"
case "$MODE" in probe|status|activate|smoke|bootstrap) ;; *) usage; exit 64 ;; esac

for c in curl git python3 bash; do command -v "$c" >/dev/null 2>&1 || { echo "$c is required" >&2; exit 1; }; done

work="$(mktemp -d -t universal-video-multikey.XXXXXX)"
trap 'rm -rf "$work"' EXIT INT TERM
source_file="$work/source.sh"
patched_file="$work/launcher.sh"

curl -fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_CONTROL_COMMIT/$SOURCE_PATH" \
  -o "$source_file"
[[ "$(git hash-object "$source_file")" == "$SOURCE_LAUNCHER_BLOB" ]] || {
  echo 'audited source launcher blob mismatch' >&2
  exit 1
}

python3 - "$source_file" "$patched_file" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1]).read_text(encoding='utf-8')
old = '''known_hosts="$work_dir/known_hosts"
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
[[ "$actual_fingerprint" == "$EXPECTED_ED25519_FINGERPRINT" ]] \\
  || die "Oracle SSH host fingerprint mismatch: expected $EXPECTED_ED25519_FINGERPRINT, got ${actual_fingerprint:-none}"
echo "oracle_host_fingerprint=$actual_fingerprint"
'''
new = '''known_hosts="$work_dir/known_hosts"
: > "$known_hosts"
for attempt in 1 2 3; do
  ssh-keyscan -T 10 -t ed25519,ecdsa,rsa "$ORACLE_HOST" > "$known_hosts.tmp" 2>/dev/null || true
  if [[ -s "$known_hosts.tmp" ]]; then
    sort -u "$known_hosts.tmp" > "$known_hosts"
    break
  fi
  sleep $((attempt * 2))
done
[[ -s "$known_hosts" ]] || die "could not collect the Oracle SSH host keys"
actual_fingerprints="$work_dir/actual-fingerprints"
expected_fingerprints="$work_dir/expected-fingerprints"
ssh-keygen -lf "$known_hosts" | awk '{print $2}' | sort -u > "$actual_fingerprints"
cat > "$expected_fingerprints" <<'EOF_FPS'
SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk
SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc
SHA256:eRCJ8c4V7HCBlIoNVSlpPSWZE5xPUMjBD6f0PvHDj64
EOF_FPS
sort -u -o "$expected_fingerprints" "$expected_fingerprints"
if ! cmp -s "$actual_fingerprints" "$expected_fingerprints"; then
  echo 'Oracle SSH host-key set mismatch' >&2
  echo 'expected fingerprints:' >&2
  cat "$expected_fingerprints" >&2
  echo 'observed fingerprints:' >&2
  cat "$actual_fingerprints" >&2
  exit 1
fi
printf 'oracle_host_fingerprints='; paste -sd, "$actual_fingerprints"
echo ORACLE_UNIVERSAL_VIDEO_MULTIKEY_HOST_IDENTITY_PASS
'''
if src.count(old) != 1:
    raise SystemExit('audited host-key block not found exactly once')
patched = src.replace(old, new)
Path(sys.argv[2]).write_text(patched, encoding='utf-8')
PY

bash -n "$patched_file"
export USER="${USER:-$(id -un)}"
env USER="$USER" bash "$patched_file" "$MODE"
