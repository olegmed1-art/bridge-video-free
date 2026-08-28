#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-time root bootstrap for bounded Universal Video OCI entrypoints.
# ocarun receives passwordless sudo only for exact root-owned commands.
# No shell and no NOPASSWD:ALL are granted.

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'SOURCE_COMMIT must be a 40-hex commit'
command -v curl >/dev/null || fail 'curl is required'
command -v visudo >/dev/null || fail 'visudo is required'
id ocarun >/dev/null 2>&1 || fail 'ocarun user does not exist'

readonly BASE="https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${SOURCE_COMMIT}/ops"
readonly TARGET='/usr/local/sbin/universal-video-oci-admin'
readonly REPAIR_TARGET='/usr/local/sbin/universal-video-spool-repair'
# Keep bounded admin grants separate from the generic submit/status grants.
# Both installers may be re-run without deleting the other's command surface.
readonly SUDOERS='/etc/sudoers.d/universal-video-admin-ocarun'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM
curl -fsSL --retry 3 --retry-delay 2 "$BASE/universal_video_oci_admin_entrypoint.sh" -o "$tmp/admin"
curl -fsSL --retry 3 --retry-delay 2 "$BASE/universal_video_spool_repair.sh" -o "$tmp/repair"
bash -n "$tmp/admin"
bash -n "$tmp/repair"
grep -Fq "usage: universal-video-oci-admin audit|productionize" "$tmp/admin" || fail 'unexpected Universal Video entrypoint contract'
grep -Fq "readonly UV_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'" "$tmp/admin" || fail 'unexpected Universal Video runtime pin'
grep -Fq "UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS" "$tmp/admin" || fail 'no-ASR productionization gate missing'
grep -Fq 'UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS' "$tmp/repair" || fail 'spool repair marker missing'
! grep -Eq '(^|[[:space:]])(bash|sh)[[:space:]]+-c[[:space:]]+"?\$' "$tmp/admin" || fail 'dynamic shell execution pattern detected'

install -o root -g root -m 0755 "$tmp/admin" "$TARGET"
install -o root -g root -m 0755 "$tmp/repair" "$REPAIR_TARGET"
cat > "$tmp/sudoers" <<'EOF'
# Bounded OCI Run Command privilege for Universal Video only.
# No shell, editor, package manager, arbitrary systemctl/path, or NOPASSWD:ALL.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin audit
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin productionize
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-spool-repair
EOF
chmod 0440 "$tmp/sudoers"
visudo -cf "$tmp/sudoers" >/dev/null
install -o root -g root -m 0440 "$tmp/sudoers" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null

if grep -Ev '^[[:space:]]*(#|$)' "$SUDOERS" | grep -Eq 'NOPASSWD:[[:space:]]*ALL'; then fail 'broad NOPASSWD grant detected'; fi
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail 'unexpected entrypoint ownership/mode'
[[ "$(stat -c '%U:%G:%a' "$REPAIR_TARGET")" == 'root:root:755' ]] || fail 'unexpected repair helper ownership/mode'
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'unexpected sudoers ownership/mode'

printf 'installed=%s\n' "$TARGET"
printf 'repair=%s\n' "$REPAIR_TARGET"
printf 'sudoers=%s\n' "$SUDOERS"
printf 'source_commit=%s\n' "$SOURCE_COMMIT"
echo UNIVERSAL_VIDEO_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS

sudo -u ocarun sudo -n "$TARGET" audit
echo UNIVERSAL_VIDEO_OCARUN_POST_BOOTSTRAP_AUDIT_PASS
