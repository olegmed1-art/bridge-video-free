#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root bootstrap for bounded OCI Run Command administration.
# Usage (on the Oracle VM as a sudo-capable administrator):
#   sudo env SOURCE_COMMIT=<40-hex-commit> bash install_assistant_lab_ocarun_admin.sh

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT must be a 40-hex commit'
command -v curl >/dev/null || fail 'curl is required'
command -v visudo >/dev/null || fail 'visudo is required'
id ocarun >/dev/null 2>&1 || fail 'ocarun user does not exist'

readonly RAW="https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${SOURCE_COMMIT}/ops/assistant_lab_oci_admin_entrypoint.sh"
readonly TARGET='/usr/local/sbin/assistant-lab-oci-admin'
readonly SUDOERS='/etc/sudoers.d/assistant-lab-ocarun'

tmp="$(mktemp)"
trap 'rm -f "$tmp" "$tmp.sudoers"' EXIT
curl -fsSL "$RAW" -o "$tmp"
bash -n "$tmp"
grep -Fq "usage: assistant-lab-oci-admin audit|restart-bridge|activate-stack" "$tmp" || fail 'unexpected entrypoint contract'

install -o root -g root -m 0755 "$tmp" "$TARGET"
cat > "$tmp.sudoers" <<'EOF'
# Bounded OCI Run Command privilege for Assistant Lab only.
# No shell, editor, package manager, arbitrary systemctl, or NOPASSWD:ALL access.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/assistant-lab-oci-admin audit
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/assistant-lab-oci-admin restart-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/assistant-lab-oci-admin activate-stack
EOF
chmod 0440 "$tmp.sudoers"
visudo -cf "$tmp.sudoers" >/dev/null
install -o root -g root -m 0440 "$tmp.sudoers" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null

# Prove there is no broad passwordless sudo grant in this file.
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad NOPASSWD grant detected'

printf 'installed=%s\n' "$TARGET"
printf 'sudoers=%s\n' "$SUDOERS"
printf 'source_commit=%s\n' "$SOURCE_COMMIT"
echo ASSISTANT_LAB_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS

# Non-mutating post-install proof through the exact ocarun path.
if sudo -u ocarun sudo -n "$TARGET" audit; then
  echo ASSISTANT_LAB_OCARUN_POST_BOOTSTRAP_AUDIT_PASS
else
  rc=$?
  echo "ASSISTANT_LAB_OCARUN_POST_BOOTSTRAP_AUDIT_FAIL rc=$rc" >&2
  echo 'Bootstrap itself is installed; use OCI audit output to determine the minimal repair.' >&2
  exit "$rc"
fi
