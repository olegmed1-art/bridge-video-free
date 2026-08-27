#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-time OCI Cloud Shell bootstrap for the two separate bounded ocarun admin
# entrypoints (Assistant Lab and Universal Video). No arbitrary host/user/command.
# The installer source is pinned to the reviewed #382 merge commit.

readonly ORACLE_HOST='158.180.47.161'
readonly ORACLE_USER='ubuntu'
readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly BOOTSTRAP_COMMIT='dffccf87d69f72cd401559018aea512f5de36b64'
readonly ASSISTANT_INSTALLER='ops/install_assistant_lab_ocarun_admin.sh'
readonly VIDEO_INSTALLER='ops/install_universal_video_ocarun_admin.sh'

fail(){ echo "ERROR: $*" >&2; exit 1; }
for c in bash curl ssh ssh-keygen ssh-keyscan stat; do command -v "$c" >/dev/null 2>&1 || fail "$c is required"; done

[[ -f "$SSH_KEY_PATH" && ! -L "$SSH_KEY_PATH" ]] || fail 'fixed Oracle private key is missing or unsafe'
key_mode="$(stat -c '%a' "$SSH_KEY_PATH")"
[[ "$key_mode" =~ ^[0-7]{3,4}$ ]] || fail 'unexpected private-key mode'
(( (8#$key_mode & 077) == 0 )) || fail 'private key must not be accessible by group or others'
ssh-keygen -y -f "$SSH_KEY_PATH" >/dev/null 2>&1 || fail 'private key cannot be parsed'

work="$(mktemp -d -t bounded-oci-admin.XXXXXX)"
trap 'rm -rf "$work"' EXIT INT TERM
known="$work/known_hosts"
: > "$known"
for attempt in 1 2 3; do
  ssh-keyscan -T 10 -t ed25519,ecdsa,rsa "$ORACLE_HOST" > "$known.tmp" 2>/dev/null || true
  if [[ -s "$known.tmp" ]]; then sort -u "$known.tmp" > "$known"; break; fi
  sleep $((attempt * 2))
done
[[ -s "$known" ]] || fail 'could not collect Oracle SSH host keys'
actual="$work/actual-fingerprints"
expected="$work/expected-fingerprints"
ssh-keygen -lf "$known" | awk '{print $2}' | sort -u > "$actual"
cat > "$expected" <<'EOF'
SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk
SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc
SHA256:eRCJ8c4V7HCBlIoNVSlpPSWZE5xPUMjBD6f0PvHDj64
EOF
sort -u -o "$expected" "$expected"
cmp -s "$actual" "$expected" || fail 'Oracle SSH host-key set mismatch'
echo ORACLE_BOUNDED_ADMIN_HOST_IDENTITY_PASS

readonly -a SSH_OPTIONS=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$known"
  -o ConnectTimeout=15
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" 'sudo -n true'
echo ORACLE_BOUNDED_ADMIN_SSH_SUDO_PASS

fetch_installer(){
  local path="$1" destination="$2" marker="$3"
  curl -fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/$REPOSITORY/$BOOTSTRAP_COMMIT/$path" -o "$destination"
  bash -n "$destination"
  grep -Fq "$marker" "$destination" || fail "unexpected installer contract: $path"
  chmod 0400 "$destination"
}

assistant_installer="$work/assistant-install.sh"
video_installer="$work/video-install.sh"
fetch_installer "$ASSISTANT_INSTALLER" "$assistant_installer" 'ASSISTANT_LAB_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS'
fetch_installer "$VIDEO_INSTALLER" "$video_installer" 'UNIVERSAL_VIDEO_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS'

echo "bootstrap_commit=$BOOTSTRAP_COMMIT"
echo ORACLE_BOUNDED_ADMIN_PIN_PASS

echo 'Installing Assistant Lab bounded OCI admin surface'
ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
  "sudo -n env SOURCE_COMMIT='$BOOTSTRAP_COMMIT' bash -s" < "$assistant_installer"

echo 'Installing Universal Video bounded OCI admin surface'
ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
  "sudo -n env SOURCE_COMMIT='$BOOTSTRAP_COMMIT' bash -s" < "$video_installer"

# Final exact-path audits. No general sudo check is granted to ocarun.
ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
  'sudo -u ocarun sudo -n /usr/local/sbin/assistant-lab-oci-admin audit'
ssh "${SSH_OPTIONS[@]}" "$ORACLE_USER@$ORACLE_HOST" \
  'sudo -u ocarun sudo -n /usr/local/sbin/universal-video-oci-admin audit'

echo ORACLE_BOUNDED_OCARUN_ADMIN_BOOTSTRAP_PASS
