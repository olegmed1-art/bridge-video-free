#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-time root bootstrap for the bounded Universal Video OCI admin entrypoint.
# It grants ocarun passwordless sudo only for two exact root-owned invocations:
# audit and productionize. It never grants a shell or NOPASSWD:ALL.

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT must be a 40-hex commit'
command -v curl >/dev/null || fail 'curl is required'
command -v visudo >/dev/null || fail 'visudo is required'
id ocarun >/dev/null 2>&1 || fail 'ocarun user does not exist'

readonly RAW="https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${SOURCE_COMMIT}/ops/universal_video_oci_admin_entrypoint.sh"
readonly TARGET='/usr/local/sbin/universal-video-oci-admin'
readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'

tmp="$(mktemp)"
trap 'rm -f "$tmp" "$tmp.sudoers"' EXIT INT TERM
curl -fsSL --retry 3 --retry-delay 2 "$RAW" -o "$tmp"
bash -n "$tmp"
grep -Fq "usage: universal-video-oci-admin audit|productionize" "$tmp" || fail 'unexpected Universal Video entrypoint contract'
grep -Fq "readonly UV_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'" "$tmp" || fail 'unexpected Universal Video runtime pin'
grep -Fq "UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS" "$tmp" || fail 'no-ASR productionization gate missing'
! grep -Eq '(^|[[:space:]])(bash|sh)[[:space:]]+-c[[:space:]]+"?\$' "$tmp" || fail 'dynamic shell execution pattern detected'

install -o root -g root -m 0755 "$tmp" "$TARGET"
cat > "$tmp.sudoers" <<'EOF'
# Bounded OCI Run Command privilege for Universal Video productionization only.
# No shell, editor, package manager, arbitrary systemctl, arbitrary path, or NOPASSWD:ALL.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin audit
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin productionize
EOF
chmod 0440 "$tmp.sudoers"
visudo -cf "$tmp.sudoers" >/dev/null
install -o root -g root -m 0440 "$tmp.sudoers" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null

! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad NOPASSWD grant detected'
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail 'unexpected entrypoint ownership/mode'
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'unexpected sudoers ownership/mode'

printf 'installed=%s\n' "$TARGET"
printf 'sudoers=%s\n' "$SUDOERS"
printf 'source_commit=%s\n' "$SOURCE_COMMIT"
echo UNIVERSAL_VIDEO_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS

# Non-mutating post-install proof through the exact ocarun path.
sudo -u ocarun sudo -n "$TARGET" audit
echo UNIVERSAL_VIDEO_OCARUN_POST_BOOTSTRAP_AUDIT_PASS
